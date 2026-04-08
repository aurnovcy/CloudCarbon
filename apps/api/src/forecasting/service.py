"""
CloudCarbon ForecastingService (synchronous).
Implements Holt-Winters exponential smoothing for four metrics.
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
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = {"cost_usd", "total_co2e_kg", "water_litres", "water_stress_adjusted_litres"}
_MIN_DATA_POINTS = 14
_HOLDOUT_DAYS = 14


@dataclass
class ForecastResult:
    metric: str
    dates: list[str]
    predicted: list[float]
    lower_80: list[float]
    upper_80: list[float]
    model_mae: float
    data_points_used: int
    generated_at: str


_METRIC_SQL_MAP = {
    "cost_usd": "COALESCE(SUM(fr.effective_cost), 0)",
    "total_co2e_kg": "COALESCE(SUM(er.total_co2e_kg), 0)",
    "water_litres": "COALESCE(SUM(er.water_litres), 0)",
    "water_stress_adjusted_litres": "COALESCE(SUM(er.water_stress_adjusted_litres), 0)",
}


def _load_daily_series(
    tenant_id: UUID, metric: str, db: Session, lookback_days: int = 90,
) -> tuple[list[date], list[float]]:
    agg_expr = _METRIC_SQL_MAP[metric]
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    sql = text(f"""
        SELECT DATE(fr.charge_period_start) AS day, {agg_expr} AS value
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id AND fr.charge_period_start >= :cutoff
        GROUP BY DATE(fr.charge_period_start) ORDER BY day ASC
    """)
    result = db.execute(sql, {"tenant_id": str(tenant_id), "cutoff": cutoff})
    rows = result.fetchall()
    if not rows:
        return [], []
    return [row[0] for row in rows], [float(row[1]) for row in rows]


def _fit_and_forecast(values: list[float], horizon_days: int, seasonal_periods: int = 7) -> tuple[list[float], list[float], list[float], float]:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    arr = np.array(values, dtype=float)
    if len(arr) > _HOLDOUT_DAYS:
        train = arr[:-_HOLDOUT_DAYS]
        holdout = arr[-_HOLDOUT_DAYS:]
    else:
        train = arr
        holdout = arr

    has_zeros = np.any(train <= 0)
    seasonal_type = "add" if has_zeros else "mul"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ExponentialSmoothing(
                train, trend="add", seasonal=seasonal_type,
                seasonal_periods=min(seasonal_periods, len(train) // 2),
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True, use_brute=False)
        except Exception:
            model = ExponentialSmoothing(train, trend="add", seasonal=None, initialization_method="estimated")
            fit = model.fit(optimized=True)

    forecast = fit.forecast(horizon_days)
    predicted = [max(0.0, float(v)) for v in forecast]

    try:
        sim = fit.simulate(horizon_days, repetitions=1000, error="add")
        sim = np.maximum(sim, 0)
        lower_80 = [float(np.percentile(sim[i], 10)) for i in range(horizon_days)]
        upper_80 = [float(np.percentile(sim[i], 90)) for i in range(horizon_days)]
    except Exception:
        lower_80 = [max(0.0, p * 0.8) for p in predicted]
        upper_80 = [p * 1.2 for p in predicted]

    mae = 0.0
    if len(holdout) > 0 and len(arr) > _HOLDOUT_DAYS:
        holdout_forecast = fit.forecast(len(holdout))
        mae = float(np.mean(np.abs(holdout - holdout_forecast)))

    return predicted, lower_80, upper_80, mae


def forecast_metric(
    tenant_id: UUID, metric: str, horizon_days: int, db: Session, redis_client=None,
) -> ForecastResult:
    if metric not in _SUPPORTED_METRICS:
        raise ValueError(f"Unsupported metric '{metric}'")

    cache_key = f"forecast:{tenant_id}:{metric}:{horizon_days}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return ForecastResult(**json.loads(cached))
        except Exception:
            pass

    dates, values = _load_daily_series(tenant_id, metric, db)

    if len(values) < _MIN_DATA_POINTS:
        mean_val = float(np.mean(values)) if values else 0.0
        today = datetime.now(timezone.utc).date()
        forecast_dates = [(today + timedelta(days=i + 1)).isoformat() for i in range(horizon_days)]
        result = ForecastResult(
            metric=metric, dates=forecast_dates,
            predicted=[mean_val] * horizon_days, lower_80=[mean_val * 0.8] * horizon_days,
            upper_80=[mean_val * 1.2] * horizon_days, model_mae=0.0,
            data_points_used=len(values), generated_at=datetime.now(timezone.utc).isoformat(),
        )
    else:
        predicted, lower_80, upper_80, mae = _fit_and_forecast(values, horizon_days)
        last_date = dates[-1]
        if isinstance(last_date, str):
            last_date = date.fromisoformat(last_date)
        forecast_dates = [(last_date + timedelta(days=i + 1)).isoformat() for i in range(horizon_days)]
        result = ForecastResult(
            metric=metric, dates=forecast_dates, predicted=predicted, lower_80=lower_80,
            upper_80=upper_80, model_mae=mae, data_points_used=len(values),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    if redis_client:
        try:
            redis_client.setex(cache_key, 6 * 3600, json.dumps(asdict(result)))
        except Exception:
            pass

    return result
