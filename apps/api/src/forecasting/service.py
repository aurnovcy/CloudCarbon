"""
CloudCarbon ForecastingService.

Implements Holt-Winters exponential smoothing (statsmodels) for four metrics:
  - cost_usd
  - total_co2e_kg
  - water_litres
  - water_stress_adjusted_litres

Each forecast uses:
  - Additive trend
  - Multiplicative seasonality with period=7 (weekly pattern)
  - 14-day holdout for MAE calculation
  - 80% confidence intervals via simulation

Results are cached in Redis with a 6-hour TTL.
"""
from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta, date
from typing import Optional
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = {
    "cost_usd",
    "total_co2e_kg",
    "water_litres",
    "water_stress_adjusted_litres",
}

_REDIS_TTL_SECONDS = 6 * 3600  # 6 hours
_MIN_DATA_POINTS = 14           # minimum required for forecasting
_HOLDOUT_DAYS = 14              # days withheld for MAE calculation


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ForecastResult:
    metric: str
    dates: list[str]          # ISO date strings
    predicted: list[float]
    lower_80: list[float]
    upper_80: list[float]
    model_mae: float
    data_points_used: int
    generated_at: str         # ISO datetime


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

_METRIC_SQL_MAP = {
    "cost_usd": "COALESCE(SUM(fr.effective_cost), 0)",
    "total_co2e_kg": "COALESCE(SUM(er.total_co2e_kg), 0)",
    "water_litres": "COALESCE(SUM(er.water_litres), 0)",
    "water_stress_adjusted_litres": "COALESCE(SUM(er.water_stress_adjusted_litres), 0)",
}


async def _load_daily_series(
    tenant_id: UUID,
    metric: str,
    db: AsyncSession,
    lookback_days: int = 90,
) -> tuple[list[date], list[float]]:
    """
    Load daily aggregated values for a metric from enriched_records.

    Returns:
        Tuple of (dates, values) sorted by date ascending.
    """
    agg_expr = _METRIC_SQL_MAP[metric]
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    sql = text(f"""
        SELECT
            DATE(fr.charge_period_start) AS day,
            {agg_expr} AS value
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND fr.charge_period_start >= :cutoff
        GROUP BY DATE(fr.charge_period_start)
        ORDER BY day ASC
    """)
    result = await db.execute(sql, {"tenant_id": str(tenant_id), "cutoff": cutoff})
    rows = result.fetchall()

    if not rows:
        return [], []

    dates = [row[0] for row in rows]
    values = [float(row[1]) for row in rows]
    return dates, values


# ---------------------------------------------------------------------------
# Holt-Winters model
# ---------------------------------------------------------------------------

