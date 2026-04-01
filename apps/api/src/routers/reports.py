"""
Reports API router.

Endpoints:
  GET /reports/overview  — multi-dimensional summary with MoM change
  GET /reports/carbon    — GHG time series, regional breakdown, Scope 3 detail
  GET /reports/water     — water consumption by region and service category
  GET /reports/forecast  — Holt-Winters forecast (delegated to forecasting router)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(d: Optional[str], default: date) -> date:
    if d is None:
        return default
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date format: {d}. Use YYYY-MM-DD.")


def _mom_pct(current: float, previous: float) -> float:
    """Month-over-month percentage change."""
    if previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 2)


# ---------------------------------------------------------------------------
# GET /reports/overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def get_overview(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    mode: str = Query("cost", pattern="^(cost|sustainability)$"),
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a multi-dimensional overview of cost, carbon, and water metrics.

    Includes month-over-month percentage changes and breakdowns by provider
    and service category.
    """
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, today.replace(day=1))

    # Previous period (same duration, shifted back)
    duration = (end - start).days or 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration)

    provider_filter = "AND LOWER(fr.provider_name) = LOWER(:provider)" if provider else ""
    params: dict = {
        "tenant_id": tenant_id,
        "start": start,
        "end": end,
        "prev_start": prev_start,
        "prev_end": prev_end,
    }
    if provider:
        params["provider"] = provider

    # Current period totals
    totals_sql = text(f"""
        SELECT
            COALESCE(SUM(fr.effective_cost), 0)                   AS cost_usd,
            COALESCE(SUM(er.total_co2e_kg), 0)                    AS co2e_kg,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)             AS scope3_co2e_kg,
            COALESCE(SUM(er.water_litres), 0)                     AS water_litres,
            COALESCE(SUM(er.water_stress_adjusted_litres), 0)     AS water_stress_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    cur = (await db.execute(totals_sql, params)).fetchone()
    cost_cur = float(cur[0] or 0)
    co2e_cur = float(cur[1] or 0)
    scope3_cur = float(cur[2] or 0)
    water_cur = float(cur[3] or 0)
    water_stress_cur = float(cur[4] or 0)

    # Previous period totals
    prev_params = {**params, "start": prev_start, "end": prev_end}
    prev_sql = text(f"""
        SELECT
            COALESCE(SUM(fr.effective_cost), 0),
            COALESCE(SUM(er.total_co2e_kg), 0),
            COALESCE(SUM(er.water_litres), 0)
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    prev = (await db.execute(prev_sql, prev_params)).fetchone()
    cost_prev = float(prev[0] or 0)
    co2e_prev = float(prev[1] or 0)
    water_prev = float(prev[2] or 0)

    # Carbon efficiency: kg CO2e per $1000 spend
    carbon_efficiency = (co2e_cur / cost_cur * 1000) if cost_cur > 0 else 0.0
    scope3_pct = (scope3_cur / co2e_cur * 100) if co2e_cur > 0 else 0.0

    # By provider
    by_prov_sql = text(f"""
        SELECT fr.provider_name,
               COALESCE(SUM(fr.effective_cost), 0)              AS cost_usd,
               COALESCE(SUM(er.total_co2e_kg), 0)               AS co2e_kg,
               COALESCE(SUM(er.water_litres), 0)                AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.provider_name
        ORDER BY cost_usd DESC
    """)
    by_prov = [
        {
            "provider": r[0],
            "cost_usd": float(r[1] or 0),
            "co2e_kg": float(r[2] or 0),
            "water_litres": float(r[3] or 0),
        }
        for r in (await db.execute(by_prov_sql, params)).fetchall()
    ]

    # By service category
    by_cat_sql = text(f"""
        SELECT fr.service_category,
               COALESCE(SUM(fr.effective_cost), 0)              AS cost_usd,
               COALESCE(SUM(er.total_co2e_kg), 0)               AS co2e_kg,
               COALESCE(SUM(er.water_litres), 0)                AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.service_category
        ORDER BY cost_usd DESC
    """)
    by_cat = [
        {
            "category": r[0],
            "cost_usd": float(r[1] or 0),
            "co2e_kg": float(r[2] or 0),
            "water_litres": float(r[3] or 0),
        }
        for r in (await db.execute(by_cat_sql, params)).fetchall()
    ]

    # Top 10 resources by CO2e
    top_res_sql = text(f"""
        SELECT fr.resource_id, fr.resource_name, fr.provider_name, fr.region_id,
               fr.service_name,
               COALESCE(SUM(er.total_co2e_kg), 0) AS co2e_kg,
               COALESCE(SUM(fr.effective_cost), 0) AS cost_usd
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.resource_id, fr.resource_name, fr.provider_name,
                 fr.region_id, fr.service_name
        ORDER BY co2e_kg DESC
        LIMIT 10
    """)
    top_resources = [
        {
            "resource_id": r[0],
            "resource_name": r[1],
            "provider": r[2],
            "region": r[3],
            "service_name": r[4],
            "co2e_kg": float(r[5] or 0),
            "cost_usd": float(r[6] or 0),
        }
        for r in (await db.execute(top_res_sql, params)).fetchall()
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_cost_usd": round(cost_cur, 4),
        "total_co2e_kg": round(co2e_cur, 4),
        "scope3_co2e_kg": round(scope3_cur, 4),
        "scope3_pct": round(scope3_pct, 2),
        "total_water_litres": round(water_cur, 4),
        "water_stress_adjusted_litres": round(water_stress_cur, 4),
        "carbon_efficiency": round(carbon_efficiency, 4),
        "cost_mom_pct": _mom_pct(cost_cur, cost_prev),
        "co2e_mom_pct": _mom_pct(co2e_cur, co2e_prev),
        "water_mom_pct": _mom_pct(water_cur, water_prev),
        "by_provider": by_prov,
        "by_service_category": by_cat,
        "top_resources_by_co2e": top_resources,
    }


# ---------------------------------------------------------------------------
# GET /reports/carbon
# ---------------------------------------------------------------------------

@router.get("/carbon")
async def get_carbon_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a GHG Protocol-aligned carbon report with time series and regional breakdown.
    """
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, (today - timedelta(days=30)))

    provider_filter = "AND LOWER(fr.provider_name) = LOWER(:provider)" if provider else ""
    params: dict = {"tenant_id": tenant_id, "start": start, "end": end}
    if provider:
        params["provider"] = provider

    # Time series truncation
    _trunc_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    trunc = _trunc_map[granularity]

    ts_sql = text(f"""
        SELECT
            DATE_TRUNC('{trunc}', fr.charge_period_start)::DATE AS period,
            COALESCE(SUM(er.scope2_co2e_kg_location), 0)        AS scope2_location,
            COALESCE(SUM(er.scope2_co2e_kg_market), 0)          AS scope2_market,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)           AS scope3_total,
            COALESCE(SUM(er.total_co2e_kg), 0)                  AS total
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY period
        ORDER BY period ASC
    """)
    ts_rows = (await db.execute(ts_sql, params)).fetchall()
    time_series = [
        {
            "date": str(r[0]),
            "scope1": 0.0,  # Scope 1 is always 0 for cloud customers
            "scope2_location": float(r[1] or 0),
            "scope2_market": float(r[2] or 0),
            "scope3_total": float(r[3] or 0),
            "total": float(r[4] or 0),
        }
        for r in ts_rows
    ]

    # By region
    region_sql = text(f"""
        SELECT fr.region_id, fr.provider_name,
               COALESCE(SUM(er.total_co2e_kg), 0)               AS co2e_kg,
               COALESCE(AVG(er.carbon_intensity_gco2_kwh), 0)   AS avg_intensity
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.region_id, fr.provider_name
        ORDER BY co2e_kg DESC
    """)
    by_region = [
        {
            "region_id": r[0],
            "provider": r[1],
            "co2e_kg": float(r[2] or 0),
            "carbon_intensity_gco2_kwh": float(r[3] or 0),
        }
        for r in (await db.execute(region_sql, params)).fetchall()
    ]

    # Scope 3 breakdown
    s3_sql = text(f"""
        SELECT
            COALESCE(SUM(er.scope3_cat1_co2e_kg), 0)            AS cat1,
            COALESCE(SUM(er.scope3_cat3_co2e_kg), 0)            AS cat3,
            COALESCE(SUM(er.scope3_cat12_co2e_kg), 0)           AS cat12,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)           AS total,
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'high')   AS high_count,
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'medium') AS medium_count,
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'low')    AS low_count,
            COUNT(*)                                             AS total_count
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    s3 = (await db.execute(s3_sql, params)).fetchone()
    total_count = int(s3[7] or 1)
    scope3_breakdown = {
        "cat1_embodied": float(s3[0] or 0),
        "cat3_upstream": float(s3[1] or 0),
        "cat12_eol": float(s3[2] or 0),
        "total": float(s3[3] or 0),
        "confidence_distribution": {
            "high_pct": round(int(s3[4] or 0) / total_count * 100, 1),
            "medium_pct": round(int(s3[5] or 0) / total_count * 100, 1),
            "low_pct": round(int(s3[6] or 0) / total_count * 100, 1),
        },
    }

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "granularity": granularity},
        "time_series": time_series,
        "by_region": by_region,
        "scope3_breakdown": scope3_breakdown,
    }


