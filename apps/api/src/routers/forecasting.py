"""
Forecasting API router (synchronous).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.database import get_db
from src.dependencies.auth import require_role
from src.forecasting.service import forecast_metric, _SUPPORTED_METRICS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/forecast")
def get_forecast(
    metrics: str = Query("cost_usd,total_co2e_kg"),
    horizon_days: int = Query(90, ge=7, le=365),
    current_user=Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    requested = [m.strip() for m in metrics.split(",") if m.strip()]
    invalid = [m for m in requested if m not in _SUPPORTED_METRICS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Invalid metrics: {invalid}. Valid: {sorted(_SUPPORTED_METRICS)}")

    results = {}
    for metric in requested:
        try:
            result = forecast_metric(tenant_id=tenant_id, metric=metric, horizon_days=horizon_days, db=db)
            results[metric] = asdict(result)
        except Exception as exc:
            logger.error("Forecast error for metric %s: %s", metric, exc)
            raise HTTPException(status_code=500, detail=f"Forecast failed for metric '{metric}': {exc}")

    return results
