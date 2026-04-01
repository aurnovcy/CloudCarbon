"""Pydantic schemas for the recommendations API."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class WeightConfigRequest(BaseModel):
    cost_weight: float = Field(0.6, ge=0.0, le=1.0)
    carbon_weight: float = Field(0.3, ge=0.0, le=1.0)
    water_weight: float = Field(0.1, ge=0.0, le=1.0)

    @field_validator("water_weight")
    @classmethod
    def weights_sum_to_one(cls, v: float, info) -> float:
        data = info.data
        total = data.get("cost_weight", 0.6) + data.get("carbon_weight", 0.3) + v
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"cost_weight + carbon_weight + water_weight must sum to 1.0 (got {total:.3f})"
            )
        return v


class RecommendationRunRequest(BaseModel):
    cost_weight: float = Field(0.6, ge=0.0, le=1.0)
    carbon_weight: float = Field(0.3, ge=0.0, le=1.0)
    water_weight: float = Field(0.1, ge=0.0, le=1.0)


class RecommendationRunResponse(BaseModel):
    job_id: str
    tenant_id: UUID
    status: str
    queued_at: datetime


class RecommendationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    type: str
    resource_id: str
    provider: str
    region: str
    service_name: str
    cost_impact_monthly_usd: float
    co2e_impact_monthly_kg: float
    water_impact_monthly_litres: float
    impact_score: float
    cost_weight_used: float
    carbon_weight_used: float
    water_weight_used: float
    complexity: str
    implementation_steps: list[str]
    methodology_notes: str
    policy_blocked: bool
    policy_block_reason: Optional[str]
    requires_approval: bool
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RecommendationPatchRequest(BaseModel):
    status: str = Field(..., pattern="^(implemented|dismissed|snoozed|open)$")
    snooze_until: Optional[datetime] = None


class RecommendationSummaryResponse(BaseModel):
    open_count: int
    total_cost_opportunity_usd: float
    total_co2e_opportunity_kg: float
    total_water_opportunity_litres: float
    by_type: list[dict]
    by_provider: list[dict]


class RecommendationListResponse(BaseModel):
    items: list[RecommendationResponse]
    total: int
    page: int
    page_size: int