# ---------------------------------------------------------------------------
# GET /reports/water
# ---------------------------------------------------------------------------

@router.get("/water")
async def get_water_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return water consumption report with regional breakdown and high-stress regions.
    """
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, (today - timedelta(days=30)))

    provider_filter = "AND LOWER(fr.provider_name) = LOWER(:provider)" if provider else ""
    params: dict = {"tenant_id": tenant_id, "start": start, "end": end}
    if provider:
        params["provider"] = provider

    # Totals
    totals_sql = text(f"""
        SELECT
            COALESCE(SUM(er.water_litres), 0)                   AS water_litres,
            COALESCE(SUM(er.water_stress_adjusted_litres), 0)   AS stress_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    totals = (await db.execute(totals_sql, params)).fetchone()

    # By region
    region_sql = text(f"""
        SELECT
            fr.region_id,
            fr.provider_name,
            COALESCE(SUM(er.water_litres), 0)                   AS water_litres,
            COALESCE(SUM(er.water_stress_adjusted_litres), 0)   AS stress_litres,
            COALESCE(AVG(er.water_stress_score), 0)             AS avg_stress_score,
            er.water_data_source                                AS cooling_type
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.region_id, fr.provider_name, er.water_data_source
        ORDER BY water_litres DESC
    """)
    by_region = [
        {
            "region_id": r[0],
            "provider": r[1],
            "water_litres": float(r[2] or 0),
            "water_stress_adjusted_litres": float(r[3] or 0),
            "wri_aqueduct_score": float(r[4] or 0),
            "cooling_type": r[5] or "air",
        }
        for r in (await db.execute(region_sql, params)).fetchall()
    ]

    # By service category
    by_cat_sql = text(f"""
        SELECT fr.service_category,
               COALESCE(SUM(er.water_litres), 0) AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.charge_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.service_category
        ORDER BY water_litres DESC
    """)
    by_cat = [
        {"category": r[0], "water_litres": float(r[1] or 0)}
        for r in (await db.execute(by_cat_sql, params)).fetchall()
    ]

    # High stress regions (WRI score > 3.0)
    high_stress = [r for r in by_region if r["wri_aqueduct_score"] > 3.0]
    high_stress.sort(key=lambda x: x["water_litres"], reverse=True)

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_water_litres": float(totals[0] or 0),
        "total_water_stress_adjusted_litres": float(totals[1] or 0),
        "by_region": by_region,
        "by_service_category": by_cat,
        "high_stress_regions": high_stress,
    }
