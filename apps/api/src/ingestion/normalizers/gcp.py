"""
GCP BigQuery billing export normalizer.

Converts GCP BigQuery billing export rows (as dicts) to FOCUS 1.0 FocusRecord objects.
GCP billing exports costs in USD natively.

BigQuery billing export schema reference:
  https://cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from focus_schema.models import ChargeCategory, FocusRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCP type → FOCUS ChargeCategory
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, ChargeCategory] = {
    "REGULAR": ChargeCategory.USAGE,
    "TAX": ChargeCategory.TAX,
    "ADJUSTMENT": ChargeCategory.ADJUSTMENT,
    "ROUNDING_ERROR": ChargeCategory.ADJUSTMENT,
    "CREDIT": ChargeCategory.CREDIT,
    "PROMOTION": ChargeCategory.ADJUSTMENT,
    "COMMITTED_USAGE_DISCOUNT": ChargeCategory.PURCHASE,
    "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE": ChargeCategory.PURCHASE,
    "SUSTAINED_USAGE_DISCOUNT": ChargeCategory.ADJUSTMENT,
    "FREE_TIER": ChargeCategory.ADJUSTMENT,
}

# ---------------------------------------------------------------------------
# GCP service description → ServiceCategory
# ---------------------------------------------------------------------------

_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "Compute Engine": "Compute",
    "Google Kubernetes Engine": "Compute",
    "Cloud Run": "Compute",
    "Cloud Functions": "Compute",
    "App Engine": "Compute",
    "Cloud Storage": "Storage",
    "Filestore": "Storage",
    "Persistent Disk": "Storage",
    "Cloud SQL": "Database",
    "Cloud Spanner": "Database",
    "Cloud Bigtable": "Database",
    "Firestore": "Database",
    "Memorystore": "Database",
    "BigQuery": "Analytics",
    "Dataflow": "Analytics",
    "Dataproc": "Analytics",
    "Pub/Sub": "Analytics",
    "Cloud Composer": "Analytics",
    "Virtual Private Cloud": "Networking",
    "Cloud Load Balancing": "Networking",
    "Cloud CDN": "Networking",
    "Cloud Interconnect": "Networking",
    "Cloud DNS": "Networking",
    "Vertex AI": "AI and Machine Learning",
    "Cloud Natural Language API": "AI and Machine Learning",
    "Cloud Vision API": "AI and Machine Learning",
    "Cloud Translation API": "AI and Machine Learning",
    "Cloud Monitoring": "Management and Governance",
    "Cloud Logging": "Management and Governance",
    "Cloud Trace": "Management and Governance",
    "Cloud Identity and Access Management": "Security, Identity, and Compliance",
    "Cloud Key Management Service": "Security, Identity, and Compliance",
    "Security Command Center": "Security, Identity, and Compliance",
}

# ---------------------------------------------------------------------------
# GCP region → human-readable name
# ---------------------------------------------------------------------------

_REGION_NAME_MAP: dict[str, str] = {
    "us-central1": "US Central (Iowa)",
    "us-east1": "US East (South Carolina)",
    "us-east4": "US East (Northern Virginia)",
    "us-east5": "US East (Columbus)",
    "us-south1": "US South (Dallas)",
    "us-west1": "US West (Oregon)",
    "us-west2": "US West (Los Angeles)",
    "us-west3": "US West (Salt Lake City)",
    "us-west4": "US West (Las Vegas)",
    "northamerica-northeast1": "Canada (Montréal)",
    "northamerica-northeast2": "Canada (Toronto)",
    "southamerica-east1": "South America (São Paulo)",
    "southamerica-west1": "South America (Santiago)",
    "europe-west1": "Europe (Belgium)",
    "europe-west2": "Europe (London)",
    "europe-west3": "Europe (Frankfurt)",
    "europe-west4": "Europe (Netherlands)",
    "europe-west6": "Europe (Zürich)",
    "europe-west8": "Europe (Milan)",
    "europe-west9": "Europe (Paris)",
    "europe-west10": "Europe (Berlin)",
    "europe-west12": "Europe (Turin)",
    "europe-north1": "Europe (Finland)",
    "europe-central2": "Europe (Warsaw)",
    "europe-southwest1": "Europe (Madrid)",
    "asia-east1": "Asia Pacific (Taiwan)",
    "asia-east2": "Asia Pacific (Hong Kong)",
    "asia-northeast1": "Asia Pacific (Tokyo)",
    "asia-northeast2": "Asia Pacific (Osaka)",
    "asia-northeast3": "Asia Pacific (Seoul)",
    "asia-south1": "Asia Pacific (Mumbai)",
    "asia-south2": "Asia Pacific (Delhi)",
    "asia-southeast1": "Asia Pacific (Singapore)",
    "asia-southeast2": "Asia Pacific (Jakarta)",
    "australia-southeast1": "Australia (Sydney)",
    "australia-southeast2": "Australia (Melbourne)",
    "me-central1": "Middle East (Doha)",
    "me-west1": "Middle East (Tel Aviv)",
    "africa-south1": "Africa (Johannesburg)",
}


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    """Parse GCP datetime fields (ISO strings or datetime objects)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        value = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _extract_labels(labels: Any) -> dict[str, str] | None:
    """
    Extract GCP labels from various formats:
    - List of {"key": ..., "value": ...} dicts (BigQuery export format)
    - Plain dict
    """
    if not labels:
        return None
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items() if v is not None}
    if isinstance(labels, list):
        result: dict[str, str] = {}
        for item in labels:
            if isinstance(item, dict) and "key" in item:
                result[str(item["key"])] = str(item.get("value", ""))
        return result if result else None
    if isinstance(labels, str):
        try:
            parsed = json.loads(labels)
            return _extract_labels(parsed)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _get_nested(row: dict, *keys: str) -> Any:
    """Safely navigate nested dict keys."""
    current = row
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def normalize_gcp_row(row: dict) -> FocusRecord | None:
    """
    Normalize a single GCP BigQuery billing export row to a FocusRecord.

    The row may be a flat dict (from JSON export) or a nested dict
    (from BigQuery API with RECORD fields).
    """
    # Cost
    cost = _parse_float(row.get("cost")) or 0.0

    # Credits reduce cost; sum them
    credits = row.get("credits", [])
    if isinstance(credits, list):
        credit_total = sum(
            _parse_float(c.get("amount", 0)) or 0.0
            for c in credits
            if isinstance(c, dict)
        )
        cost += credit_total  # credits are negative in GCP exports

    # Charge type
    charge_type = row.get("type", "REGULAR").upper()
    charge_category = _TYPE_MAP.get(charge_type, ChargeCategory.USAGE)

    # Service
    service_desc = (
        _get_nested(row, "service", "description")
        or row.get("service_description")
        or row.get("service.description")
        or "Unknown"
    )
    service_category = _SERVICE_CATEGORY_MAP.get(service_desc, "Other")

    # SKU
    sku_desc = (
        _get_nested(row, "sku", "description")
        or row.get("sku_description")
        or row.get("sku.description")
    )
    sku_id = (
        _get_nested(row, "sku", "id")
        or row.get("sku_id")
        or row.get("sku.id")
    )

    # Region
    region_id = (
        _get_nested(row, "location", "region")
        or row.get("location_region")
        or row.get("location.region")
    )
    region_name = _REGION_NAME_MAP.get(region_id or "", region_id)

    # Dates
    usage_start = _parse_datetime(
        _get_nested(row, "usage_start_time")
        or row.get("usage_start_time")
    )
    usage_end = _parse_datetime(
        _get_nested(row, "usage_end_time")
        or row.get("usage_end_time")
    )
    export_time = _parse_datetime(row.get("export_time"))

    billing_start = usage_start or export_time or datetime.now(tz=timezone.utc)
    billing_end = usage_end or billing_start

    # Resource
    resource_name = (
        _get_nested(row, "resource", "name")
        or row.get("resource_name")
        or row.get("resource.name")
    )
    resource_id = (
        _get_nested(row, "resource", "global_name")
        or row.get("resource_global_name")
        or row.get("resource.global_name")
        or resource_name
    )

    # Usage
    usage_amount = _parse_float(
        _get_nested(row, "usage", "amount")
        or row.get("usage_amount")
        or row.get("usage.amount")
    )
    usage_unit = (
        _get_nested(row, "usage", "unit")
        or row.get("usage_unit")
        or row.get("usage.unit")
    )

    # Sub-account (project)
    project_id = (
        _get_nested(row, "project", "id")
        or row.get("project_id")
        or row.get("project.id")
    )
    project_name = (
        _get_nested(row, "project", "name")
        or row.get("project_name")
        or row.get("project.name")
    )

    # Labels
    labels = row.get("labels") or _get_nested(row, "project", "labels")
    tags = _extract_labels(labels)

    return FocusRecord(
        BillingPeriodStart=billing_start,
        BillingPeriodEnd=billing_end,
        ChargeCategory=charge_category,
        EffectiveCost=cost,
        InvoiceIssuerName="Google Cloud Platform",
        ProviderName="Google Cloud Platform",
        ServiceCategory=service_category,
        ServiceName=service_desc,
        ChargePeriodStart=usage_start,
        ChargePeriodEnd=usage_end,
        ConsumedQuantity=usage_amount,
        ConsumedUnit=usage_unit,
        RegionId=region_id,
        RegionName=region_name,
        ResourceId=resource_id,
        ResourceName=resource_name,
        SkuId=sku_id,
        SkuPriceId=sku_desc,
        SubAccountId=project_id,
        SubAccountName=project_name,
        Tags=tags,
        PublisherName="Google",
    )


def parse_gcp_json(file_path: str) -> list[FocusRecord]:
    """
    Parse a GCP BigQuery billing export JSON file.

    Args:
        file_path: Path to a JSON file containing a list of billing export rows.

    Returns:
        List of FocusRecord objects.
    """
    import json as _json

    path = Path(file_path)
    records: list[FocusRecord] = []
    errors = 0

    with open(path, encoding="utf-8") as f:
        data = _json.load(f)

    if not isinstance(data, list):
        data = [data]

    for i, row in enumerate(data):
        try:
            record = normalize_gcp_row(row)
            if record is not None:
                records.append(record)
        except Exception as exc:
            errors += 1
            logger.warning("GCP row %d failed: %s", i, exc)

    logger.info(
        "GCP export parsed: %d records, %d errors from %s",
        len(records), errors, file_path,
    )
    return records
