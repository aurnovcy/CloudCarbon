"""
Pydantic schemas for the enrichment API endpoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class EnrichmentRunRequest(BaseModel):
    """Request body for POST /enrichment/run."""
    tenant_id: Optional[uuid.UUID] = Field(None, description="Tenant UUID to enrich records for (defaults to caller's tenant)")
    mode: Literal["incremental", "full"] = Field(
        "incremental",
        description=(
            "incremental: only enrich records without an existing enriched_record. "
            "full: re-enrich all records (use when methodology is updated)."
        ),
    )
    enrichment_version: Optional[str] = Field(
        None,
        description="Version string to stamp on re-enriched records (full mode only)",
        max_length=20,
    )
    limit: Optional[int] = Field(
        None,
        ge=1,
        le=100_000,
        description="Maximum records to process (incremental mode only; default 1000)",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class EnrichmentRunResponse(BaseModel):
    """Response for POST /enrichment/run."""
    job_id: str = Field(..., description="Job ID for status polling")
    tenant_id: uuid.UUID
    mode: str
    status: str = "queued"
    queued_at: datetime


class EnrichmentStatusResponse(BaseModel):
    """Response for GET /enrichment/status/{job_id}."""
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    processed: int = 0
    total: int = 0
    pct: float = Field(0.0, ge=0.0, le=100.0)
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


class EnrichmentSummaryResponse(BaseModel):
    """Response for GET /enrichment/summary."""
    tenant_id: uuid.UUID
    total_records_enriched: int
    total_focus_records: int
    coverage_pct: float = Field(..., description="Percentage of FOCUS records with enrichment")
    total_co2e_kg: float = Field(..., description="Total carbon across all scopes (kg CO2e)")
    total_scope3_co2e_kg: float = Field(..., description="Total Scope 3 carbon (kg CO2e)")
    scope3_pct_of_total: float = Field(..., description="Scope 3 as percentage of total carbon")
    total_water_litres: float = Field(..., description="Total water consumption (litres)")
    last_enriched_at: Optional[datetime] = None
    enrichment_version: Optional[str] = None
