"""
FOCUS 1.0 core schema + CloudCarbon enrichment extensions.

Pydantic v2 models. All monetary values are in USD.
All datetime fields are timezone-aware (UTC).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ChargeCategory(str, Enum):
    """FOCUS 1.0 ChargeCategory values."""
    USAGE = "Usage"
    PURCHASE = "Purchase"
    TAX = "Tax"
    ADJUSTMENT = "Adjustment"
    CREDIT = "Credit"


class ChargeClass(str, Enum):
    """FOCUS 1.0 ChargeClass values."""
    REGULAR = "Regular"
    CORRECTION = "Correction"


class ChargeFrequency(str, Enum):
    """FOCUS 1.0 ChargeFrequency values."""
    ONE_TIME = "One-Time"
    RECURRING = "Recurring"
    USAGE_BASED = "Usage-Based"


class PricingCategory(str, Enum):
    """FOCUS 1.0 PricingCategory values."""
    ON_DEMAND = "On-Demand"
    COMMITMENT_BASED = "Commitment-Based"
    SPOT = "Spot"
    OTHER = "Other"


class Scope3Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WaterDataSource(str, Enum):
    PROVIDER_DISCLOSED = "provider_disclosed"
    COOLING_TYPE_ESTIMATE = "cooling_type_estimate"


# ---------------------------------------------------------------------------
# Module-level aliases for enums that share names with FOCUS fields.
# Without these aliases, `from __future__ import annotations` causes Pydantic
# to resolve the field annotation in the class namespace where the field name
# shadows the enum type, resulting in Optional[None] instead of Optional[EnumType].
# ---------------------------------------------------------------------------

_ChargeClass = ChargeClass
_ChargeFrequency = ChargeFrequency
_PricingCategory = PricingCategory


# ---------------------------------------------------------------------------
# FOCUS 1.0 Core Record
# ---------------------------------------------------------------------------

class FocusRecord(BaseModel):
    """
    FOCUS 1.0 core billing record.
    All required FOCUS fields are present; optional fields default to None.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    # Required FOCUS fields
    BillingPeriodStart: datetime
    BillingPeriodEnd: datetime
    ChargeCategory: ChargeCategory
    EffectiveCost: float
    InvoiceIssuerName: str
    ProviderName: str
    ServiceCategory: str
    ServiceName: str

    # Optional FOCUS fields
    # NOTE: Fields whose name matches their enum type use Optional[X] instead of X | None
    # to avoid Python 3.11 name-shadowing with `from __future__ import annotations`.
    ChargeClass: Optional[_ChargeClass] = None
    ChargeDescription: Optional[str] = None
    ChargeFrequency: Optional[_ChargeFrequency] = None
    ChargePeriodStart: Optional[datetime] = None
    ChargePeriodEnd: Optional[datetime] = None
    ConsumedQuantity: Optional[float] = None
    ConsumedUnit: Optional[str] = None
    ContractedCost: Optional[float] = None
    ContractedUnitPrice: Optional[float] = None
    ListCost: Optional[float] = None
    ListUnitPrice: Optional[float] = None
    PricingCategory: Optional[_PricingCategory] = None
    PricingQuantity: Optional[float] = None
    PricingUnit: Optional[str] = None
    PublisherName: Optional[str] = None
    RegionId: Optional[str] = None
    RegionName: Optional[str] = None
    ResourceId: Optional[str] = None
    ResourceName: Optional[str] = None
    ResourceType: Optional[str] = None
    SkuId: Optional[str] = None
    SkuPriceId: Optional[str] = None
    SubAccountId: Optional[str] = None
    SubAccountName: Optional[str] = None
    Tags: Optional[dict[str, str]] = None

    @field_validator("BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd", mode="before")
    @classmethod
    def parse_datetime(cls, v: Any) -> Any:
        """Accept ISO strings and datetime objects."""
        if isinstance(v, str):
            # Handle Z suffix and offset-naive strings
            v = v.replace("Z", "+00:00")
            return datetime.fromisoformat(v)
        return v

    @field_validator("EffectiveCost", "ListCost", "ContractedCost", mode="before")
    @classmethod
    def parse_cost(cls, v: Any) -> Any:
        """Coerce string costs to float."""
        if isinstance(v, str):
            v = v.strip()
            return float(v) if v else 0.0
        return v

    def to_db_dict(self) -> dict:
        """
        Convert to a dict suitable for inserting into the focus_records table.
        Maps FOCUS PascalCase field names to snake_case column names.
        """
        return {
            "billing_period_start": self.BillingPeriodStart,
            "billing_period_end": self.BillingPeriodEnd,
            "provider": self.ProviderName,
            "service_name": self.ServiceName,
            "service_category": self.ServiceCategory,
            "region": self.RegionId,
            "resource_id": self.ResourceId,
            "resource_type": self.ResourceType,
            "usage_quantity": self.ConsumedQuantity,
            "usage_unit": self.ConsumedUnit,
            "cost_usd": self.EffectiveCost,
            "list_cost_usd": self.ListCost,
            "currency": "USD",
            "tags": self.Tags or {},
            "raw_data": self.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------
# CloudCarbon Enriched Record
# ---------------------------------------------------------------------------

class CloudCarbonRecord(FocusRecord):
    """
    FOCUS 1.0 core fields + CloudCarbon enrichment extensions.
    All enrichment fields are optional and default to None.
    """

    # GHG Protocol Scope 1
    scope1_co2e_kg: float | None = None

    # GHG Protocol Scope 2 (location-based and market-based)
    scope2_co2e_kg_location: float | None = None
    scope2_co2e_kg_market: float | None = None

    # GHG Protocol Scope 3
    scope3_cat1_co2e_kg: float | None = None   # Purchased goods and services (hardware mfg)
    scope3_cat3_co2e_kg: float | None = None   # Fuel and energy related activities
    scope3_cat12_co2e_kg: float | None = None  # End-of-life treatment of sold products
    scope3_total_co2e_kg: float | None = None
    scope3_confidence: Scope3Confidence | None = None
    scope3_methodology_ref: str | None = None  # e.g. "boavizta-v2.3"

    # Totals
    total_co2e_kg: float | None = None

    # Energy
    carbon_intensity_gco2_kwh: float | None = None
    estimated_kwh: float | None = None

    # Hardware
    hardware_family: str | None = None
    resource_share: float | None = None  # fraction of physical host used (0.0–1.0)

    # Water
    water_litres: float | None = None
    wue_litres_per_kwh: float | None = None
    water_stress_score: float | None = None
    water_stress_adjusted_litres: float | None = None
    water_data_source: WaterDataSource | None = None
