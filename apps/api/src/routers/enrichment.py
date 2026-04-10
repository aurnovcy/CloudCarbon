"""
Enrichment API router (synchronous).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.database import get_db, SessionLocal
from src.dependencies.auth import get_current_user, require_role
from src.enrichment.service import EnrichmentService, ENRICHMENT_VERSION
from src.redis_client import get_redis
from src.schemas.enrichment import (
    EnrichmentRunRequest,
    EnrichmentRunResponse,
    EnrichmentStatusResponse,
    EnrichmentSummaryResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/enrichment", tags=["Enrichment"])

_JOB_KEY_PREFIX = "enrichment:job:"
_JOB_TTL_SECONDS = 86_400


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _set_job_state(redis_client: redis.Redis, job_id: str, state: dict) -> None:
    try:
        redis_client.setex(_job_key(job_id), _JOB_TTL_SECONDS, json.dumps(state, default=str))
    except Exception as exc:
        logger.warning("Failed to write job state to Redis for %s: %s", job_id, exc)


def _get_job_state(redis_client: redis.Redis, job_id: str) -> dict | None:
    try:
        raw = redis_client.get(_job_key(job_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to read job state from Redis for %s: %s", job_id, exc)
    return None


def _run_enrichment_background(
    job_id: str,
    tenant_id: uuid.UUID,
    mode: str,
    enrichment_version: str,
    limit: int,
    redis_client: redis.Redis,
) -> None:
    started_at = datetime.now(tz=timezone.utc)
    _set_job_state(redis_client, job_id, {
        "job_id": job_id, "status": "running", "processed": 0, "total": 0,
        "pct": 0.0, "failed": 0, "errors": [],
        "started_at": started_at.isoformat(), "completed_at": None, "duration_seconds": None,
    })

    db = SessionLocal()
    try:
        svc = EnrichmentService(enrichment_version=enrichment_version)
        if mode == "full":
            result = svc.re_enrich_all(tenant_id=tenant_id, enrichment_version=enrichment_version, db=db)
        else:
            result = svc.enrich_batch(tenant_id=tenant_id, db=db, limit=limit)

        completed_at = datetime.now(tz=timezone.utc)
        total = result.processed + result.failed
        pct = (result.processed / total * 100.0) if total > 0 else 100.0

        _set_job_state(redis_client, job_id, {
            "job_id": job_id, "status": "completed",
            "processed": result.processed, "total": total, "pct": round(pct, 2),
            "failed": result.failed, "errors": result.errors[:50],
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": result.duration_seconds,
        })
    except Exception as exc:
        logger.error("Enrichment job %s failed: %s", job_id, exc, exc_info=True)
        completed_at = datetime.now(tz=timezone.utc)
        _set_job_state(redis_client, job_id, {
            "job_id": job_id, "status": "failed", "processed": 0, "total": 0,
            "pct": 0.0, "failed": 1, "errors": [str(exc)],
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(), "duration_seconds": None,
        })
    finally:
        db.close()


@router.post("/run", response_model=EnrichmentRunResponse, status_code=status.HTTP_202_ACCEPTED)
def run_enrichment(
    request: EnrichmentRunRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_role("engineer"))],
) -> EnrichmentRunResponse:
    tenant_id = request.tenant_id or current_user.tenant_id

    redis_client = None
    try:
        redis_client = get_redis()
    except Exception:
        pass

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(tz=timezone.utc)
    enrichment_version = request.enrichment_version or ENRICHMENT_VERSION
    limit = request.limit or 1000

    if redis_client:
        _set_job_state(redis_client, job_id, {
            "job_id": job_id, "status": "queued", "processed": 0, "total": 0,
            "pct": 0.0, "failed": 0, "errors": [],
            "started_at": None, "completed_at": None, "duration_seconds": None,
        })

    background_tasks.add_task(
        _run_enrichment_background,
        job_id=job_id,
        tenant_id=tenant_id,
        mode=request.mode,
        enrichment_version=enrichment_version,
        limit=limit,
        redis_client=redis_client,
    )

    return EnrichmentRunResponse(
        job_id=job_id, tenant_id=tenant_id,
        mode=request.mode, status="queued", queued_at=queued_at,
    )


@router.get("/status/{job_id}", response_model=EnrichmentStatusResponse)
def get_enrichment_status(
    job_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> EnrichmentStatusResponse:
    try:
        redis_client = get_redis()
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")

    state = _get_job_state(redis_client, job_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found or expired")

    return EnrichmentStatusResponse(
        job_id=state["job_id"], status=state["status"],
        processed=state.get("processed", 0), total=state.get("total", 0),
        pct=state.get("pct", 0.0), failed=state.get("failed", 0),
        errors=state.get("errors", []), started_at=state.get("started_at"),
        completed_at=state.get("completed_at"), duration_seconds=state.get("duration_seconds"),
    )


@router.get("/summary", response_model=EnrichmentSummaryResponse)
def get_enrichment_summary(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_role("viewer"))],
    tenant_id: Annotated[Optional[uuid.UUID], Query(description="Tenant UUID (defaults to caller's tenant)")] = None,
) -> EnrichmentSummaryResponse:
    tenant_id = tenant_id or current_user.tenant_id
    svc = EnrichmentService()
    summary = svc.get_summary(tenant_id=tenant_id, db=db)
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
