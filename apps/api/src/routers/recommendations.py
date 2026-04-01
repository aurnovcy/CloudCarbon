"""
Recommendations API router.

Endpoints:
  GET    /recommendations          — list with filters, sorting, and mode
  PATCH  /recommendations/{id}     — update status (implement/dismiss/snooze)
  POST   /recommendations/run      — trigger recommendation generation
  GET    /recommendations/summary  — aggregated opportunity statistics
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import get_current_user, require_role
from src.recommendations.service import (
    WeightConfig,
    run_recommendations,
    get_recommendations_summary,
)
from src.schemas.recommendations import (
    RecommendationListResponse,
    RecommendationPatchRequest,
    RecommendationResponse,
    RecommendationRunRequest,
    RecommendationRunResponse,
    RecommendationSummaryResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recommendations", tags=["Recommendations"])


# ---------------------------------------------------------------------------
# GET /recommendations
# ---------------------------------------------------------------------------

@router.get("", response_model=RecommendationListResponse)
async def list_recommendations(
    status: Optional[str] = Query(None, pattern="^(open|implemented|dismissed|snoozed)$"),
    type: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    min_cost_impact: Optional[float] = Query(None, ge=0),
    min_co2e_impact: Optional[float] = Query(None, ge=0),
    sort_by: str = Query("impact_score", pattern="^(impact_score|cost|co2e|water)$"),
    mode: str = Query("cost", pattern="^(cost|sustainability)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List recommendations with optional filters.

    - **mode=cost**: default sort by cost_impact_monthly_usd
    - **mode=sustainability**: default sort by co2e_impact_monthly_kg
    """
    tenant_id = current_user.tenant_id

    # Build WHERE clauses
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": str(tenant_id), "limit": limit, "offset": offset}

    if status:
        conditions.append("status = :status")
        params["status"] = status
    if type:
        conditions.append("type = :type")
        params["type"] = type
    if provider:
        conditions.append("LOWER(provider) = LOWER(:provider)")
        params["provider"] = provider
    if region:
        conditions.append("LOWER(region) = LOWER(:region)")
        params["region"] = region
    if min_cost_impact is not None:
        conditions.append("cost_impact_monthly_usd >= :min_cost")
        params["min_cost"] = min_cost_impact
    if min_co2e_impact is not None:
        conditions.append("co2e_impact_monthly_kg >= :min_co2e")
        params["min_co2e"] = min_co2e_impact

    where = " AND ".join(conditions)

    # Determine sort column
    _sort_map = {
        "impact_score": "impact_score",
        "cost": "cost_impact_monthly_usd",
        "co2e": "co2e_impact_monthly_kg",
        "water": "water_impact_monthly_litres",
    }
    # mode overrides default sort
    if sort_by == "impact_score":
        if mode == "sustainability":
            order_col = "co2e_impact_monthly_kg"
        else:
            order_col = "cost_impact_monthly_usd"
    else:
        order_col = _sort_map[sort_by]

    count_sql = text(f"SELECT COUNT(*) FROM recommendations WHERE {where}")
    total = (await db.execute(count_sql, params)).scalar() or 0

    list_sql = text(f"""
        SELECT id, tenant_id, type, resource_id, provider, region, service_name,
               cost_impact_monthly_usd, co2e_impact_monthly_kg, water_impact_monthly_litres,
               impact_score, cost_weight_used, carbon_weight_used, water_weight_used,
               complexity, implementation_steps, methodology_notes,
               policy_blocked, policy_block_reason, requires_approval,
               status, created_at, updated_at
        FROM recommendations
        WHERE {where}
        ORDER BY {order_col} DESC
        LIMIT :limit OFFSET :offset
    """)
    rows = (await db.execute(list_sql, params)).fetchall()

    items = [
        RecommendationResponse(
            id=row[0], tenant_id=row[1], type=row[2], resource_id=row[3],
            provider=row[4], region=row[5], service_name=row[6],
            cost_impact_monthly_usd=float(row[7] or 0),
            co2e_impact_monthly_kg=float(row[8] or 0),
            water_impact_monthly_litres=float(row[9] or 0),
            impact_score=float(row[10] or 0),
            cost_weight_used=float(row[11] or 0),
            carbon_weight_used=float(row[12] or 0),
            water_weight_used=float(row[13] or 0),
            complexity=row[14] or "low",
            implementation_steps=row[15] or [],
            methodology_notes=row[16] or "",
            policy_blocked=bool(row[17]),
            policy_block_reason=row[18],
            requires_approval=bool(row[19]),
            status=row[20] or "open",
            created_at=row[21],
            updated_at=row[22],
        )
        for row in rows
    ]

    return RecommendationListResponse(
        items=items,
        total=int(total),
        page=(offset // limit) + 1,
        page_size=limit,
    )


# ---------------------------------------------------------------------------
# PATCH /recommendations/{id}
# ---------------------------------------------------------------------------

@router.patch("/{recommendation_id}", response_model=RecommendationResponse)
async def update_recommendation(
    recommendation_id: uuid.UUID,
    body: RecommendationPatchRequest,
    current_user=Depends(require_role("analyst")),
    db: AsyncSession = Depends(get_db),
):
    """Update the status of a recommendation (implement/dismiss/snooze)."""
    tenant_id = current_user.tenant_id
    now = datetime.now(timezone.utc)

    # Verify ownership
    check_sql = text(
        "SELECT id FROM recommendations WHERE id = :id AND tenant_id = :tenant_id"
    )
    row = (await db.execute(check_sql, {
        "id": str(recommendation_id), "tenant_id": str(tenant_id)
    })).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    update_sql = text("""
        UPDATE recommendations
        SET status = :status,
            snooze_until = :snooze_until,
            updated_at = :now
        WHERE id = :id AND tenant_id = :tenant_id
        RETURNING id, tenant_id, type, resource_id, provider, region, service_name,
                  cost_impact_monthly_usd, co2e_impact_monthly_kg, water_impact_monthly_litres,
                  impact_score, cost_weight_used, carbon_weight_used, water_weight_used,
                  complexity, implementation_steps, methodology_notes,
                  policy_blocked, policy_block_reason, requires_approval,
                  status, created_at, updated_at
    """)
    updated = (await db.execute(update_sql, {
        "status": body.status,
        "snooze_until": body.snooze_until,
        "now": now,
        "id": str(recommendation_id),
        "tenant_id": str(tenant_id),
    })).fetchone()

    # Write audit log
    audit_sql = text("""
        INSERT INTO audit_logs (id, tenant_id, user_id, action, resource_type,
                                resource_id, after_state, created_at)
        VALUES (:id, :tenant_id, :user_id, 'update_recommendation', 'recommendation',
                :resource_id, :after_state, :now)
    """)
    await db.execute(audit_sql, {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "user_id": str(current_user.id),
        "resource_id": str(recommendation_id),
        "after_state": {"status": body.status, "snooze_until": str(body.snooze_until)},
        "now": now,
    })
    await db.commit()

    row = updated
    return RecommendationResponse(
        id=row[0], tenant_id=row[1], type=row[2], resource_id=row[3],
        provider=row[4], region=row[5], service_name=row[6],
        cost_impact_monthly_usd=float(row[7] or 0),
        co2e_impact_monthly_kg=float(row[8] or 0),
        water_impact_monthly_litres=float(row[9] or 0),
        impact_score=float(row[10] or 0),
        cost_weight_used=float(row[11] or 0),
        carbon_weight_used=float(row[12] or 0),
        water_weight_used=float(row[13] or 0),
        complexity=row[14] or "low",
        implementation_steps=row[15] or [],
        methodology_notes=row[16] or "",
        policy_blocked=bool(row[17]),
        policy_block_reason=row[18],
        requires_approval=bool(row[19]),
        status=row[20] or "open",
        created_at=row[21],
        updated_at=row[22],
    )


# ---------------------------------------------------------------------------
# POST /recommendations/run
# ---------------------------------------------------------------------------

@router.post("/run", response_model=RecommendationRunResponse, status_code=202)
async def run_recommendations_endpoint(
    body: RecommendationRunRequest,
    background_tasks: BackgroundTasks,
    current_user=Depends(require_role("engineer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger recommendation generation for the current tenant as a background task.
    """
    tenant_id = current_user.tenant_id
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    weights = WeightConfig(
        cost_weight=body.cost_weight,
        carbon_weight=body.carbon_weight,
        water_weight=body.water_weight,
    )

    async def _run():
        try:
            count = await run_recommendations(tenant_id, weights, db)
            logger.info("Recommendation job %s: generated %d recommendations", job_id, count)
        except Exception as exc:
            logger.error("Recommendation job %s failed: %s", job_id, exc)

    background_tasks.add_task(_run)

    return RecommendationRunResponse(
        job_id=job_id,
        tenant_id=tenant_id,
        status="queued",
        queued_at=now,
    )


# ---------------------------------------------------------------------------
# GET /recommendations/summary
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=RecommendationSummaryResponse)
async def recommendations_summary(
    current_user=Depends(require_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated recommendation opportunity statistics for the tenant."""
    summary = await get_recommendations_summary(current_user.tenant_id, db)
    return RecommendationSummaryResponse(**summary)
