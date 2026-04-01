"""
Pydantic schemas for the agent control API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Agent config schemas
# ---------------------------------------------------------------------------

class AgentConfigResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    agent_type: str
    enabled: bool
    dry_run: bool
    cron_expression: str | None
    thresholds: dict[str, Any]
    notification_channels: list[dict[str, Any]]
    require_approval_for_actions: bool
    created_at: datetime | None
    updated_at: datetime | None
    last_run_summary: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class AgentConfigPatchRequest(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    cron_expression: str | None = None
    thresholds: dict[str, Any] | None = None
    notification_channels: list[dict[str, Any]] | None = None
    require_approval_for_actions: bool | None = None


# ---------------------------------------------------------------------------
# Agent run schemas
# ---------------------------------------------------------------------------

class AgentRunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    agent_type: str
    status: str
    trigger: str | None
    started_at: datetime | None
    completed_at: datetime | None
    dry_run: bool
    actions_taken: list[dict[str, Any]] | None
    summary: dict[str, Any] | None

    model_config = {"from_attributes": True}


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    total: int
    limit: int
    offset: int


class ManualRunRequest(BaseModel):
    dry_run: bool = True


class ManualRunResponse(BaseModel):
    job_id: UUID
    agent_type: str
    dry_run: bool
    status: str = "started"
    started_at: datetime


# ---------------------------------------------------------------------------
# Approval schemas
# ---------------------------------------------------------------------------

class ApproveActionRequest(BaseModel):
    action_index: int = Field(
        ...,
        ge=0,
        description="Index of the action in agent_runs.actions_taken to approve",
    )
    notes: str | None = None


class ApproveActionResponse(BaseModel):
    run_id: UUID
    action_index: int
    status: str
    approved_at: datetime
    notes: str | None