def _fit_and_forecast(
    values: list[float],
    horizon_days: int,
    seasonal_periods: int = 7,
) -> tuple[list[float], list[float], list[float], float]:
    """
    Fit a Holt-Winters model and return (predicted, lower_80, upper_80, mae).

    Uses additive trend and multiplicative seasonality.
    Falls back to additive seasonality if multiplicative fails (e.g. zeros in series).
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    arr = np.array(values, dtype=float)

    # Holdout the last _HOLDOUT_DAYS for MAE
    if len(arr) > _HOLDOUT_DAYS:
        train = arr[:-_HOLDOUT_DAYS]
        holdout = arr[-_HOLDOUT_DAYS:]
    else:
        train = arr
        holdout = arr

    # Determine seasonality type: multiplicative requires all positive values
    has_zeros = np.any(train <= 0)
    seasonal_type = "add" if has_zeros else "mul"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ExponentialSmoothing(
                train,
                trend="add",
                seasonal=seasonal_type,
                seasonal_periods=min(seasonal_periods, len(train) // 2),
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True, use_brute=False)
        except Exception:
            # Final fallback: simple additive with no seasonality
            model = ExponentialSmoothing(
                train,
                trend="add",
                seasonal=None,
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True)

    # Forecast
    forecast = fit.forecast(horizon_days)
    predicted = [max(0.0, float(v)) for v in forecast]

    # Confidence intervals via simulation (1000 paths, 80% CI)
    try:
        sim = fit.simulate(horizon_days, repetitions=1000, error="add")
        sim = np.maximum(sim, 0)
        lower_80 = [float(np.percentile(sim[i], 10)) for i in range(horizon_days)]
        upper_80 = [float(np.percentile(sim[i], 90)) for i in range(horizon_days)]
    except Exception:
        # Fallback: ±20% of predicted
        lower_80 = [max(0.0, p * 0.8) for p in predicted]
        upper_80 = [p * 1.2 for p in predicted]

    # MAE on holdout
    if len(holdout) > 0 and len(arr) > _HOLDOUT_DAYS:
        holdout_forecast = fit.forecast(len(holdout))
        mae = float(np.mean(np.abs(holdout - holdout_forecast)))
    else:
        mae = 0.0

    return predicted, lower_80, upper_80, mae


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def forecast_metric(
    tenant_id: UUID,
    metric: str,
    horizon_days: int,
    db: AsyncSession,
    redis_client=None,
) -> ForecastResult:
    """
    Forecast a single metric for horizon_days ahead.

    Args:
        tenant_id: Tenant UUID.
        metric: One of cost_usd, total_co2e_kg, water_litres, water_stress_adjusted_litres.
        horizon_days: Number of days to forecast.
        db: AsyncSession.
        redis_client: Optional Redis client for caching.

    Returns:
        ForecastResult with dates, predicted values, confidence intervals, and MAE.
    """
    if metric not in _SUPPORTED_METRICS:
        raise ValueError(f"Unsupported metric '{metric}'. Valid: {sorted(_SUPPORTED_METRICS)}")

    # Check Redis cache
    cache_key = f"forecast:{tenant_id}:{metric}:{horizon_days}"
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                return ForecastResult(**data)
        except Exception as exc:
            logger.debug("Redis cache miss for %s: %s", cache_key, exc)

    # Load historical data
    dates, values = await _load_daily_series(tenant_id, metric, db)

    if len(values) < _MIN_DATA_POINTS:
        logger.warning(
            "Insufficient data for %s forecast (tenant %s): %d points",
            metric, tenant_id, len(values),
        )
        # Return flat forecast based on available mean
        mean_val = float(np.mean(values)) if values else 0.0
        today = datetime.now(timezone.utc).date()
        forecast_dates = [
            (today + timedelta(days=i + 1)).isoformat()
            for i in range(horizon_days)
        ]
        result = ForecastResult(
            metric=metric,
            dates=forecast_dates,
            predicted=[mean_val] * horizon_days,
            lower_80=[mean_val * 0.8] * horizon_days,
            upper_80=[mean_val * 1.2] * horizon_days,
            model_mae=0.0,
            data_points_used=len(values),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    else:
        predicted, lower_80, upper_80, mae = _fit_and_forecast(values, horizon_days)

        last_date = dates[-1]
        if isinstance(last_date, str):
            last_date = date.fromisoformat(last_date)

        forecast_dates = [
            (last_date + timedelta(days=i + 1)).isoformat()
            for i in range(horizon_days)
        ]

        result = ForecastResult(
            metric=metric,
            dates=forecast_dates,
            predicted=predicted,
            lower_80=lower_80,
            upper_80=upper_80,
            model_mae=mae,
            data_points_used=len(values),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # Cache result
    if redis_client:
        try:
            await redis_client.setex(cache_key, _REDIS_TTL_SECONDS, json.dumps(asdict(result)))
        except Exception as exc:
            logger.debug("Redis cache write failed for %s: %s", cache_key, exc)

    return result


async def forecast_all_metrics(
    tenant_id: UUID,
    db: AsyncSession,
    redis_client=None,
    horizon_days: int = 90,
) -> dict[str, ForecastResult]:
    """
    Forecast all four metrics with horizon_days=90 and cache results.

    Returns:
        Dict mapping metric name to ForecastResult.
    """
    results: dict[str, ForecastResult] = {}
    for metric in sorted(_SUPPORTED_METRICS):
        try:
            results[metric] = await forecast_metric(
                tenant_id, metric, horizon_days, db, redis_client
            )
        except Exception as exc:
            logger.error("Forecast failed for metric %s: %s", metric, exc)

    return results
