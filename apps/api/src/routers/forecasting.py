"""
Forecasting API router.

Endpoints:
  GET /reports/forecast — Holt-Winters forecast for one or more metrics
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import require_role
from src.forecasting.service import forecast_metric, _SUPPORTED_METRICS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/forecast")
async def get_forecast(
    metrics: str = Query(
        "cost_usd,total_co2e_kg",
        description="Comma-separated list of metrics to forecast",
    ),
    horizon_days: int = Query(90, ge=7, le=365, description="Forecast horizon in days"),
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return Holt-Winters forecasts for the requested metrics.

    **Supported metrics:** cost_usd, total_co2e_kg, water_litres, water_stress_adjusted_litres

    Each result includes:
    - `dates`: list of ISO date strings for the forecast horizon
    - `predicted`: point forecast values
    - `lower_80` / `upper_80`: 80% confidence interval bounds
    - `model_mae`: mean absolute error on 14-day holdout
    - `data_points_used`: number of historical data points used
    """
    tenant_id = current_user.tenant_id

    requested = [m.strip() for m in metrics.split(",") if m.strip()]
    invalid = [m for m in requested if m not in _SUPPORTED_METRICS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid metrics: {invalid}. Valid: {sorted(_SUPPORTED_METRICS)}",
        )

    results = {}
    for metric in requested:
        try:
            result = await forecast_metric(
                tenant_id=tenant_id,
                metric=metric,
                horizon_days=horizon_days,
                db=db,
            )
            results[metric] = asdict(result)
        except Exception as exc:
            logger.error("Forecast error for metric %s: %s", metric, exc)
            raise HTTPException(
                status_code=500,
                detail=f"Forecast failed for metric '{metric}': {exc}",
            )

    return results
