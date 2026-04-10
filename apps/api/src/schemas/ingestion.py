"""
Pydantic request/response schemas for the ingestion and accounts API.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CloudProvider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    ALIBABA = "alibaba"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    PENDING = "pending"


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Ingestion result
# ---------------------------------------------------------------------------

class ValidationErrorDetail(BaseModel):
    row: int
    field: str | None = None
    message: str


class IngestionResultResponse(BaseModel):
    inserted: int
    updated: int
    skipped: int = 0
    total: int
    errors: list[str] = Field(default_factory=list)
    validation_errors: list[ValidationErrorDetail] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sync job
# ---------------------------------------------------------------------------

class SyncJobResponse(BaseModel):
    status: str = "sync_started"
    account_id: UUID
    job_id: UUID


class SyncJobStatusResponse(BaseModel):
    job_id: UUID
    account_id: UUID | None = None
    status: SyncStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: IngestionResultResponse | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Cloud account schemas
# ---------------------------------------------------------------------------

class CloudAccountCreate(BaseModel):
    """Request body for creating a new cloud account connection."""
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="display_name", min_length=1, max_length=255, description="Human-readable account name")
    provider: CloudProvider
    account_identifier: str = Field(
        ...,
        alias="account_id",
        description="Provider account ID (AWS account ID, Azure subscription ID, GCP project ID, Alibaba account ID)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific configuration (S3 bucket, BigQuery dataset, etc.)",
    )
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description="Provider credentials (will be stored as credentials_ref, not raw)",
    )

    @field_validator("credentials")
    @classmethod
    def credentials_not_empty(cls, v: dict) -> dict:
        # Credentials are optional for accounts using IAM roles / managed identity
        return v


class CloudAccountResponse(BaseModel):
    """Response schema for a cloud account."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    provider: str
    account_identifier: str
    status: str
    config: dict[str, Any] = Field(default_factory=dict)
    credentials_ref: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    record_count: int = 0


class CloudAccountListResponse(BaseModel):
    accounts: list[CloudAccountResponse]
    total: int


# ---------------------------------------------------------------------------
# Credential validation result
# ---------------------------------------------------------------------------

class CredentialValidationResult(BaseModel):
    valid: bool
    provider: CloudProvider
    account_identifier: str | None = None
    message: str
