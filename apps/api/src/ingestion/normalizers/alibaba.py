"""
Alibaba Cloud billing API response normalizer.

Converts Alibaba Cloud BSS OpenAPI billing records to FOCUS 1.0 FocusRecord objects.
Alibaba costs are in CNY; conversion to USD uses a configurable FX rate.

BSS OpenAPI reference:
  https://www.alibabacloud.com/help/en/bss-openapi/latest/queryinstancebill
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from focus_schema.models import ChargeCategory, FocusRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default CNY → USD exchange rate (updated periodically via config)
# ---------------------------------------------------------------------------

DEFAULT_CNY_TO_USD = 0.138  # Approximate rate; override via config

# ---------------------------------------------------------------------------
# Alibaba ProductCode → ServiceName, ServiceCategory
# ---------------------------------------------------------------------------

_PRODUCT_MAP: dict[str, tuple[str, str]] = {
    "ecs": ("Elastic Compute Service", "Compute"),
    "ack": ("Container Service for Kubernetes", "Compute"),
    "fc": ("Function Compute", "Compute"),
    "sae": ("Serverless App Engine", "Compute"),
    "oss": ("Object Storage Service", "Storage"),
    "nas": ("Network Attached Storage", "Storage"),
    "disk": ("Cloud Disk", "Storage"),
    "rds": ("ApsaraDB RDS", "Database"),
    "mongodb": ("ApsaraDB for MongoDB", "Database"),
    "redis": ("ApsaraDB for Redis", "Database"),
    "polardb": ("PolarDB", "Database"),
    "hbase": ("ApsaraDB for HBase", "Database"),
    "odps": ("MaxCompute", "Analytics"),
    "dataworks": ("DataWorks", "Analytics"),
    "realtime-compute": ("Realtime Compute for Apache Flink", "Analytics"),
    "vpc": ("Virtual Private Cloud", "Networking"),
    "slb": ("Server Load Balancer", "Networking"),
    "cdn": ("Content Delivery Network", "Networking"),
    "expressconnect": ("Express Connect", "Networking"),
    "eip": ("Elastic IP Address", "Networking"),
    "ddos": ("Anti-DDoS", "Security, Identity, and Compliance"),
    "waf": ("Web Application Firewall", "Security, Identity, and Compliance"),
    "kms": ("Key Management Service", "Security, Identity, and Compliance"),
    "ram": ("Resource Access Management", "Security, Identity, and Compliance"),
    "cms": ("Cloud Monitor Service", "Management and Governance"),
    "actiontrail": ("ActionTrail", "Management and Governance"),
    "pai": ("Platform for AI", "AI and Machine Learning"),
    "nlp": ("Natural Language Processing", "AI and Machine Learning"),
}

# ---------------------------------------------------------------------------
# Alibaba region code → RegionId, RegionName
# ---------------------------------------------------------------------------

_REGION_MAP: dict[str, tuple[str, str]] = {
    "cn-hangzhou": ("cn-hangzhou", "China (Hangzhou)"),
    "cn-shanghai": ("cn-shanghai", "China (Shanghai)"),
    "cn-beijing": ("cn-beijing", "China (Beijing)"),
    "cn-shenzhen": ("cn-shenzhen", "China (Shenzhen)"),
    "cn-zhangjiakou": ("cn-zhangjiakou", "China (Zhangjiakou)"),
    "cn-huhehaote": ("cn-huhehaote", "China (Hohhot)"),
    "cn-wulanchabu": ("cn-wulanchabu", "China (Ulanqab)"),
    "cn-chengdu": ("cn-chengdu", "China (Chengdu)"),
    "cn-qingdao": ("cn-qingdao", "China (Qingdao)"),
    "cn-nanjing": ("cn-nanjing", "China (Nanjing)"),
    "cn-heyuan": ("cn-heyuan", "China (Heyuan)"),
    "cn-guangzhou": ("cn-guangzhou", "China (Guangzhou)"),
    "cn-wuhan-lr": ("cn-wuhan-lr", "China (Wuhan)"),
    "ap-southeast-1": ("ap-southeast-1", "Asia Pacific (Singapore)"),
    "ap-southeast-2": ("ap-southeast-2", "Asia Pacific (Sydney)"),
    "ap-southeast-3": ("ap-southeast-3", "Asia Pacific (Kuala Lumpur)"),
    "ap-southeast-5": ("ap-southeast-5", "Asia Pacific (Jakarta)"),
    "ap-southeast-6": ("ap-southeast-6", "Asia Pacific (Manila)"),
    "ap-southeast-7": ("ap-southeast-7", "Asia Pacific (Bangkok)"),
    "ap-northeast-1": ("ap-northeast-1", "Asia Pacific (Tokyo)"),
    "ap-northeast-2": ("ap-northeast-2", "Asia Pacific (Seoul)"),
    "ap-south-1": ("ap-south-1", "Asia Pacific (Mumbai)"),
    "eu-west-1": ("eu-west-1", "Europe (London)"),
    "eu-central-1": ("eu-central-1", "Europe (Frankfurt)"),
    "us-east-1": ("us-east-1", "US (Virginia)"),
    "us-west-1": ("us-west-1", "US (Silicon Valley)"),
    "me-east-1": ("me-east-1", "Middle East (Dubai)"),
}


def _parse_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse Alibaba datetime formats."""
    if not value:
        return None
    value = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_alibaba_row(
    row: dict,
    cny_to_usd: float = DEFAULT_CNY_TO_USD,
) -> FocusRecord | None:
    """
    Normalize a single Alibaba Cloud BSS billing row to a FocusRecord.

    Args:
        row: Dict from Alibaba BSS QueryInstanceBill API response.
        cny_to_usd: CNY to USD exchange rate.
    """
    # Cost (CNY → USD)
    pretax_amount_cny = _parse_float(row.get("PretaxAmount") or row.get("Amount")) or 0.0
    effective_cost = pretax_amount_cny * cny_to_usd

    # Service
    product_code = (row.get("ProductCode") or "").lower()
    product_type = row.get("ProductType") or ""
    service_name, service_category = _PRODUCT_MAP.get(
        product_code,
        (row.get("ProductName") or product_code or "Unknown", "Other"),
    )
    if product_type and product_type != service_name:
        service_name = f"{service_name} - {product_type}"

    # Region
    region_code = row.get("Region") or row.get("RegionId") or ""
    region_id, region_name = _REGION_MAP.get(
        region_code.lower(),
        (region_code or None, region_code or None),
    )

    # Dates
    usage_start = _parse_datetime(
        row.get("UsageStartTime") or row.get("StartTime") or row.get("BillingDate")
    )
    usage_end = _parse_datetime(
        row.get("UsageEndTime") or row.get("EndTime")
    )

    billing_month = row.get("BillingCycle") or row.get("Month")
    if billing_month and not usage_start:
        # BillingCycle format: "2024-01"
        try:
            usage_start = datetime.strptime(billing_month, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError:
            usage_start = datetime.now(tz=timezone.utc)

    billing_start = usage_start or datetime.now(tz=timezone.utc)
    billing_end = usage_end or billing_start

    # Resource
    resource_id = row.get("InstanceID") or row.get("InstanceId") or None
    resource_type = row.get("InstanceSpec") or row.get("InstanceConfig") or None

    # Usage
    consumed_quantity = _parse_float(row.get("Amount") or row.get("UsageAmount"))
    consumed_unit = row.get("Unit") or None

    # Sub-account
    sub_account_id = row.get("AccountID") or row.get("OwnerID") or None
    sub_account_name = row.get("AccountName") or None

    # Tags — Alibaba uses "key:value,key:value" format in the Tag field
    tags: dict[str, str] | None = None
    tag_str = row.get("Tag") or row.get("Tags") or ""
    if tag_str:
        parsed_tags: dict[str, str] = {}
        for pair in str(tag_str).split(","):
            pair = pair.strip()
            if ":" in pair:
                k, _, v = pair.partition(":")
                parsed_tags[k.strip()] = v.strip()
        tags = parsed_tags if parsed_tags else None

    # Charge category — Alibaba doesn't have a direct equivalent; default to Usage
    charge_category = ChargeCategory.USAGE
    item_type = (row.get("ItemType") or "").lower()
    if "tax" in item_type:
        charge_category = ChargeCategory.TAX
    elif "refund" in item_type or "credit" in item_type:
        charge_category = ChargeCategory.ADJUSTMENT

    return FocusRecord(
        BillingPeriodStart=billing_start,
        BillingPeriodEnd=billing_end,
        ChargeCategory=charge_category,
        EffectiveCost=effective_cost,
        InvoiceIssuerName="Alibaba Cloud",
        ProviderName="Alibaba Cloud",
        ServiceCategory=service_category,
        ServiceName=service_name,
        ChargePeriodStart=usage_start,
        ChargePeriodEnd=usage_end,
        ConsumedQuantity=consumed_quantity,
        ConsumedUnit=consumed_unit,
        RegionId=region_id,
        RegionName=region_name,
        ResourceId=resource_id,
        ResourceType=resource_type,
        SubAccountId=sub_account_id,
        SubAccountName=sub_account_name,
        PublisherName="Alibaba Cloud",
        Tags=tags,
    )


def parse_alibaba_json(
    data: list[dict],
    cny_to_usd: float = DEFAULT_CNY_TO_USD,
) -> list[FocusRecord]:
    """
    Parse a list of Alibaba Cloud BSS billing API response rows.

    Args:
        data: List of billing record dicts from the BSS QueryInstanceBill API.
        cny_to_usd: CNY to USD exchange rate.

    Returns:
        List of FocusRecord objects.
    """
    records: list[FocusRecord] = []
    errors = 0

    for i, row in enumerate(data):
        try:
            record = normalize_alibaba_row(row, cny_to_usd)
            if record is not None:
                records.append(record)
        except Exception as exc:
            errors += 1
            logger.warning("Alibaba row %d failed: %s", i, exc)

    logger.info(
        "Alibaba export parsed: %d records, %d errors",
        len(records), errors,
    )
    return records
