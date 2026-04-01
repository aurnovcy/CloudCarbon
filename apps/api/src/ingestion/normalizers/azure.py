"""
Azure Cost Management export normalizer.

Converts Azure Cost Management CSV export rows to FOCUS 1.0 FocusRecord objects.
Azure exports costs in the subscription's billing currency; this normalizer
converts to USD using the exchange rate provided in the export (or a fallback rate).

Azure Cost Management export reference:
  https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-export-acm-data
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from focus_schema.models import ChargeCategory, FocusRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Azure ChargeType → FOCUS ChargeCategory
# ---------------------------------------------------------------------------

_CHARGE_TYPE_MAP: dict[str, ChargeCategory] = {
    "Usage": ChargeCategory.USAGE,
    "Purchase": ChargeCategory.PURCHASE,
    "Refund": ChargeCategory.ADJUSTMENT,
    "Tax": ChargeCategory.TAX,
    "Adjustment": ChargeCategory.ADJUSTMENT,
    "UnusedReservation": ChargeCategory.PURCHASE,
    "UnusedSavingsPlan": ChargeCategory.PURCHASE,
    "RoundingAdjustment": ChargeCategory.ADJUSTMENT,
}

# ---------------------------------------------------------------------------
# Azure ServiceName → ServiceCategory taxonomy
# ---------------------------------------------------------------------------

_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "Virtual Machines": "Compute",
    "Azure Kubernetes Service": "Compute",
    "Azure Container Instances": "Compute",
    "Azure Functions": "Compute",
    "App Service": "Compute",
    "Azure Batch": "Compute",
    "Azure Storage": "Storage",
    "Azure Blob Storage": "Storage",
    "Azure Files": "Storage",
    "Azure Disk Storage": "Storage",
    "Azure Data Lake Storage": "Storage",
    "Azure SQL Database": "Database",
    "Azure Cosmos DB": "Database",
    "Azure Database for PostgreSQL": "Database",
    "Azure Database for MySQL": "Database",
    "Azure Cache for Redis": "Database",
    "Azure Synapse Analytics": "Analytics",
    "Azure HDInsight": "Analytics",
    "Azure Databricks": "Analytics",
    "Azure Stream Analytics": "Analytics",
    "Azure Virtual Network": "Networking",
    "Azure Load Balancer": "Networking",
    "Azure Application Gateway": "Networking",
    "Azure CDN": "Networking",
    "Azure ExpressRoute": "Networking",
    "Azure DNS": "Networking",
    "Azure Bandwidth": "Networking",
    "Azure Machine Learning": "AI and Machine Learning",
    "Azure OpenAI": "AI and Machine Learning",
    "Azure Cognitive Services": "AI and Machine Learning",
    "Azure Monitor": "Management and Governance",
    "Azure Policy": "Management and Governance",
    "Azure Automation": "Management and Governance",
    "Microsoft Defender for Cloud": "Security, Identity, and Compliance",
    "Azure Key Vault": "Security, Identity, and Compliance",
    "Azure Active Directory": "Security, Identity, and Compliance",
}

# ---------------------------------------------------------------------------
# Azure region location → RegionId, RegionName
# ---------------------------------------------------------------------------

_REGION_MAP: dict[str, tuple[str, str]] = {
    "eastus": ("eastus", "East US"),
    "eastus2": ("eastus2", "East US 2"),
    "westus": ("westus", "West US"),
    "westus2": ("westus2", "West US 2"),
    "westus3": ("westus3", "West US 3"),
    "centralus": ("centralus", "Central US"),
    "northcentralus": ("northcentralus", "North Central US"),
    "southcentralus": ("southcentralus", "South Central US"),
    "canadacentral": ("canadacentral", "Canada Central"),
    "canadaeast": ("canadaeast", "Canada East"),
    "northeurope": ("northeurope", "North Europe"),
    "westeurope": ("westeurope", "West Europe"),
    "uksouth": ("uksouth", "UK South"),
    "ukwest": ("ukwest", "UK West"),
    "francecentral": ("francecentral", "France Central"),
    "germanywestcentral": ("germanywestcentral", "Germany West Central"),
    "swedencentral": ("swedencentral", "Sweden Central"),
    "switzerlandnorth": ("switzerlandnorth", "Switzerland North"),
    "norwayeast": ("norwayeast", "Norway East"),
    "eastasia": ("eastasia", "East Asia"),
    "southeastasia": ("southeastasia", "Southeast Asia"),
    "japaneast": ("japaneast", "Japan East"),
    "japanwest": ("japanwest", "Japan West"),
    "australiaeast": ("australiaeast", "Australia East"),
    "australiasoutheast": ("australiasoutheast", "Australia Southeast"),
    "koreacentral": ("koreacentral", "Korea Central"),
    "centralindia": ("centralindia", "Central India"),
    "southindia": ("southindia", "South India"),
    "westindia": ("westindia", "West India"),
    "brazilsouth": ("brazilsouth", "Brazil South"),
    "southafricanorth": ("southafricanorth", "South Africa North"),
    "uaenorth": ("uaenorth", "UAE North"),
}


def _parse_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_date(value: str | None) -> datetime | None:
    """Parse Azure date formats: YYYY-MM-DD or MM/DD/YYYY."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_tags(tags_str: str | None) -> dict[str, str] | None:
    """Parse Azure tags from JSON string or key:value pairs."""
    if not tags_str or tags_str.strip() in ("", "{}", "null"):
        return None
    try:
        parsed = json.loads(tags_str)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v is not None}
    except (json.JSONDecodeError, TypeError):
        pass
    # Try key:value format
    tags: dict[str, str] = {}
    for part in tags_str.split(";"):
        if ":" in part:
            k, _, v = part.partition(":")
            tags[k.strip()] = v.strip()
    return tags if tags else None


