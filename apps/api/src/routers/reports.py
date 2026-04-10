"""
Reports API router (synchronous).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import get_db
from src.dependencies.auth import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


def _parse_date(d: Optional[str], default: date) -> date:
    if d is None:
        return default
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date format: {d}. Use YYYY-MM-DD.")


def _mom_pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 2)


@router.get("/overview")
def get_overview(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    mode: str = Query("cost", pattern="^(cost|sustainability)$"),
    current_user=Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, today.replace(day=1))

    duration = (end - start).days or 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration)

    provider_filter = "AND LOWER(fr.provider) = LOWER(:provider)" if provider else ""
    params: dict = {
        "tenant_id": tenant_id, "start": start, "end": end,
        "prev_start": prev_start, "prev_end": prev_end,
    }
    if provider:
        params["provider"] = provider

    totals_sql = text(f"""
        SELECT
            COALESCE(SUM(fr.cost_usd), 0)                   AS cost_usd,
            COALESCE(SUM(er.total_co2e_kg), 0)                    AS co2e_kg,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)             AS scope3_co2e_kg,
            COALESCE(SUM(er.water_litres), 0)                     AS water_litres,
            COALESCE(SUM(er.water_stress_adjusted_litres), 0)     AS water_stress_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    cur = db.execute(totals_sql, params).fetchone()
    cost_cur = float(cur[0] or 0)
    co2e_cur = float(cur[1] or 0)
    scope3_cur = float(cur[2] or 0)
    water_cur = float(cur[3] or 0)
    water_stress_cur = float(cur[4] or 0)

    prev_params = {**params, "start": prev_start, "end": prev_end}
    prev_sql = text(f"""
        SELECT
            COALESCE(SUM(fr.cost_usd), 0),
            COALESCE(SUM(er.total_co2e_kg), 0),
            COALESCE(SUM(er.water_litres), 0)
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    prev = db.execute(prev_sql, prev_params).fetchone()
    cost_prev = float(prev[0] or 0)
    co2e_prev = float(prev[1] or 0)
    water_prev = float(prev[2] or 0)

    carbon_efficiency = (co2e_cur / cost_cur * 1000) if cost_cur > 0 else 0.0
    scope3_pct = (scope3_cur / co2e_cur * 100) if co2e_cur > 0 else 0.0

    by_prov_sql = text(f"""
        SELECT fr.provider,
               COALESCE(SUM(fr.cost_usd), 0) AS cost_usd,
               COALESCE(SUM(er.total_co2e_kg), 0)  AS co2e_kg,
               COALESCE(SUM(er.water_litres), 0)   AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.provider ORDER BY cost_usd DESC
    """)
    by_prov = [
        {"provider": r[0], "cost_usd": float(r[1] or 0), "co2e_kg": float(r[2] or 0), "water_litres": float(r[3] or 0)}
        for r in db.execute(by_prov_sql, params).fetchall()
    ]

    by_cat_sql = text(f"""
        SELECT fr.service_category,
               COALESCE(SUM(fr.cost_usd), 0) AS cost_usd,
               COALESCE(SUM(er.total_co2e_kg), 0)  AS co2e_kg,
               COALESCE(SUM(er.water_litres), 0)   AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.service_category ORDER BY cost_usd DESC
    """)
    by_cat = [
        {"category": r[0], "cost_usd": float(r[1] or 0), "co2e_kg": float(r[2] or 0), "water_litres": float(r[3] or 0)}
        for r in db.execute(by_cat_sql, params).fetchall()
    ]

    top_res_sql = text(f"""
        SELECT fr.resource_id, fr.resource_type, fr.provider, fr.region,
               fr.service_name,
               COALESCE(SUM(er.total_co2e_kg), 0) AS co2e_kg,
               COALESCE(SUM(fr.cost_usd), 0) AS cost_usd
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.resource_id, fr.resource_type, fr.provider,
                 fr.region, fr.service_name
        ORDER BY co2e_kg DESC LIMIT 10
    """)
    top_resources = [
        {"resource_id": r[0], "resource_name": r[1], "provider": r[2],
         "region": r[3], "service_name": r[4], "co2e_kg": float(r[5] or 0), "cost_usd": float(r[6] or 0)}
        for r in db.execute(top_res_sql, params).fetchall()
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


@router.get("/carbon")
def get_carbon_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    current_user=Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, (today - timedelta(days=30)))

    provider_filter = "AND LOWER(fr.provider) = LOWER(:provider)" if provider else ""
    params: dict = {"tenant_id": tenant_id, "start": start, "end": end}
    if provider:
        params["provider"] = provider

    _trunc_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    trunc = _trunc_map[granularity]

    ts_sql = text(f"""
        SELECT
            DATE_TRUNC('{trunc}', fr.billing_period_start)::DATE AS period,
            COALESCE(SUM(er.scope2_co2e_kg_location), 0)        AS scope2_location,
            COALESCE(SUM(er.scope2_co2e_kg_market), 0)          AS scope2_market,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)           AS scope3_total,
            COALESCE(SUM(er.total_co2e_kg), 0)                  AS total
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY period ORDER BY period ASC
    """)
    time_series = [
        {"date": str(r[0]), "scope1": 0.0, "scope2_location": float(r[1] or 0),
         "scope2_market": float(r[2] or 0), "scope3_total": float(r[3] or 0), "total": float(r[4] or 0)}
        for r in db.execute(ts_sql, params).fetchall()
    ]

    region_sql = text(f"""
        SELECT fr.region, fr.provider,
               COALESCE(SUM(er.total_co2e_kg), 0)               AS co2e_kg,
               COALESCE(AVG(er.carbon_intensity_gco2_kwh), 0)   AS avg_intensity
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.region, fr.provider ORDER BY co2e_kg DESC
    """)
    by_region = [
        {"region_id": r[0], "provider": r[1], "co2e_kg": float(r[2] or 0), "carbon_intensity_gco2_kwh": float(r[3] or 0)}
        for r in db.execute(region_sql, params).fetchall()
    ]

    s3_sql = text(f"""
        SELECT
            COALESCE(SUM(er.scope3_cat1_co2e_kg), 0),
            COALESCE(SUM(er.scope3_cat3_co2e_kg), 0),
            COALESCE(SUM(er.scope3_cat12_co2e_kg), 0),
            COALESCE(SUM(er.scope3_total_co2e_kg), 0),
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'high'),
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'medium'),
            COUNT(*) FILTER (WHERE er.scope3_confidence = 'low'),
            COUNT(*)
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    s3 = db.execute(s3_sql, params).fetchone()
    total_count = int(s3[7] or 1)
    scope3_breakdown = {
        "cat1_embodied": float(s3[0] or 0), "cat3_upstream": float(s3[1] or 0),
        "cat12_eol": float(s3[2] or 0), "total": float(s3[3] or 0),
        "confidence_distribution": {
            "high_pct": round(int(s3[4] or 0) / total_count * 100, 1),
            "medium_pct": round(int(s3[5] or 0) / total_count * 100, 1),
            "low_pct": round(int(s3[6] or 0) / total_count * 100, 1),
        },
    }

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "granularity": granularity},
        "time_series": time_series, "by_region": by_region, "scope3_breakdown": scope3_breakdown,
    }


