"""
AWS Cost and Usage Report (CUR) normalizer.

Converts AWS CUR CSV rows to FOCUS 1.0 FocusRecord objects.
All monetary values are already in USD in CUR exports.

CUR column reference:
  https://docs.aws.amazon.com/cur/latest/userguide/data-dictionary.html
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from focus_schema.models import ChargeCategory, FocusRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AWS LineItemType → FOCUS ChargeCategory mapping
# ---------------------------------------------------------------------------

_LINE_ITEM_TYPE_MAP: dict[str, ChargeCategory] = {
    "Usage": ChargeCategory.USAGE,
    "Tax": ChargeCategory.TAX,
    "Credit": ChargeCategory.CREDIT,
    "Refund": ChargeCategory.ADJUSTMENT,
    "Fee": ChargeCategory.PURCHASE,
    "RIFee": ChargeCategory.PURCHASE,
    "SavingsPlanRecurringFee": ChargeCategory.PURCHASE,
    "SavingsPlanCoveredUsage": ChargeCategory.USAGE,
    "SavingsPlanNegation": ChargeCategory.ADJUSTMENT,
    "DiscountedUsage": ChargeCategory.USAGE,
    "Discount": ChargeCategory.ADJUSTMENT,
    "BundledDiscount": ChargeCategory.ADJUSTMENT,
    "EdpDiscount": ChargeCategory.ADJUSTMENT,
    "PrivateRateDiscount": ChargeCategory.ADJUSTMENT,
    "SupportFee": ChargeCategory.PURCHASE,
}

# ---------------------------------------------------------------------------
# AWS ProductCode → ServiceCategory mapping
# ---------------------------------------------------------------------------

_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "AmazonEC2": "Compute",
    "AWSLambda": "Compute",
    "AmazonECS": "Compute",
    "AmazonEKS": "Compute",
    "AmazonLightsail": "Compute",
    "AmazonS3": "Storage",
    "AmazonEBS": "Storage",
    "AmazonEFS": "Storage",
    "AmazonGlacier": "Storage",
    "AmazonRDS": "Database",
    "AmazonDynamoDB": "Database",
    "AmazonElastiCache": "Database",
    "AmazonRedshift": "Database",
    "AmazonAurora": "Database",
    "AmazonVPC": "Networking",
    "AmazonCloudFront": "Networking",
    "AWSDirectConnect": "Networking",
    "AmazonRoute53": "Networking",
    "AWSDataTransfer": "Networking",
    "AmazonSageMaker": "AI and Machine Learning",
    "AmazonBedrock": "AI and Machine Learning",
    "AmazonRekognition": "AI and Machine Learning",
    "AWSGlue": "Analytics",
    "AmazonAthena": "Analytics",
    "AmazonEMR": "Analytics",
    "AmazonKinesis": "Analytics",
    "AWSCloudTrail": "Management and Governance",
    "AmazonCloudWatch": "Management and Governance",
    "AWSConfig": "Management and Governance",
    "AWSSecurityHub": "Security, Identity, and Compliance",
    "AmazonGuardDuty": "Security, Identity, and Compliance",
    "AWSKMS": "Security, Identity, and Compliance",
    "AWSIAMIdentityCenter": "Security, Identity, and Compliance",
}

# ---------------------------------------------------------------------------
# AWS region code → human-readable name
# ---------------------------------------------------------------------------

_REGION_NAME_MAP: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)",
    "eu-central-1": "Europe (Frankfurt)",
    "eu-north-1": "Europe (Stockholm)",
    "eu-south-1": "Europe (Milan)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "sa-east-1": "South America (São Paulo)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
    "us-gov-east-1": "AWS GovCloud (US-East)",
    "us-gov-west-1": "AWS GovCloud (US-West)",
}


def _parse_float(value: Any) -> float | None:
    """Safely parse a float from a CUR field."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string from CUR."""
    if not value:
        return None
    try:
        # CUR uses format: 2024-01-01T00:00:00Z
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_tags(row: dict[str, str]) -> dict[str, str]:
    """Extract resourceTags/* columns into a clean dict."""
    tags: dict[str, str] = {}
    for key, value in row.items():
        if key.startswith("resourceTags/") and value:
            tag_key = key.removeprefix("resourceTags/")
            # Remove aws: prefix for user tags
            tag_key = tag_key.removeprefix("user:")
            tags[tag_key] = value
    return tags


