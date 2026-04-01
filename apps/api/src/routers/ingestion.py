"""
Ingestion and cloud accounts API router.

Endpoints:
  POST   /ingest/upload               — Upload a FOCUS/provider billing file
  POST   /ingest/sync/{account_id}    — Trigger async sync for a cloud account
  GET    /ingest/sync/{job_id}/status — Get sync job status from Redis
  GET    /accounts                    — List cloud accounts for current tenant
  POST   /accounts                    — Create a new cloud account connection
  DELETE /accounts/{account_id}       — Soft-delete a cloud account
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import get_current_user, require_role
from src.ingestion.service import (
    IngestionResult,
    handle_focus_upload,
    sync_account_by_provider,
)
from src.ingestion.normalizers.aws import parse_aws_csv
from src.ingestion.normalizers.azure import parse_azure_csv
from src.ingestion.normalizers.gcp import parse_gcp_json
from src.ingestion.service import ingest_focus_records
from src.models.cloud_account import CloudAccount
from src.models.user import User
from src.redis_client import get_redis
from src.schemas.ingestion import (
    CloudAccountCreate,
    CloudAccountListResponse,
    CloudAccountResponse,
    CredentialValidationResult,
    IngestionResultResponse,
    SyncJobResponse,
    SyncJobStatusResponse,
    SyncStatus,
    ValidationErrorDetail,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ingestion"])
accounts_router = APIRouter(tags=["Accounts"])

# ---------------------------------------------------------------------------
# Helper: detect file format from filename / content-type
# ---------------------------------------------------------------------------

PROVIDER_EXTENSIONS = {
    ".csv": None,      # Could be any provider or FOCUS
    ".parquet": None,  # FOCUS passthrough
    ".pq": None,
    ".json": "gcp",    # GCP BigQuery JSON export
}


def _detect_provider(filename: str, explicit_provider: str | None) -> str | None:
    """Return provider hint from filename or explicit override."""
    if explicit_provider:
        return explicit_provider.lower()
    name_lower = filename.lower()
    if "cur" in name_lower or "aws" in name_lower:
        return "aws"
    if "azure" in name_lower:
        return "azure"
    if "gcp" in name_lower or "google" in name_lower:
        return "gcp"
    if "alibaba" in name_lower or "aliyun" in name_lower:
        return "alibaba"
    return None  # Assume FOCUS passthrough


# ---------------------------------------------------------------------------
# Ingestion endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/ingest/upload",
    response_model=IngestionResultResponse,
    summary="Upload a billing file for ingestion",
    description=(
        "Accept a multipart billing file upload (CSV, Parquet, or JSON). "
        "Supports AWS CUR, Azure Cost Management, GCP BigQuery export, and FOCUS 1.0 format. "
        "If the file contains validation errors, they are returned without ingesting any records."
    ),
)
async def upload_billing_file(
    file: UploadFile = File(..., description="Billing file (CSV, Parquet, or JSON)"),
    account_id: UUID = Query(..., description="Cloud account UUID to associate records with"),
    provider: str | None = Query(
        None,
        description="Provider hint: aws | azure | gcp | alibaba. Auto-detected if not provided.",
    ),
    current_user: User = Depends(require_role("analyst", "engineer", "admin")),
    db: AsyncSession = Depends(get_db),
) -> IngestionResultResponse:
    """Upload and ingest a billing file."""
    import tempfile, os

    filename = file.filename or "upload.csv"
    detected_provider = _detect_provider(filename, provider)

    # Verify account belongs to the current user's tenant
    account_result = await db.execute(
        select(CloudAccount).where(
            CloudAccount.id == account_id,
            CloudAccount.tenant_id == current_user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cloud account {account_id} not found",
        )

    # Write upload to temp file
    content = await file.read()
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".csv"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if detected_provider == "aws":
            records = parse_aws_csv(tmp_path)
            result = await ingest_focus_records(records, current_user.tenant_id, account_id, db)
        elif detected_provider == "azure":
            records = parse_azure_csv(tmp_path)
            result = await ingest_focus_records(records, current_user.tenant_id, account_id, db)
        elif detected_provider == "gcp":
            records = parse_gcp_json(tmp_path)
            result = await ingest_focus_records(records, current_user.tenant_id, account_id, db)
        else:
            # FOCUS passthrough (handles CSV and Parquet)
            upload_file_obj = UploadFile(filename=filename)
            upload_file_obj._file = open(tmp_path, "rb")  # type: ignore[attr-defined]
            result = await handle_focus_upload(
                upload_file_obj, current_user.tenant_id, account_id, db
            )
    finally:
        os.unlink(tmp_path)

    return IngestionResultResponse(
        inserted=result.inserted,
        updated=result.updated,
        skipped=result.skipped,
        total=result.total,
        errors=result.errors,
        validation_errors=[
            ValidationErrorDetail(**e) for e in result.validation_errors
        ],
    )


@router.post(
    "/ingest/sync/{account_id}",
    response_model=SyncJobResponse,
    summary="Trigger async sync for a cloud account",
    description=(
        "Start a background sync job for the specified cloud account. "
        "The sync runs asynchronously; poll /ingest/sync/{job_id}/status for progress."
    ),
)
async def trigger_sync(
    account_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role("engineer", "admin")),
    db: AsyncSession = Depends(get_db),
) -> SyncJobResponse:
    """Trigger an async sync for a cloud account."""
    # Verify account
    account_result = await db.execute(
        select(CloudAccount).where(
            CloudAccount.id == account_id,
            CloudAccount.tenant_id == current_user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cloud account {account_id} not found",
        )

    job_id = uuid.uuid4()

    # Store initial job status in Redis
    redis = await get_redis()
    job_key = f"sync_job:{job_id}"
    await redis.setex(
        job_key,
        86400,  # 24-hour TTL
        json.dumps({
            "job_id": str(job_id),
            "account_id": str(account_id),
            "status": SyncStatus.PENDING,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "completed_at": None,
            "result": None,
            "error": None,
        }),
    )

    # Run sync in background
    background_tasks.add_task(
        _run_sync_background,
        account=account,
        job_id=job_id,
        tenant_id=current_user.tenant_id,
    )

    return SyncJobResponse(status="sync_started", account_id=account_id, job_id=job_id)


async def _run_sync_background(account: CloudAccount, job_id: UUID, tenant_id: UUID) -> None:
    """Background task: run provider sync and update Redis job status."""
    from src.database import AsyncSessionLocal

    redis = await get_redis()
    job_key = f"sync_job:{job_id}"

    # Mark as running
    await redis.setex(
        job_key,
        86400,
        json.dumps({
            "job_id": str(job_id),
            "account_id": str(account.id),
            "status": SyncStatus.RUNNING,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "completed_at": None,
            "result": None,
            "error": None,
        }),
    )

    try:
        async with AsyncSessionLocal() as db:
            result = await sync_account_by_provider(account, db)

            # Update last_sync_at on the account
            await db.execute(
                update(CloudAccount)
                .where(CloudAccount.id == account.id)
                .values(last_sync_at=datetime.now(tz=timezone.utc))
            )
            await db.commit()

        await redis.setex(
            job_key,
            86400,
            json.dumps({
                "job_id": str(job_id),
                "account_id": str(account.id),
                "status": SyncStatus.COMPLETED,
                "started_at": None,
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                "result": result.to_dict(),
                "error": None,
            }),
        )
    except Exception as exc:
        logger.error("Background sync failed for job %s: %s", job_id, exc)
        await redis.setex(
            job_key,
            86400,
            json.dumps({
                "job_id": str(job_id),
                "account_id": str(account.id),
                "status": SyncStatus.FAILED,
                "started_at": None,
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                "result": None,
                "error": str(exc),
            }),
        )


@router.get(
    "/ingest/sync/{job_id}/status",
    response_model=SyncJobStatusResponse,
    summary="Get sync job status",
    description="Poll the status of an async sync job by job ID.",
)
async def get_sync_status(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
) -> SyncJobStatusResponse:
    """Get the status of a sync job from Redis."""
    redis = await get_redis()
    job_key = f"sync_job:{job_id}"
    data = await redis.get(job_key)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sync job {job_id} not found or expired",
        )

    job_data = json.loads(data)

    result_data = job_data.get("result")
    result_response = None
    if result_data:
        result_response = IngestionResultResponse(
            inserted=result_data.get("inserted", 0),
            updated=result_data.get("updated", 0),
            skipped=result_data.get("skipped", 0),
            total=result_data.get("total", 0),
            errors=result_data.get("errors", []),
        )

    return SyncJobStatusResponse(
        job_id=UUID(job_data["job_id"]),
        account_id=UUID(job_data["account_id"]) if job_data.get("account_id") else None,
        status=SyncStatus(job_data["status"]),
        started_at=datetime.fromisoformat(job_data["started_at"]) if job_data.get("started_at") else None,
        completed_at=datetime.fromisoformat(job_data["completed_at"]) if job_data.get("completed_at") else None,
        result=result_response,
        error=job_data.get("error"),
    )


# ---------------------------------------------------------------------------
# Accounts endpoints
# ---------------------------------------------------------------------------

@accounts_router.get(
    "/accounts",
    response_model=CloudAccountListResponse,
    summary="List cloud accounts",
    description="Return all cloud accounts for the current tenant with last_sync_at and record counts.",
)
async def list_accounts(
    current_user: User = Depends(require_role("viewer", "analyst", "engineer", "admin")),
    db: AsyncSession = Depends(get_db),
) -> CloudAccountListResponse:
    """List all cloud accounts for the current tenant."""
    # Get accounts with record counts via subquery
    accounts_result = await db.execute(
        select(CloudAccount).where(
            CloudAccount.tenant_id == current_user.tenant_id,
            CloudAccount.status != "inactive",
        ).order_by(CloudAccount.created_at.desc())
    )
    accounts = accounts_result.scalars().all()

    # Get record counts per account
    counts_result = await db.execute(
        text("""
            SELECT cloud_account_id, COUNT(*) as cnt
            FROM focus_records
            WHERE tenant_id = :tenant_id
            GROUP BY cloud_account_id
        """),
        {"tenant_id": str(current_user.tenant_id)},
    )
    counts = {str(row.cloud_account_id): row.cnt for row in counts_result}

    account_responses = []
    for account in accounts:
        account_responses.append(
            CloudAccountResponse(
                id=account.id,
                tenant_id=account.tenant_id,
                name=account.name,
                provider=account.provider,
                account_identifier=account.account_identifier,
                status=account.status,
                config=account.config or {},
                credentials_ref=account.credentials_ref,
                last_sync_at=account.last_sync_at,
                created_at=account.created_at,
                updated_at=account.updated_at,
                record_count=counts.get(str(account.id), 0),
            )
        )

    return CloudAccountListResponse(accounts=account_responses, total=len(account_responses))


@accounts_router.post(
    "/accounts",
    response_model=CloudAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a cloud account connection",
    description=(
        "Create a new cloud account connection. "
        "Validates credentials by making a test API call to the provider. "
        "Raw credentials are not stored; only a reference is kept."
    ),
)
async def create_account(
    body: CloudAccountCreate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> CloudAccountResponse:
    """Create a new cloud account connection."""
    # Validate credentials
    validation = await _validate_provider_credentials(body)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Credential validation failed: {validation.message}",
        )

    # Store credentials reference (hash or secret manager key)
    credentials_ref = _store_credentials_ref(body.provider, body.credentials)

    account = CloudAccount(
        tenant_id=current_user.tenant_id,
        name=body.name,
        provider=body.provider.value,
        account_identifier=body.account_identifier,
        status="active",
        config=body.config,
        credentials_ref=credentials_ref,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    return CloudAccountResponse(
        id=account.id,
        tenant_id=account.tenant_id,
        name=account.name,
        provider=account.provider,
        account_identifier=account.account_identifier,
        status=account.status,
        config=account.config or {},
        credentials_ref=account.credentials_ref,
        last_sync_at=account.last_sync_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
        record_count=0,
    )


@accounts_router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a cloud account",
    description="Soft-delete a cloud account (sets status to 'inactive'). Writes an audit log entry.",
)
async def delete_account(
    account_id: UUID,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a cloud account."""
    account_result = await db.execute(
        select(CloudAccount).where(
            CloudAccount.id == account_id,
            CloudAccount.tenant_id == current_user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cloud account {account_id} not found",
        )

    # Soft delete
    await db.execute(
        update(CloudAccount)
        .where(CloudAccount.id == account_id)
        .values(status="inactive", updated_at=datetime.now(tz=timezone.utc))
    )

    # Audit log
    await db.execute(
        text("""
            INSERT INTO audit_logs (
                id, tenant_id, user_id, action,
                resource_type, resource_id, before_state, created_at
            ) VALUES (
                gen_random_uuid(), :tenant_id, :user_id, 'delete_account',
                'cloud_accounts', :account_id,
                :before_state::jsonb, now()
            )
        """),
        {
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.id),
            "account_id": str(account_id),
            "before_state": json.dumps({"name": account.name, "provider": account.provider}),
        },
    )

    await db.commit()


