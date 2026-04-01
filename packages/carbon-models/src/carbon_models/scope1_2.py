"""
Scope 1 and Scope 2 carbon estimation for cloud workloads.

GHG Protocol definitions:
  Scope 1 — Direct emissions from owned/controlled sources.
             For cloud customers, this is ZERO: the provider owns the
             combustion sources (backup generators, on-site fuel).
  Scope 2 — Indirect emissions from purchased electricity.
             Calculated using both location-based and market-based methods
             per GHG Protocol Scope 2 Guidance (2015).

All carbon values are in kg CO2e.
All energy values are in kWh.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global fallback constants
# ---------------------------------------------------------------------------

GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH: float = 475.0  # IEA 2023 world average
SCOPE1_METHODOLOGY_NOTE: str = (
    "Scope 1 emissions are zero for cloud customers under the GHG Protocol. "
    "Direct combustion sources (backup generators, on-site fuel) are owned by "
    "the cloud provider and classified as their Scope 1 emissions."
)

# ---------------------------------------------------------------------------
# Energy intensity coefficients
# ---------------------------------------------------------------------------

# Compute
_KWH_PER_VCPU_HOUR: float = 0.005          # TDP-based estimate for x86 vCPU
_KWH_PER_GPU_HOUR: float = 0.300           # Average GPU (A100-class)
_KWH_PER_INVOCATION: float = 0.0000003    # Serverless function invocation

# Storage
_KWH_PER_GB_MONTH: float = 0.000002       # HDD/SSD blended

# Networking
_KWH_PER_GB_TRANSFERRED: float = 0.001    # WAN data transfer

# Database
_KWH_PER_DB_VCPU_HOUR: float = 0.006      # Higher than compute due to storage I/O

# AI / ML
_KWH_PER_AI_GPU_HOUR: float = 0.400       # Training-class GPU

# Spend-based fallback
_KWH_PER_USD: float = 0.002               # Spend-based fallback (Scope 2 guidance)

# Unit-string keywords that indicate the usage quantity unit
_VCPU_KEYWORDS: tuple[str, ...] = ("vcpu", "core", "cpu", "vcore")
_GPU_KEYWORDS: tuple[str, ...] = ("gpu",)
_INVOCATION_KEYWORDS: tuple[str, ...] = ("invocation", "request", "call", "execution")
_GB_KEYWORDS: tuple[str, ...] = ("gb", "gib", "gigabyte")
_HOUR_KEYWORDS: tuple[str, ...] = ("hour", "hr", "h")


def _unit_contains(unit: str | None, *keywords: str) -> bool:
    if not unit:
        return False
    u = unit.lower()
    return any(k in u for k in keywords)


def compute_kwh_per_unit(resource_type: str | None, consumed_unit: str | None) -> float:
    """
    Return the kWh coefficient for one unit of the given compute resource.

    Decision order:
    1. GPU instance (resource_type contains 'gpu' or unit contains 'gpu')
    2. Serverless invocation (unit contains invocation keywords)
    3. Default vCPU-hour
    """
    rt = (resource_type or "").lower()
    if "gpu" in rt or _unit_contains(consumed_unit, *_GPU_KEYWORDS):
        return _KWH_PER_GPU_HOUR
    if _unit_contains(consumed_unit, *_INVOCATION_KEYWORDS):
        return _KWH_PER_INVOCATION
    return _KWH_PER_VCPU_HOUR


def estimate_kwh(
    service_category: str,
    consumed_quantity: float | None,
    consumed_unit: str | None,
    resource_type: str | None,
    cost_usd: float,
) -> float:
    """
    Estimate energy consumption in kWh for a single FOCUS billing record.

    Args:
        service_category: FOCUS ServiceCategory string.
        consumed_quantity: FOCUS ConsumedQuantity (may be None).
        consumed_unit: FOCUS ConsumedUnit string (may be None).
        resource_type: FOCUS ResourceType (instance type, e.g. 'm5.xlarge').
        cost_usd: FOCUS EffectiveCost in USD (used as fallback).

    Returns:
        Estimated energy in kWh as a float.
    """
    qty = consumed_quantity or 0.0
    cat = (service_category or "").strip()

    if cat == "Compute":
        coeff = compute_kwh_per_unit(resource_type, consumed_unit)
        if qty > 0:
            return qty * coeff
        # Fallback: spend-based if no quantity
        return cost_usd * _KWH_PER_USD

    if cat == "Storage":
        if qty > 0:
            return qty * _KWH_PER_GB_MONTH
        return cost_usd * _KWH_PER_USD

    if cat == "Networking":
        if qty > 0:
            return qty * _KWH_PER_GB_TRANSFERRED
        return cost_usd * _KWH_PER_USD

    if cat == "Database":
        if qty > 0:
            return qty * _KWH_PER_DB_VCPU_HOUR
        return cost_usd * _KWH_PER_USD

    if cat in ("AI and Machine Learning", "AI and ML"):
        if qty > 0:
            return qty * _KWH_PER_AI_GPU_HOUR
        return cost_usd * _KWH_PER_USD

    # Unrecognized category — spend-based fallback
    return cost_usd * _KWH_PER_USD


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Scope2Result:
    """Result of Scope 2 carbon calculation."""
    location_kg: float
    market_kg: float
    intensity_gco2_kwh: float
    region_found: bool
    methodology_note: str = ""


@dataclass
class Scope1Result:
    """Result of Scope 1 carbon calculation (always zero for cloud customers)."""
    co2e_kg: float = 0.0
    methodology_note: str = SCOPE1_METHODOLOGY_NOTE


# ---------------------------------------------------------------------------
# Scope 2 calculation
# ---------------------------------------------------------------------------

async def calculate_scope2_async(
    estimated_kwh: float,
    region_id: str | None,
    db_session: Any,
) -> Scope2Result:
    """
    Calculate Scope 2 carbon using both location-based and market-based methods.

    Performs an async database lookup in region_carbon_intensity.
    Falls back to the global average (475 gCO2/kWh) if the region is not found.

    Args:
        estimated_kwh: Energy estimate in kWh.
        region_id: Cloud region identifier (e.g. 'us-east-1', 'eastus').
        db_session: SQLAlchemy AsyncSession.

    Returns:
        Scope2Result with location_kg, market_kg, intensity_gco2_kwh, region_found.
    """
    from sqlalchemy import text

    intensity_gco2: float = GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH
    market_intensity_gco2: float = GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH
    region_found: bool = False

    if region_id:
        try:
            result = await db_session.execute(
                text(
                    "SELECT carbon_intensity_gco2_kwh, carbon_intensity_market_gco2_kwh "
                    "FROM region_carbon_intensity WHERE region_id = :rid LIMIT 1"
                ),
                {"rid": region_id},
            )
            row = result.fetchone()
            if row and row[0] is not None:
                intensity_gco2 = float(row[0])
                market_intensity_gco2 = float(row[1]) if row[1] is not None else intensity_gco2
                region_found = True
        except Exception as exc:
            logger.warning("Scope 2 DB lookup failed for region %s: %s", region_id, exc)

    location_kg = estimated_kwh * intensity_gco2 / 1000.0
    market_kg = estimated_kwh * market_intensity_gco2 / 1000.0

    return Scope2Result(
        location_kg=location_kg,
        market_kg=market_kg,
        intensity_gco2_kwh=intensity_gco2,
        region_found=region_found,
        methodology_note=(
            "GHG Protocol Scope 2 Guidance (2015) — location-based and market-based methods. "
            f"Region {'found in' if region_found else 'not found; using global average from'} "
            "region_carbon_intensity table."
        ),
    )


def calculate_scope2_sync(
    estimated_kwh: float,
    region_id: str | None,
    intensity_gco2_kwh: float | None = None,
    market_intensity_gco2_kwh: float | None = None,
) -> Scope2Result:
    """
    Synchronous Scope 2 calculation using pre-fetched intensity values.

    Useful for unit tests and batch pre-fetch patterns.

    Args:
        estimated_kwh: Energy estimate in kWh.
        region_id: Cloud region identifier (informational only).
        intensity_gco2_kwh: Location-based intensity (gCO2/kWh). Uses global avg if None.
        market_intensity_gco2_kwh: Market-based intensity. Defaults to location value.

    Returns:
        Scope2Result.
    """
    intensity = intensity_gco2_kwh if intensity_gco2_kwh is not None else GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH
    market_intensity = (
        market_intensity_gco2_kwh if market_intensity_gco2_kwh is not None else intensity
    )
    region_found = intensity_gco2_kwh is not None

    return Scope2Result(
        location_kg=estimated_kwh * intensity / 1000.0,
        market_kg=estimated_kwh * market_intensity / 1000.0,
        intensity_gco2_kwh=intensity,
        region_found=region_found,
    )


def get_scope1() -> Scope1Result:
    """Return the Scope 1 result (always zero for cloud customers)."""
    return Scope1Result()