def _get_region(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    # Normalize: remove spaces, lowercase
    normalized = location.lower().replace(" ", "").replace("-", "")
    if normalized in _REGION_MAP:
        return _REGION_MAP[normalized]
    # Try direct match
    if location.lower() in _REGION_MAP:
        return _REGION_MAP[location.lower()]
    return location, location


def normalize_azure_row(row: dict[str, str], fx_rate_to_usd: float = 1.0) -> FocusRecord | None:
    """
    Normalize a single Azure Cost Management export row to a FocusRecord.

    Args:
        row: CSV row dict from Azure Cost Management export.
        fx_rate_to_usd: Exchange rate from billing currency to USD.
    """
    # Azure exports have varying column names depending on export type
    # Support both ActualCost and AmortizedCost exports

    cost_str = (
        row.get("Cost")
        or row.get("CostInBillingCurrency")
        or row.get("PreTaxCost")
        or "0"
    )
    effective_cost_raw = _parse_float(cost_str) or 0.0
    effective_cost = effective_cost_raw * fx_rate_to_usd

    unit_price_raw = _parse_float(
        row.get("UnitPrice") or row.get("EffectivePrice")
    )
    list_unit_price = (unit_price_raw * fx_rate_to_usd) if unit_price_raw is not None else None

    # Dates
    date_str = row.get("Date") or row.get("UsageDateTime") or row.get("BillingPeriodStartDate")
    charge_start = _parse_date(date_str)
    if charge_start:
        charge_end = charge_start + timedelta(days=1)
    else:
        charge_end = None

    billing_start_str = row.get("BillingPeriodStartDate") or date_str
    billing_end_str = row.get("BillingPeriodEndDate")
    billing_start = _parse_date(billing_start_str) or charge_start or datetime.now(tz=timezone.utc)
    billing_end = _parse_date(billing_end_str) or (billing_start + timedelta(days=30))

    # Service
    service_name = (
        row.get("ServiceName")
        or row.get("MeterCategory")
        or row.get("ConsumedService")
        or "Unknown"
    )
    # Try to resolve service category from Product name first (more specific),
    # then fall back to service_name (MeterCategory)
    product_name = row.get("Product") or ""
    service_category = (
        _SERVICE_CATEGORY_MAP.get(product_name)
        or _SERVICE_CATEGORY_MAP.get(service_name)
        or "Other"
    )

    # Charge type
    charge_type = row.get("ChargeType") or "Usage"
    charge_category = _CHARGE_TYPE_MAP.get(charge_type, ChargeCategory.USAGE)

    # Region
    location = row.get("ResourceLocation") or row.get("Location")
    region_id, region_name = _get_region(location)

    # Resource
    resource_id = row.get("ResourceId") or row.get("InstanceId") or None
    resource_name = row.get("ResourceName") or row.get("ResourceGroup") or None
    resource_type = row.get("ResourceType") or None

    # Usage
    consumed_quantity = _parse_float(row.get("Quantity") or row.get("UsageQuantity"))
    consumed_unit = row.get("UnitOfMeasure") or None

    # Sub-account (subscription)
    sub_account_id = row.get("SubscriptionId") or row.get("SubscriptionGuid") or None
    sub_account_name = row.get("SubscriptionName") or None

    # SKU
    sku_id = row.get("MeterId") or None
    sku_price_id = row.get("MeterSubCategory") or None

    # Tags
    tags = _parse_tags(row.get("Tags"))

    return FocusRecord(
        BillingPeriodStart=billing_start,
        BillingPeriodEnd=billing_end,
        ChargeCategory=charge_category,
        EffectiveCost=effective_cost,
        InvoiceIssuerName="Microsoft Azure",
        ProviderName="Microsoft Azure",
        ServiceCategory=service_category,
        ServiceName=service_name,
        ChargePeriodStart=charge_start,
        ChargePeriodEnd=charge_end,
        ConsumedQuantity=consumed_quantity,
        ConsumedUnit=consumed_unit,
        ListUnitPrice=list_unit_price,
        RegionId=region_id,
        RegionName=region_name,
        ResourceId=resource_id,
        ResourceName=resource_name,
        ResourceType=resource_type,
        SkuId=sku_id,
        SkuPriceId=sku_price_id,
        SubAccountId=sub_account_id,
        SubAccountName=sub_account_name,
        Tags=tags,
        PublisherName="Microsoft",
    )


def parse_azure_csv(file_path: str, fx_rate_to_usd: float = 1.0) -> list[FocusRecord]:
    """
    Parse an Azure Cost Management export CSV file.

    Args:
        file_path: Path to the Azure Cost Management CSV export.
        fx_rate_to_usd: Exchange rate to convert billing currency to USD.

    Returns:
        List of FocusRecord objects.
    """
    path = Path(file_path)
    records: list[FocusRecord] = []
    errors = 0

    with open(path, encoding="utf-8-sig") as f:
        # Azure exports sometimes have metadata rows at the top; skip until header
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            try:
                record = normalize_azure_row(row, fx_rate_to_usd)
                if record is not None:
                    records.append(record)
            except Exception as exc:
                errors += 1
                logger.warning("Azure row %d failed: %s", i, exc)

    logger.info(
        "Azure export parsed: %d records, %d errors from %s",
        len(records), errors, file_path,
    )
    return records