# ---------------------------------------------------------------------------
# Credential validation helpers
# ---------------------------------------------------------------------------

async def _validate_provider_credentials(body: CloudAccountCreate) -> CredentialValidationResult:
    """
    Make a lightweight test API call to validate provider credentials.
    Returns a CredentialValidationResult indicating success or failure.
    """
    provider = body.provider.value
    creds = body.credentials
    config = body.config

    try:
        if provider == "aws":
            import boto3
            sts = boto3.client(
                "sts",
                aws_access_key_id=creds.get("aws_access_key_id"),
                aws_secret_access_key=creds.get("aws_secret_access_key"),
                aws_session_token=creds.get("aws_session_token"),
            )
            identity = sts.get_caller_identity()
            return CredentialValidationResult(
                valid=True,
                provider=body.provider,
                account_identifier=identity.get("Account"),
                message="AWS credentials valid",
            )

        elif provider == "azure":
            from azure.identity import ClientSecretCredential
            from azure.mgmt.resource import SubscriptionClient

            credential = ClientSecretCredential(
                tenant_id=creds.get("azure_tenant_id", ""),
                client_id=creds.get("azure_client_id", ""),
                client_secret=creds.get("azure_client_secret", ""),
            )
            sub_client = SubscriptionClient(credential)
            sub_id = config.get("subscription_id") or body.account_identifier
            sub = sub_client.subscriptions.get(sub_id)
            return CredentialValidationResult(
                valid=True,
                provider=body.provider,
                account_identifier=sub.subscription_id,
                message="Azure credentials valid",
            )

        elif provider == "gcp":
            from google.oauth2 import service_account
            from google.cloud import resourcemanager_v3

            sa_info = creds.get("service_account_json")
            if sa_info:
                import json as _json
                sa_dict = _json.loads(sa_info) if isinstance(sa_info, str) else sa_info
                gcp_creds = service_account.Credentials.from_service_account_info(
                    sa_dict,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                rm_client = resourcemanager_v3.ProjectsClient(credentials=gcp_creds)
            else:
                rm_client = resourcemanager_v3.ProjectsClient()

            project_id = config.get("project_id") or body.account_identifier
            project = rm_client.get_project(name=f"projects/{project_id}")
            return CredentialValidationResult(
                valid=True,
                provider=body.provider,
                account_identifier=project.project_id,
                message="GCP credentials valid",
            )

        elif provider == "alibaba":
            from aliyunsdkcore.client import AcsClient
            from aliyunsdksts.request.v20150401 import GetCallerIdentityRequest

            ali_client = AcsClient(
                creds.get("access_key_id", ""),
                creds.get("access_key_secret", ""),
                config.get("region_id", "cn-hangzhou"),
            )
            request = GetCallerIdentityRequest.GetCallerIdentityRequest()
            import json as _json
            response = _json.loads(ali_client.do_action_with_exception(request))
            return CredentialValidationResult(
                valid=True,
                provider=body.provider,
                account_identifier=response.get("AccountId"),
                message="Alibaba Cloud credentials valid",
            )

        else:
            return CredentialValidationResult(
                valid=False,
                provider=body.provider,
                message=f"Unknown provider: {provider}",
            )

    except Exception as exc:
        return CredentialValidationResult(
            valid=False,
            provider=body.provider,
            message=str(exc),
        )


def _store_credentials_ref(provider: str, credentials: dict) -> str | None:
    """
    Store credentials securely and return a reference string.

    In production this would store to AWS Secrets Manager / Azure Key Vault / GCP Secret Manager.
    For now, returns a hashed reference for identification purposes.
    """
    import hashlib
    import json as _json

    if not credentials:
        return None

    # Create a deterministic reference based on credential keys (not values)
    key_fingerprint = hashlib.sha256(
        _json.dumps(sorted(credentials.keys())).encode()
    ).hexdigest()[:16]

    return f"cloudcarbon/{provider}/{key_fingerprint}"
