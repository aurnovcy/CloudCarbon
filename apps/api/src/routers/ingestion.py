"""
Ingestion and cloud accounts API router (synchronous).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from uuid import UUID

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from src.database import get_db, SessionLocal
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

PROVIDER_EXTENSIONS = {".csv": None, ".parquet": None, ".pq": None, ".json": "gcp"}


def _detect_provider(filename: str, explicit_provider: str | None) -> str | None:
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
    return None


@router.post("/ingest/upload", response_model=IngestionResultResponse)
def upload_billing_file(
    file: UploadFile = File(...),
    account_id: UUID = Query(...),
    provider: str | None = Query(None),
    current_user: User = Depends(require_role("analyst")),
    db: Session = Depends(get_db),
) -> IngestionResultResponse:
    import tempfile, os

    filename = file.filename or "upload.csv"
    detected_provider = _detect_provider(filename, provider)

    account_result = db.execute(
        select(CloudAccount).where(
            CloudAccount.id == account_id,
            CloudAccount.tenant_id == current_user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cloud account {account_id} not found")

    content = file.file.read()
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".csv"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if detected_provider == "aws":
            records = parse_aws_csv(tmp_path)
            result = ingest_focus_records(records, current_user.tenant_id, account_id, db)
        elif detected_provider == "azure":
            records = parse_azure_csv(tmp_path)
            result = ingest_focus_records(records, current_user.tenant_id, account_id, db)
        elif detected_provider == "gcp":
            records = parse_gcp_json(tmp_path)
            result = ingest_focus_records(records, current_user.tenant_id, account_id, db)
        else:
            result = handle_focus_upload(tmp_path, filename, current_user.tenant_id, account_id, db)
    finally:
        os.unlink(tmp_path)

    return IngestionResultResponse(
        inserted=result.inserted, updated=result.updated, skipped=result.skipped,
        total=result.total, errors=result.errors,
        validation_errors=[ValidationErrorDetail(**e) for e in result.validation_errors],
    )


@router.post("/ingest/sync/{account_id}", response_model=SyncJobResponse)
def trigger_sync(
    account_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role("engineer")),
    db: Session = Depends(get_db),
) -> SyncJobResponse:
    account_result = db.execute(
        select(CloudAccount).where(
            CloudAccount.id == account_id,
            CloudAccount.tenant_id == current_user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cloud account {account_id} not found")

    job_id = uuid.uuid4()
    redis_client = get_redis()
    job_key = f"sync_job:{job_id}"
    redis_client.setex(job_key, 86400, json.dumps({
        "job_id": str(job_id), "account_id": str(account_id),
        "status": SyncStatus.PENDING,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "completed_at": None, "result": None, "error": None,
    }))

    background_tasks.add_task(_run_sync_background, account=account, job_id=job_id, tenant_id=current_user.tenant_id)
    return SyncJobResponse(status="sync_started", account_id=account_id, job_id=job_id)


def _run_sync_background(account: CloudAccount, job_id: UUID, tenant_id: UUID) -> None:
    redis_client = get_redis()
    job_key = f"sync_job:{job_id}"

    redis_client.setex(job_key, 86400, json.dumps({
        "job_id": str(job_id), "account_id": str(account.id),
        "status": SyncStatus.RUNNING,
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "completed_at": None, "result": None, "error": None,
    }))

    db = SessionLocal()
    try:
        result = sync_account_by_provider(account, db)
        db.execute(update(CloudAccount).where(CloudAccount.id == account.id).values(last_sync_at=datetime.now(tz=timezone.utc)))
        db.commit()

        redis_client.setex(job_key, 86400, json.dumps({
            "job_id": str(job_id), "account_id": str(account.id),
            "status": SyncStatus.COMPLETED,
            "started_at": None, "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "result": result.to_dict(), "error": None,
        }))
    except Exception as exc:
        logger.error("Background sync failed for job %s: %s", job_id, exc)
        redis_client.setex(job_key, 86400, json.dumps({
            "job_id": str(job_id), "account_id": str(account.id),
            "status": SyncStatus.FAILED,
            "started_at": None, "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "result": None, "error": str(exc),
        }))
    finally:
        db.close()


@router.get("/ingest/sync/{job_id}/status", response_model=SyncJobStatusResponse)
def get_sync_status(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
) -> SyncJobStatusResponse:
    redis_client = get_redis()
    job_key = f"sync_job:{job_id}"
    data = redis_client.get(job_key)

    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sync job {job_id} not found or expired")

    job_data = json.loads(data)
    result_data = job_data.get("result")
    result_response = None
    if result_data:
        result_response = IngestionResultResponse(
            inserted=result_data.get("inserted", 0), updated=result_data.get("updated", 0),
            skipped=result_data.get("skipped", 0), total=result_data.get("total", 0),
            errors=result_data.get("errors", []),
        )

    return SyncJobStatusResponse(
        job_id=UUID(job_data["job_id"]),
        account_id=UUID(job_data["account_id"]) if job_data.get("account_id") else None,
        status=SyncStatus(job_data["status"]),
        started_at=datetime.fromisoformat(job_data["started_at"]) if job_data.get("started_at") else None,
        completed_at=datetime.fromisoformat(job_data["completed_at"]) if job_data.get("completed_at") else None,
        result=result_response, error=job_data.get("error"),
    )


@accounts_router.get("/accounts", response_model=CloudAccountListResponse)
def list_accounts(
    current_user: User = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
) -> CloudAccountListResponse:
    accounts_result = db.execute(
        select(CloudAccount).where(
            CloudAccount.tenant_id == current_user.tenant_id,
            CloudAccount.status != "inactive",
        ).order_by(CloudAccount.created_at.desc())
    )
    accounts = accounts_result.scalars().all()

    counts_result = db.execute(
        text("SELECT cloud_account_id, COUNT(*) as cnt FROM focus_records WHERE tenant_id = :tenant_id GROUP BY cloud_account_id"),
        {"tenant_id": str(current_user.tenant_id)},
    )
    counts = {str(row.cloud_account_id): row.cnt for row in counts_result}

    account_responses = [
        CloudAccountResponse(
            id=account.id, tenant_id=account.tenant_id, name=account.name,
            provider=account.provider, account_identifier=account.account_identifier,
            status=account.status, config=account.config or {},
            credentials_ref=account.credentials_ref, last_sync_at=account.last_sync_at,
            created_at=account.created_at, updated_at=account.updated_at,
            record_count=counts.get(str(account.id), 0),
        )
        for account in accounts
    ]
    return CloudAccountListResponse(accounts=account_responses, total=len(account_responses))


@accounts_router.post("/accounts", response_model=CloudAccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(
    body: CloudAccountCreate,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> CloudAccountResponse:
    validation = _validate_provider_credentials(body)
    if not validation.valid:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Credential validation failed: {validation.message}")

    credentials_ref = _store_credentials_ref(body.provider, body.credentials)
    account = CloudAccount(
        tenant_id=current_user.tenant_id, name=body.name,
        provider=body.provider.value, account_identifier=body.account_identifier,
        status="active", config=body.config, credentials_ref=credentials_ref,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return CloudAccountResponse(
        id=account.id, tenant_id=account.tenant_id, name=account.name,
        provider=account.provider, account_identifier=account.account_identifier,
        status=account.status, config=account.config or {},
        credentials_ref=account.credentials_ref, last_sync_at=account.last_sync_at,
        created_at=account.created_at, updated_at=account.updated_at, record_count=0,
    )


@accounts_router.delete("/accounts/{account_id}", status_code=status.HTTP_200_OK)
def delete_account(
    account_id: UUID,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    account_result = db.execute(
        select(CloudAccount).where(CloudAccount.id == account_id, CloudAccount.tenant_id == current_user.tenant_id)
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Cloud account {account_id} not found")

    db.execute(update(CloudAccount).where(CloudAccount.id == account_id).values(status="inactive", updated_at=datetime.now(tz=timezone.utc)))
    db.execute(text("""
        INSERT INTO audit_logs (id, tenant_id, user_id, action, resource_type, resource_id, before_state, created_at)
        VALUES (gen_random_uuid(), :tenant_id, :user_id, 'delete_account', 'cloud_accounts', :account_id, CAST(:before_state AS jsonb), now())
    """), {
        "tenant_id": str(current_user.tenant_id), "user_id": str(current_user.id),
        "account_id": str(account_id),
        "before_state": json.dumps({"name": account.name, "provider": account.provider}),
    })
    db.commit()
    return {"status": "deleted", "account_id": str(account_id)}


def _validate_provider_credentials(body: CloudAccountCreate) -> CredentialValidationResult:
    provider = body.provider.value
    creds = body.credentials
    config = body.config
    try:
        if provider == "aws":
            import boto3
            sts = boto3.client("sts", aws_access_key_id=creds.get("aws_access_key_id"),
                               aws_secret_access_key=creds.get("aws_secret_access_key"),
                               aws_session_token=creds.get("aws_session_token"))
            identity = sts.get_caller_identity()
            return CredentialValidationResult(valid=True, provider=body.provider, account_identifier=identity.get("Account"), message="AWS credentials valid")
        elif provider == "azure":
            from azure.identity import ClientSecretCredential
            from azure.mgmt.resource import SubscriptionClient
            credential = ClientSecretCredential(tenant_id=creds.get("azure_tenant_id", ""), client_id=creds.get("azure_client_id", ""), client_secret=creds.get("azure_client_secret", ""))
            sub_client = SubscriptionClient(credential)
            sub_id = config.get("subscription_id") or body.account_identifier
            sub = sub_client.subscriptions.get(sub_id)
            return CredentialValidationResult(valid=True, provider=body.provider, account_identifier=sub.subscription_id, message="Azure credentials valid")
        else:
            return CredentialValidationResult(valid=False, provider=body.provider, message=f"Unsupported provider for validation: {provider}")
    except Exception as exc:
        return CredentialValidationResult(valid=False, provider=body.provider, message=str(exc))


def _store_credentials_ref(provider: str, credentials: dict) -> str | None:
    import hashlib
    import json as _json
    if not credentials:
        return None
    key_fingerprint = hashlib.sha256(_json.dumps(sorted(credentials.keys())).encode()).hexdigest()[:16]
    return f"cloudcarbon/{provider}/{key_fingerprint}"