@router.get("/water")
def get_water_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    current_user=Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, (today - timedelta(days=30)))

    provider_filter = "AND LOWER(fr.provider) = LOWER(:provider)" if provider else ""
    params: dict = {"tenant_id": tenant_id, "start": start, "end": end}
    if provider:
        params["provider"] = provider

    totals_sql = text(f"""
        SELECT
            COALESCE(SUM(er.water_litres), 0),
            COALESCE(SUM(er.water_stress_adjusted_litres), 0)
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
    """)
    totals = db.execute(totals_sql, params).fetchone()

    region_sql = text(f"""
        SELECT fr.region, fr.provider,
               COALESCE(SUM(er.water_litres), 0)                 AS water_litres,
               COALESCE(SUM(er.water_stress_adjusted_litres), 0) AS water_stress_litres,
               COALESCE(AVG(er.water_stress_score), 0)           AS water_stress_score,
               er.water_data_source
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.region, fr.provider, er.water_data_source
        ORDER BY 3 DESC
    """)
    by_region = [
        {"region_id": r[0], "provider": r[1], "water_litres": float(r[2] or 0),
         "water_stress_adjusted_litres": float(r[3] or 0), "wri_aqueduct_score": float(r[4] or 0),
         "cooling_type": r[5] or "air"}
        for r in db.execute(region_sql, params).fetchall()
    ]

    by_cat_sql = text(f"""
        SELECT fr.service_category, COALESCE(SUM(er.water_litres), 0) AS water_litres
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
          {provider_filter}
        GROUP BY fr.service_category ORDER BY water_litres DESC
    """)
    by_cat = [{"category": r[0], "water_litres": float(r[1] or 0)} for r in db.execute(by_cat_sql, params).fetchall()]

    high_stress = [r for r in by_region if r["wri_aqueduct_score"] > 3.0]
    high_stress.sort(key=lambda x: x["water_litres"], reverse=True)

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_water_litres": float(totals[0] or 0),
        "total_water_stress_adjusted_litres": float(totals[1] or 0),
        "by_region": by_region, "by_service_category": by_cat, "high_stress_regions": high_stress,
    }


