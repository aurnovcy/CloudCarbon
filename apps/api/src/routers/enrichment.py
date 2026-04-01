"""
Enrichment API router.

Endpoints:
  POST /enrichment/run           — Start batch enrichment as a background task
  GET  /enrichment/status/{job_id} — Poll enrichment job progress from Redis
  GET  /enrichment/summary       — Aggregated carbon and water statistics
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import get_current_user, require_role
from src.enrichment.service import EnrichmentService, ENRICHMENT_VERSION
from src.schemas.enrichment import (
    EnrichmentRunRequest,
    EnrichmentRunResponse,
    EnrichmentStatusResponse,
    EnrichmentSummaryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enrichment", tags=["Enrichment"])

# ---------------------------------------------------------------------------
# Redis job state helpers
# ---------------------------------------------------------------------------

_JOB_KEY_PREFIX = "enrichment:job:"
_JOB_TTL_SECONDS = 86_400  # 24 hours


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


async def _set_job_state(redis_client: any, job_id: str, state: dict) -> None:
    """Write job state to Redis."""
    try:
        await redis_client.setex(
            _job_key(job_id),
            _JOB_TTL_SECONDS,
            json.dumps(state, default=str),
        )
    except Exception as exc:
        logger.warning("Failed to write job state to Redis for %s: %s", job_id, exc)


async def _get_job_state(redis_client: any, job_id: str) -> dict | None:
    """Read job state from Redis."""
    try:
        raw = await redis_client.get(_job_key(job_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to read job state from Redis for %s: %s", job_id, exc)
    return None


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

async def _run_enrichment_background(
    job_id: str,
    tenant_id: uuid.UUID,
    mode: str,
    enrichment_version: str,
    limit: int,
    db_factory: any,
    redis_client: any,
) -> None:
    """
    Background task that runs enrichment and updates Redis job state.
    """
    started_at = datetime.now(tz=timezone.utc)

    # Mark as running
    await _set_job_state(redis_client, job_id, {
        "job_id": job_id,
        "status": "running",
        "processed": 0,
        "total": 0,
        "pct": 0.0,
        "failed": 0,
        "errors": [],
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "duration_seconds": None,
    })

    try:
        async with db_factory() as db:
            svc = EnrichmentService(enrichment_version=enrichment_version)

            if mode == "full":
                result = await svc.re_enrich_all(
                    tenant_id=tenant_id,
                    enrichment_version=enrichment_version,
                    db=db,
                )
            else:
                result = await svc.enrich_batch(
                    tenant_id=tenant_id,
                    db=db,
                    limit=limit,
                )

        completed_at = datetime.now(tz=timezone.utc)
        total = result.processed + result.failed
        pct = (result.processed / total * 100.0) if total > 0 else 100.0

        await _set_job_state(redis_client, job_id, {
            "job_id": job_id,
            "status": "completed",
            "processed": result.processed,
            "total": total,
            "pct": round(pct, 2),
            "failed": result.failed,
            "errors": result.errors[:50],  # Cap error list
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": result.duration_seconds,
        })

    except Exception as exc:
        logger.error("Enrichment job %s failed: %s", job_id, exc, exc_info=True)
        completed_at = datetime.now(tz=timezone.utc)
        await _set_job_state(redis_client, job_id, {
            "job_id": job_id,
            "status": "failed",
            "processed": 0,
            "total": 0,
            "pct": 0.0,
            "failed": 1,
            "errors": [str(exc)],
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": None,
        })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/run",
    response_model=EnrichmentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start enrichment job",
    description=(
        "Trigger carbon and water enrichment for a tenant's FOCUS records. "
        "Runs as a background task; poll /enrichment/status/{job_id} for progress."
    ),
)
async def run_enrichment(
    request: EnrichmentRunRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_role("engineer"))],
) -> EnrichmentRunResponse:
    """
    Start a batch enrichment job.

    - **incremental**: Enrich only records without an existing enriched_record.
    - **full**: Re-enrich all records (use after methodology updates).
    """
    # Import Redis client lazily to avoid import-time issues
    try:
        from src.redis_client import get_redis
        redis_client = await get_redis()
    except Exception:
        redis_client = None

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(tz=timezone.utc)
    enrichment_version = request.enrichment_version or ENRICHMENT_VERSION
    limit = request.limit or 1000

    # Write initial queued state
    if redis_client:
        await _set_job_state(redis_client, job_id, {
            "job_id": job_id,
            "status": "queued",
            "processed": 0,
            "total": 0,
            "pct": 0.0,
            "failed": 0,
            "errors": [],
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
        })

    # Import the db session factory for background task
    from src.database import AsyncSessionLocal

    background_tasks.add_task(
        _run_enrichment_background,
        job_id=job_id,
        tenant_id=request.tenant_id,
        mode=request.mode,
        enrichment_version=enrichment_version,
        limit=limit,
        db_factory=AsyncSessionLocal,
        redis_client=redis_client,
    )

    logger.info(
        "Enrichment job %s queued: tenant=%s mode=%s",
        job_id, request.tenant_id, request.mode,
    )

    return EnrichmentRunResponse(
        job_id=job_id,
        tenant_id=request.tenant_id,
        mode=request.mode,
        status="queued",
        queued_at=queued_at,
    )


@router.get(
    "/status/{job_id}",
    response_model=EnrichmentStatusResponse,
    summary="Get enrichment job status",
    description="Poll the progress of an enrichment job by job_id.",
)
async def get_enrichment_status(
    job_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> EnrichmentStatusResponse:
    """
    Return the current status of an enrichment job.

    Progress is stored in Redis. Returns 404 if the job is not found
    (either it never existed or the 24-hour TTL has expired).
    """
    try:
        from src.redis_client import get_redis
        redis_client = await get_redis()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is not available; cannot retrieve job status",
        )

    state = await _get_job_state(redis_client, job_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found or has expired",
        )

    return EnrichmentStatusResponse(
        job_id=state["job_id"],
        status=state["status"],
        processed=state.get("processed", 0),
        total=state.get("total", 0),
        pct=state.get("pct", 0.0),
        failed=state.get("failed", 0),
        errors=state.get("errors", []),
        started_at=state.get("started_at"),
        completed_at=state.get("completed_at"),
        duration_seconds=state.get("duration_seconds"),
    )


@router.get(
    "/summary",
    response_model=EnrichmentSummaryResponse,
    summary="Get enrichment summary",
    description=(
        "Return aggregated carbon and water statistics for the current tenant. "
        "Includes total CO2e, Scope 3 breakdown, water consumption, and coverage percentage."
    ),
)
async def get_enrichment_summary(
    tenant_id: Annotated[uuid.UUID, Query(description="Tenant UUID")],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_role("viewer"))],
) -> EnrichmentSummaryResponse:
    """
    Return aggregated enrichment statistics for a tenant.

    Accessible by all authenticated roles (viewer and above).
    """
    svc = EnrichmentService()
    summary = await svc.get_summary(tenant_id=tenant_id, db=db)

    return EnrichmentSummaryResponse(
        tenant_id=tenant_id,
        total_records_enriched=summary["total_records_enriched"],
        total_focus_records=summary["total_focus_records"],
        coverage_pct=summary["coverage_pct"],
        total_co2e_kg=summary["total_co2e_kg"],
        total_scope3_co2e_kg=summary["total_scope3_co2e_kg"],
        scope3_pct_of_total=summary["scope3_pct_of_total"],
        total_water_litres=summary["total_water_litres"],
        last_enriched_at=summary.get("last_enriched_at"),
        enrichment_version=ENRICHMENT_VERSION,
    )