def _extract_region(row: dict[str, str]) -> tuple[str | None, str | None]:
    """Extract RegionId and RegionName from a CUR row."""
    region_id = row.get("product/region") or row.get("lineItem/AvailabilityZone")
    if region_id:
        # Strip AZ suffix (us-east-1a → us-east-1)
        if len(region_id) > 2 and region_id[-1].isalpha() and region_id[-2].isdigit():
            region_id = region_id[:-1]
        region_name = _REGION_NAME_MAP.get(region_id)
        return region_id, region_name
    return None, None


def normalize_aws_row(row: dict[str, str]) -> FocusRecord | None:
    """
    Normalize a single AWS CUR row dict to a FocusRecord.
    Returns None if the row should be skipped (e.g. header rows).
    """
    # Skip rows with no cost data
    blended_cost_str = row.get("lineItem/BlendedCost", "")
    if blended_cost_str == "" and row.get("lineItem/UnblendedCost", "") == "":
        return None

    product_code = row.get("lineItem/ProductCode", "")
    line_item_type = row.get("lineItem/LineItemType", "Usage")
    region_id, region_name = _extract_region(row)

    # Billing period
    billing_start_str = row.get("bill/BillingPeriodStartDate")
    billing_end_str = row.get("bill/BillingPeriodEndDate")
    billing_start = _parse_datetime(billing_start_str) or datetime.now(tz=timezone.utc)
    billing_end = _parse_datetime(billing_end_str) or billing_start

    # Charge period
    charge_start = _parse_datetime(row.get("lineItem/UsageStartDate"))
    charge_end = _parse_datetime(row.get("lineItem/UsageEndDate"))

    # Costs
    effective_cost = _parse_float(row.get("lineItem/BlendedCost")) or 0.0
    list_cost = _parse_float(row.get("lineItem/UnblendedCost"))

    # Usage
    consumed_quantity = _parse_float(row.get("lineItem/UsageAmount"))
    consumed_unit = row.get("pricing/unit") or row.get("lineItem/UsageType")

    # Charge category
    charge_category = _LINE_ITEM_TYPE_MAP.get(line_item_type, ChargeCategory.USAGE)

    # Service
    service_name = (
        row.get("product/ProductName")
        or product_code
        or "Unknown"
    )
    service_category = _SERVICE_CATEGORY_MAP.get(product_code, "Other")

    # Resource
    resource_id = row.get("lineItem/ResourceId") or None
    resource_type = row.get("product/instanceType") or None

    # SKU
    sku_id = row.get("lineItem/LineItemDescription") or None
    sku_price_id = row.get("pricing/RateId") or None

    # Sub-account
    sub_account_id = row.get("lineItem/UsageAccountId") or None
    sub_account_name = row.get("bill/PayerAccountId") or None

    # Tags
    tags = _extract_tags(row)

    return FocusRecord(
        BillingPeriodStart=billing_start,
        BillingPeriodEnd=billing_end,
        ChargeCategory=charge_category,
        EffectiveCost=effective_cost,
        InvoiceIssuerName="Amazon Web Services",
        ProviderName="Amazon Web Services",
        ServiceCategory=service_category,
        ServiceName=service_name,
        ChargePeriodStart=charge_start,
        ChargePeriodEnd=charge_end,
        ConsumedQuantity=consumed_quantity,
        ConsumedUnit=consumed_unit,
        ListCost=list_cost,
        ListUnitPrice=_parse_float(row.get("pricing/publicOnDemandRate")),
        RegionId=region_id,
        RegionName=region_name,
        ResourceId=resource_id,
        ResourceType=resource_type,
        SkuId=sku_id,
        SkuPriceId=sku_price_id,
        SubAccountId=sub_account_id,
        SubAccountName=sub_account_name,
        Tags=tags if tags else None,
        PublisherName="Amazon Web Services",
    )


def parse_aws_csv(file_path: str) -> list[FocusRecord]:
    """
    Parse an AWS Cost and Usage Report CSV file.

    Args:
        file_path: Path to the CUR CSV file (may be gzip-compressed).

    Returns:
        List of FocusRecord objects.
    """
    path = Path(file_path)
    records: list[FocusRecord] = []
    errors = 0

    open_fn = __import__("gzip").open if path.suffix == ".gz" else open
    mode = "rt"

    with open_fn(path, mode, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            try:
                record = normalize_aws_row(row)
                if record is not None:
                    records.append(record)
            except Exception as exc:
                errors += 1
                logger.warning("AWS CUR row %d failed: %s", i, exc)

    logger.info(
        "AWS CUR parsed: %d records, %d errors from %s",
        len(records), errors, file_path,
    )
    return records