@router.get("/executive-summary")
def get_executive_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user=Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    from src.reports.generator import generate_executive_summary, ReportData

    today = date.today()
    end = _parse_date(end_date, today)
    start = _parse_date(start_date, today.replace(day=1))
    tenant_id = str(current_user.tenant_id)

    # Main period aggregates (join focus_records + enriched_records)
    row = db.execute(text("""
        SELECT
            COALESCE(SUM(fr.cost_usd), 0)               AS total_cost,
            COALESCE(SUM(er.total_co2e_kg), 0)                AS total_co2e,
            COALESCE(SUM(er.scope3_total_co2e_kg), 0)         AS scope3_co2e,
            COALESCE(SUM(er.water_litres), 0)                 AS water,
            COALESCE(SUM(er.water_stress_adjusted_litres), 0) AS water_stress
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tid
          AND DATE(fr.billing_period_start) BETWEEN :start AND :end
    """), {"tid": tenant_id, "start": start, "end": end}).fetchone()

    # Top provider by carbon
    top_provider_carbon = db.execute(text("""
        SELECT fr.provider
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tid
        GROUP BY fr.provider
        ORDER BY SUM(er.total_co2e_kg) DESC
        LIMIT 1
    """), {"tid": tenant_id}).scalar() or "Unknown"

    # Top provider by cost
    top_provider_cost = db.execute(text("""
        SELECT provider
        FROM focus_records
        WHERE tenant_id = :tid
        GROUP BY provider
        ORDER BY SUM(cost_usd) DESC
        LIMIT 1
    """), {"tid": tenant_id}).scalar() or "Unknown"

    # Top service by carbon
    top_service = db.execute(text("""
        SELECT fr.service_name
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tid
        GROUP BY fr.service_name
        ORDER BY SUM(er.total_co2e_kg) DESC
        LIMIT 1
    """), {"tid": tenant_id}).scalar() or "Unknown"

    # Open recommendations
    open_recs = db.execute(text("""
        SELECT
            COUNT(*),
            COALESCE(SUM(cost_impact_monthly_usd), 0),
            COALESCE(SUM(co2e_impact_monthly_kg), 0)
        FROM recommendations
        WHERE tenant_id = :tid AND status = 'open'
    """), {"tid": tenant_id}).fetchone()

    # High water stress regions (score > 3.0)
    stress_regions = db.execute(text("""
        SELECT DISTINCT fr.region
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tid
          AND er.water_stress_score > 3.0
        ORDER BY fr.region
    """), {"tid": tenant_id}).fetchall()
    high_stress_water_regions = [r[0] for r in stress_regions if r[0]]

    total_co2e = float(row.total_co2e or 0)
    total_cost = float(row.total_cost or 0)
    scope3_co2e = float(row.scope3_co2e or 0)
    scope3_pct = (scope3_co2e / total_co2e * 100) if total_co2e > 0 else 0.0
    carbon_efficiency = (total_co2e / total_cost * 1000) if total_cost > 0 else 0.0

    data = ReportData(
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        total_cost_usd=total_cost,
        total_co2e_kg=total_co2e,
        scope3_co2e_kg=scope3_co2e,
        scope3_pct=scope3_pct,
        total_water_litres=float(row.water or 0),
        water_stress_adjusted_litres=float(row.water_stress or 0),
        carbon_efficiency=carbon_efficiency,
        cost_mom_pct=0.0,
        co2e_mom_pct=0.0,
        water_mom_pct=0.0,
        top_provider_by_cost=top_provider_cost,
        top_provider_by_carbon=top_provider_carbon,
        top_service_by_carbon=top_service,
        open_recommendations_count=int(open_recs[0] or 0),
        total_cost_opportunity_usd=float(open_recs[1] or 0),
        total_co2e_opportunity_kg=float(open_recs[2] or 0),
        high_stress_water_regions=high_stress_water_regions,
    )

    return generate_executive_summary(data)
