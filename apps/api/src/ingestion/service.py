"""
IngestionService — core ingestion logic for CloudCarbon.

Responsibilities:
  - Bulk upsert FocusRecord objects into the focus_records table
  - Sync cloud accounts (AWS/Azure/GCP/Alibaba) by fetching and normalizing billing data
  - Handle FOCUS file uploads (CSV/Parquet passthrough)
  - Write audit log entries for all ingestion operations
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from focus_schema.models import FocusRecord
from src.ingestion.normalizers.focus_passthrough import (
    RowValidationError,
    parse_focus_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    validation_errors: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated

    def to_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "total": self.total,
            "errors": self.errors,
            "validation_errors": self.validation_errors,
        }


# ---------------------------------------------------------------------------
# Core upsert
# ---------------------------------------------------------------------------

async def ingest_focus_records(
    records: list[FocusRecord],
    tenant_id: UUID,
    cloud_account_id: UUID,
    db: AsyncSession,
) -> IngestionResult:
    """
    Bulk upsert FocusRecord objects into the focus_records table.

    Upsert key: (tenant_id, cloud_account_id, billing_period_start, resource_id, service_name)

    Writes an audit log entry after successful ingestion.
    """
    result = IngestionResult()

    if not records:
        return result

    # Build upsert batches (PostgreSQL supports up to ~65535 params per query)
    BATCH_SIZE = 500
    batches = [records[i : i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]

    for batch in batches:
        rows = []
        for rec in batch:
            db_dict = rec.to_db_dict()
            db_dict["tenant_id"] = str(tenant_id)
            db_dict["cloud_account_id"] = str(cloud_account_id)
            rows.append(db_dict)

        try:
            # Use raw SQL for performance; SQLAlchemy ORM upsert is verbose
            upsert_sql = text("""
                INSERT INTO focus_records (
                    id, tenant_id, cloud_account_id,
                    billing_period_start, billing_period_end,
                    provider, service_name, service_category,
                    region, resource_id, resource_type,
                    usage_quantity, usage_unit,
                    cost_usd, list_cost_usd, currency,
                    tags, raw_data, ingested_at
                )
                VALUES (
                    gen_random_uuid(), :tenant_id, :cloud_account_id,
                    :billing_period_start, :billing_period_end,
                    :provider, :service_name, :service_category,
                    :region, :resource_id, :resource_type,
                    :usage_quantity, :usage_unit,
                    :cost_usd, :list_cost_usd, :currency,
                    :tags::jsonb, :raw_data::jsonb, now()
                )
                ON CONFLICT (tenant_id, cloud_account_id, billing_period_start, resource_id, service_name)
                DO UPDATE SET
                    billing_period_end = EXCLUDED.billing_period_end,
                    service_category = EXCLUDED.service_category,
                    region = EXCLUDED.region,
                    resource_type = EXCLUDED.resource_type,
                    usage_quantity = EXCLUDED.usage_quantity,
                    usage_unit = EXCLUDED.usage_unit,
                    cost_usd = EXCLUDED.cost_usd,
                    list_cost_usd = EXCLUDED.list_cost_usd,
                    currency = EXCLUDED.currency,
                    tags = EXCLUDED.tags,
                    raw_data = EXCLUDED.raw_data,
                    ingested_at = now()
                RETURNING xmax
            """)

            for row in rows:
                # Serialize JSONB fields
                row["tags"] = json.dumps(row.get("tags") or {})
                row["raw_data"] = json.dumps(row.get("raw_data") or {})
                # resource_id may be None; use empty string for upsert key
                if row.get("resource_id") is None:
                    row["resource_id"] = ""

                res = await db.execute(upsert_sql, row)
                xmax = res.scalar()
                # xmax == 0 means INSERT; xmax > 0 means UPDATE
                if xmax == 0:
                    result.inserted += 1
                else:
                    result.updated += 1

        except Exception as exc:
            logger.error("Batch upsert failed: %s", exc)
            result.errors.append(str(exc))
            await db.rollback()
            return result

    # Write audit log
    try:
        await db.execute(
            text("""
                INSERT INTO audit_logs (
                    id, tenant_id, user_id, action,
                    resource_type, resource_id, after_state, created_at
                ) VALUES (
                    gen_random_uuid(), :tenant_id, NULL, 'ingest_records',
                    'focus_records', :cloud_account_id,
                    :after_state::jsonb, now()
                )
            """),
            {
                "tenant_id": str(tenant_id),
                "cloud_account_id": str(cloud_account_id),
                "after_state": json.dumps({
                    "count": result.total,
                    "inserted": result.inserted,
                    "updated": result.updated,
                }),
            },
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Audit log write failed (non-fatal): %s", exc)
        await db.rollback()

    return result


# ---------------------------------------------------------------------------
# Provider sync methods
# ---------------------------------------------------------------------------

async def sync_aws_account(account: Any, db: AsyncSession) -> IngestionResult:
    """
    Sync an AWS cloud account by downloading the latest CUR from S3.

    account.config expected keys:
      s3_bucket: str       — S3 bucket containing the CUR
      s3_prefix: str       — S3 key prefix for the CUR files
      report_name: str     — CUR report name
      aws_region: str      — AWS region for S3 client (default: us-east-1)

    Credentials are resolved via the standard boto3 credential chain
    (environment variables, IAM role, or account.credentials_ref).
    """
    import boto3
    from src.ingestion.normalizers.aws import parse_aws_csv

    config = account.config or {}
    s3_bucket = config.get("s3_bucket")
    s3_prefix = config.get("s3_prefix", "")
    report_name = config.get("report_name", "")
    aws_region = config.get("aws_region", "us-east-1")

    if not s3_bucket:
        return IngestionResult(errors=["account.config.s3_bucket is required for AWS sync"])

    try:
        s3 = boto3.client("s3", region_name=aws_region)

        # List objects to find the latest manifest
        prefix = f"{s3_prefix}/{report_name}/" if report_name else s3_prefix
        response = s3.list_objects_v2(Bucket=s3_bucket, Prefix=prefix)
        objects = response.get("Contents", [])

        # Find the latest CSV file
        csv_objects = [
            obj for obj in objects
            if obj["Key"].endswith(".csv") or obj["Key"].endswith(".csv.gz")
        ]
        if not csv_objects:
            return IngestionResult(errors=[f"No CUR CSV files found in s3://{s3_bucket}/{prefix}"])

        latest = max(csv_objects, key=lambda o: o["LastModified"])

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            s3.download_fileobj(s3_bucket, latest["Key"], tmp)
            tmp_path = tmp.name

        records = parse_aws_csv(tmp_path)
        os.unlink(tmp_path)

    except Exception as exc:
        logger.error("AWS sync failed for account %s: %s", account.id, exc)
        return IngestionResult(errors=[str(exc)])

    return await ingest_focus_records(records, account.tenant_id, account.id, db)


async def sync_azure_account(account: Any, db: AsyncSession) -> IngestionResult:
    """
    Sync an Azure cloud account using the Azure Cost Management API.

    account.config expected keys:
      subscription_id: str
      resource_group: str  (optional)
      scope: str           (optional, overrides subscription_id)
      export_name: str     (optional, for scheduled exports)

    Credentials resolved via DefaultAzureCredential (env vars, managed identity, CLI).
    """
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.costmanagement import CostManagementClient
    from azure.mgmt.costmanagement.models import (
        ExportType,
        QueryDataset,
        QueryDefinition,
        QueryTimePeriod,
        TimeframeType,
    )
    from src.ingestion.normalizers.azure import normalize_azure_row

    config = account.config or {}
    subscription_id = config.get("subscription_id")
    scope = config.get("scope") or f"/subscriptions/{subscription_id}"

    if not subscription_id and not config.get("scope"):
        return IngestionResult(errors=["account.config.subscription_id is required for Azure sync"])

    try:
        credential = DefaultAzureCredential()
        client = CostManagementClient(credential)

        # Query last 30 days of usage
        from datetime import timedelta
        end_date = datetime.now(tz=timezone.utc).date()
        start_date = end_date - timedelta(days=30)

        query = QueryDefinition(
            type=ExportType.ACTUAL_COST,
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(
                from_property=start_date.isoformat(),
                to=end_date.isoformat(),
            ),
            dataset=QueryDataset(
                granularity="Daily",
                grouping=[
                    {"type": "Dimension", "name": "ServiceName"},
                    {"type": "Dimension", "name": "ResourceLocation"},
                    {"type": "Dimension", "name": "ResourceId"},
                    {"type": "Dimension", "name": "SubscriptionId"},
                ],
            ),
        )

        result_data = client.query.usage(scope, query)
        rows = result_data.rows or []
        columns = [col.name for col in (result_data.columns or [])]

        records: list[FocusRecord] = []
        for row_values in rows:
            row_dict = dict(zip(columns, row_values))
            try:
                rec = normalize_azure_row(row_dict)
                if rec:
                    records.append(rec)
            except Exception as exc:
                logger.warning("Azure row normalization failed: %s", exc)

    except Exception as exc:
        logger.error("Azure sync failed for account %s: %s", account.id, exc)
        return IngestionResult(errors=[str(exc)])

    return await ingest_focus_records(records, account.tenant_id, account.id, db)


async def sync_gcp_account(account: Any, db: AsyncSession) -> IngestionResult:
    """
    Sync a GCP cloud account by querying the BigQuery billing export dataset.

    account.config expected keys:
      project_id: str
      dataset_id: str      — BigQuery dataset containing billing export
      table_id: str        — Table name (default: gcp_billing_export_v1_*)
      days_back: int       — Number of days to query (default: 30)

    Credentials resolved via Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS
    env var, gcloud auth, or Workload Identity).
    """
    from google.cloud import bigquery
    from src.ingestion.normalizers.gcp import normalize_gcp_row

    config = account.config or {}
    project_id = config.get("project_id")
    dataset_id = config.get("dataset_id")
    table_id = config.get("table_id", "gcp_billing_export_v1")
    days_back = int(config.get("days_back", 30))

    if not project_id or not dataset_id:
        return IngestionResult(
            errors=["account.config.project_id and dataset_id are required for GCP sync"]
        )

    try:
        bq_client = bigquery.Client(project=project_id)
        table_ref = f"`{project_id}.{dataset_id}.{table_id}`"

        query = f"""
            SELECT *
            FROM {table_ref}
            WHERE DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL {days_back} DAY)
            ORDER BY usage_start_time DESC
        """

        query_job = bq_client.query(query)
        rows = list(query_job.result())

        records: list[FocusRecord] = []
        for bq_row in rows:
            row_dict = dict(bq_row.items())
            try:
                rec = normalize_gcp_row(row_dict)
                if rec:
                    records.append(rec)
            except Exception as exc:
                logger.warning("GCP row normalization failed: %s", exc)

    except Exception as exc:
        logger.error("GCP sync failed for account %s: %s", account.id, exc)
        return IngestionResult(errors=[str(exc)])

    return await ingest_focus_records(records, account.tenant_id, account.id, db)


async def sync_alibaba_account(account: Any, db: AsyncSession) -> IngestionResult:
    """
    Sync an Alibaba Cloud account using the BSS OpenAPI.

    account.config expected keys:
      access_key_id: str
      access_key_secret: str
      region_id: str       — Alibaba region for BSS API (default: cn-hangzhou)
      billing_cycle: str   — YYYY-MM format (default: current month)
      cny_to_usd: float    — CNY to USD exchange rate (default: 0.138)
    """
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkbssopenapi.request.v20171214 import QueryInstanceBillRequest
    from src.ingestion.normalizers.alibaba import parse_alibaba_json

    config = account.config or {}
    access_key_id = config.get("access_key_id") or os.environ.get("ALIBABA_ACCESS_KEY_ID", "")
    access_key_secret = config.get("access_key_secret") or os.environ.get("ALIBABA_ACCESS_KEY_SECRET", "")
    region_id = config.get("region_id", "cn-hangzhou")
    cny_to_usd = float(config.get("cny_to_usd", 0.138))

    # Default to current month
    billing_cycle = config.get("billing_cycle") or datetime.now(tz=timezone.utc).strftime("%Y-%m")

    if not access_key_id or not access_key_secret:
        return IngestionResult(
            errors=["Alibaba access_key_id and access_key_secret are required"]
        )

    try:
        ali_client = AcsClient(access_key_id, access_key_secret, region_id)

        request = QueryInstanceBillRequest.QueryInstanceBillRequest()
        request.set_BillingCycle(billing_cycle)
        request.set_IsBillingItem(False)
        request.set_PageSize(300)

        all_items: list[dict] = []
        page_num = 1

        while True:
            request.set_PageNum(page_num)
            response = ali_client.do_action_with_exception(request)
            data = json.loads(response)

            items = (
                data.get("Data", {})
                    .get("Items", {})
                    .get("Item", [])
            )
            all_items.extend(items)

            total_count = data.get("Data", {}).get("TotalCount", 0)
            if len(all_items) >= total_count:
                break
            page_num += 1

        records = parse_alibaba_json(all_items, cny_to_usd)

    except Exception as exc:
        logger.error("Alibaba sync failed for account %s: %s", account.id, exc)
        return IngestionResult(errors=[str(exc)])

    return await ingest_focus_records(records, account.tenant_id, account.id, db)


# ---------------------------------------------------------------------------
# File upload handler
# ---------------------------------------------------------------------------

async def handle_focus_upload(
    file: UploadFile,
    tenant_id: UUID,
    cloud_account_id: UUID,
    db: AsyncSession,
) -> IngestionResult:
    """
    Accept a FOCUS 1.0 CSV or Parquet file upload, validate, and ingest.

    If validation errors are found, they are returned in the result without
    ingesting any records (fail-fast mode).
    """
    filename = file.filename or "upload"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".csv"

    # Write to a temp file for parsing
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        records, validation_errors = parse_focus_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    result = IngestionResult()

    if validation_errors:
        result.validation_errors = [
            {
                "row": err.row_number,
                "field": err.field,
                "message": err.message,
            }
            for err in validation_errors
        ]
        result.errors.append(
            f"{len(validation_errors)} validation error(s) found; no records ingested."
        )
        return result

    return await ingest_focus_records(records, tenant_id, cloud_account_id, db)


# ---------------------------------------------------------------------------
# Convenience dispatcher
# ---------------------------------------------------------------------------

PROVIDER_SYNC_MAP = {
    "aws": sync_aws_account,
    "azure": sync_azure_account,
    "gcp": sync_gcp_account,
    "alibaba": sync_alibaba_account,
}


async def sync_account_by_provider(account: Any, db: AsyncSession) -> IngestionResult:
    """Dispatch to the correct provider sync method based on account.provider."""
    provider = (account.provider or "").lower()
    sync_fn = PROVIDER_SYNC_MAP.get(provider)
    if not sync_fn:
        return IngestionResult(errors=[f"Unknown provider: {provider}"])
    return await sync_fn(account, db)
