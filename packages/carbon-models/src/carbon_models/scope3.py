"""
Scope 3 carbon estimation for cloud workloads.

Implements three GHG Protocol Scope 3 categories material for cloud:

  Category 1  — Purchased goods and services (embodied carbon in hardware)
  Category 3  — Fuel and energy-related activities (upstream energy supply chain)
  Category 12 — End-of-life treatment of sold products (hardware disposal)

Methodology reference: cloudcarbon_scope3_v1
All carbon values in kg CO2e.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default hardware coefficients (used when DB lookup fails)
# ---------------------------------------------------------------------------

_DEFAULT_MFG_CO2E_KG: float = 1000.0      # generic x86 server manufacturing
_DEFAULT_LIFESPAN_HOURS: int = 35_040      # 4 years × 8760 h/year
_DEFAULT_EOL_CO2E_KG: float = 50.0        # generic end-of-life estimate

# Default hardware families by service category
_CATEGORY_DEFAULT_HARDWARE: dict[str, str] = {
    "Compute": "generic_x86_server",
    "Database": "generic_x86_server",
    "Storage": "generic_storage_array",
    "AI and Machine Learning": "generic_gpu_server",
    "AI and ML": "generic_gpu_server",
}

# Serverless resource share constant
_SERVERLESS_RESOURCE_SHARE: float = 0.0001

# Upstream supply chain factors (IPCC AR6)
_FOSSIL_UPSTREAM_FACTOR: float = 0.15     # 15% upstream for fossil fuel chain
_RENEWABLE_UPSTREAM_FACTOR: float = 0.03  # 3% upstream for renewables
_TD_LOSS_FACTOR: float = 0.06             # 6% T&D losses (global average)

# Default renewable percentage when region data is unavailable
_DEFAULT_RENEWABLE_PCT: float = 0.30      # 30% global average


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Cat1Result:
    """Category 1: Embodied carbon in hardware (purchased goods and services)."""
    co2e_kg: float
    hardware_family: str
    resource_share: float
    confidence: Literal["high", "medium", "low"]
    methodology: str


@dataclass
class Cat3Result:
    """Category 3: Upstream fuel and energy-related activities."""
    co2e_kg: float
    upstream_factor_used: float
    confidence: Literal["high", "medium", "low"] = "medium"


@dataclass
class Cat12Result:
    """Category 12: End-of-life treatment of hardware."""
    co2e_kg: float
    confidence: Literal["high", "medium", "low"] = "low"


@dataclass
class Scope3Estimate:
    """Aggregated Scope 3 estimate across all three categories."""
    cat1: Cat1Result
    cat3: Cat3Result
    cat12: Cat12Result
    scope3_total_co2e_kg: float
    confidence: Literal["high", "medium", "low"]
    methodology_ref: str


# ---------------------------------------------------------------------------
# Helper: usage hours from FOCUS record period
# ---------------------------------------------------------------------------

def _usage_hours(charge_period_start: Any, charge_period_end: Any) -> float:
    """
    Calculate usage hours from ChargePeriodStart and ChargePeriodEnd.
    Falls back to 1.0 hour if either value is missing or invalid.
    """
    try:
        if charge_period_start and charge_period_end:
            delta = charge_period_end - charge_period_start
            hours = delta.total_seconds() / 3600.0
            return max(hours, 0.0)
    except (TypeError, AttributeError):
        pass
    return 1.0  # safe fallback


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Category 1 — Embodied carbon
# ---------------------------------------------------------------------------

async def estimate_cat1_embodied_async(
    service_category: str,
    resource_type: str | None,
    provider: str | None,
    consumed_quantity: float | None,
    consumed_unit: str | None,
    charge_period_start: Any,
    charge_period_end: Any,
    db_session: Any,
) -> Cat1Result:
    """
    Async version: looks up instance_hardware_map and hardware_carbon_coefficients.
    """
    from sqlalchemy import text

    cat = (service_category or "").strip()

    # Skip networking — embodied carbon is negligible
    if cat == "Networking":
        return Cat1Result(
            co2e_kg=0.0,
            hardware_family="N/A",
            resource_share=0.0,
            confidence="low",
            methodology="category_estimate_v1",
        )

    # Step 1: Resolve hardware family and instance specs
    hardware_family: str | None = None
    vcpu_count: float = 1.0
    ram_gb: float = 4.0
    total_vcpu: float = 64.0
    total_ram: float = 256.0
    instance_found: bool = False

    if resource_type and provider:
        try:
            result = await db_session.execute(
                text(
                    "SELECT hardware_family, vcpu_count, ram_gb, "
                    "total_vcpu_on_host, total_ram_gb_on_host "
                    "FROM instance_hardware_map "
                    "WHERE instance_type = :it AND provider = :prov LIMIT 1"
                ),
                {"it": resource_type, "prov": provider},
            )
            row = result.fetchone()
            if row and row[0]:
                hardware_family = row[0]
                vcpu_count = float(row[1] or 1)
                ram_gb = float(row[2] or 4)
                total_vcpu = float(row[3] or 64)
                total_ram = float(row[4] or 256)
                instance_found = True
        except Exception as exc:
            logger.warning("Cat1 instance lookup failed for %s/%s: %s", provider, resource_type, exc)

    if not hardware_family:
        hardware_family = _CATEGORY_DEFAULT_HARDWARE.get(cat, "generic_x86_server")

    # Step 2: Calculate resource share
    unit_lower = (consumed_unit or "").lower()
    is_serverless = any(k in unit_lower for k in ("invocation", "request", "call", "execution"))

    if is_serverless:
        qty = consumed_quantity or 0.0
        resource_share = qty * _SERVERLESS_RESOURCE_SHARE
    else:
        vcpu_share = vcpu_count / total_vcpu if total_vcpu > 0 else 0.0
        ram_share = ram_gb / total_ram if total_ram > 0 else 0.0
        resource_share = (vcpu_share + ram_share) / 2.0

    resource_share = _clamp(resource_share, 0.0001, 1.0)

    # Step 3: Get hardware coefficient from DB
    mfg_co2e_kg: float = _DEFAULT_MFG_CO2E_KG
    lifespan_hours: int = _DEFAULT_LIFESPAN_HOURS
    methodology: str = "category_estimate_v1"

    try:
        result = await db_session.execute(
            text(
                "SELECT mfg_co2e_kg, lifespan_hours, source "
                "FROM hardware_carbon_coefficients "
                "WHERE hardware_family = :hf LIMIT 1"
            ),
            {"hf": hardware_family},
        )
        row = result.fetchone()
        if row and row[0] is not None:
            mfg_co2e_kg = float(row[0])
            lifespan_hours = int(row[1]) if row[1] else _DEFAULT_LIFESPAN_HOURS
            source = row[2] or "estimate"
            methodology = "boavizta_v1" if "boavizta" in source.lower() else "category_estimate_v1"
    except Exception as exc:
        logger.warning("Cat1 hardware coefficient lookup failed for %s: %s", hardware_family, exc)

    # Step 4: Usage hours
    hours = _usage_hours(charge_period_start, charge_period_end)

    # Step 5: Final calculation
    embodied_per_hour = mfg_co2e_kg / lifespan_hours if lifespan_hours > 0 else 0.0
    cat1_co2e_kg = embodied_per_hour * hours * resource_share

    confidence: str
    if instance_found:
        confidence = "high"
    elif cat in _CATEGORY_DEFAULT_HARDWARE:
        confidence = "medium"
    else:
        confidence = "low"

    return Cat1Result(
        co2e_kg=cat1_co2e_kg,
        hardware_family=hardware_family,
        resource_share=resource_share,
        confidence=confidence,  # type: ignore[arg-type]
        methodology=methodology,
    )


def estimate_cat1_embodied_sync(
    service_category: str,
    resource_type: str | None,
    consumed_quantity: float | None,
    consumed_unit: str | None,
    charge_period_start: Any,
    charge_period_end: Any,
    mfg_co2e_kg: float = _DEFAULT_MFG_CO2E_KG,
    lifespan_hours: int = _DEFAULT_LIFESPAN_HOURS,
    vcpu_count: float = 1.0,
    ram_gb: float = 4.0,
    total_vcpu: float = 64.0,
    total_ram: float = 256.0,
    hardware_family: str = "generic_x86_server",
    instance_found: bool = False,
) -> Cat1Result:
    """
    Synchronous Cat1 calculation using pre-fetched values.
    Used in unit tests and batch pre-fetch patterns.
    """
    cat = (service_category or "").strip()

    if cat == "Networking":
        return Cat1Result(
            co2e_kg=0.0,
            hardware_family="N/A",
            resource_share=0.0,
            confidence="low",
            methodology="category_estimate_v1",
        )

    unit_lower = (consumed_unit or "").lower()
    is_serverless = any(k in unit_lower for k in ("invocation", "request", "call", "execution"))

    if is_serverless:
        qty = consumed_quantity or 0.0
        resource_share = qty * _SERVERLESS_RESOURCE_SHARE
    else:
        vcpu_share = vcpu_count / total_vcpu if total_vcpu > 0 else 0.0
        ram_share = ram_gb / total_ram if total_ram > 0 else 0.0
        resource_share = (vcpu_share + ram_share) / 2.0

    resource_share = _clamp(resource_share, 0.0001, 1.0)

    hours = _usage_hours(charge_period_start, charge_period_end)
    embodied_per_hour = mfg_co2e_kg / lifespan_hours if lifespan_hours > 0 else 0.0
    cat1_co2e_kg = embodied_per_hour * hours * resource_share

    if instance_found:
        confidence = "high"
    elif cat in _CATEGORY_DEFAULT_HARDWARE:
        confidence = "medium"
    else:
        confidence = "low"

    return Cat1Result(
        co2e_kg=cat1_co2e_kg,
        hardware_family=hardware_family,
        resource_share=resource_share,
        confidence=confidence,  # type: ignore[arg-type]
        methodology="category_estimate_v1",
    )


# ---------------------------------------------------------------------------
# Category 3 — Upstream energy
# ---------------------------------------------------------------------------

async def estimate_cat3_upstream_async(
    estimated_kwh: float,
    region_id: str | None,
    carbon_intensity_gco2_kwh: float,
    db_session: Any,
) -> Cat3Result:
    """
    Async version: looks up renewable_pct from region_carbon_intensity.
    """
    from sqlalchemy import text

    renewable_pct: float = _DEFAULT_RENEWABLE_PCT

    if region_id:
        try:
            result = await db_session.execute(
                text(
                    "SELECT renewable_pct FROM region_carbon_intensity "
                    "WHERE region_id = :rid LIMIT 1"
                ),
                {"rid": region_id},
            )
            row = result.fetchone()
            if row and row[0] is not None:
                renewable_pct = float(row[0]) / 100.0  # stored as percentage 0–100
        except Exception as exc:
            logger.warning("Cat3 renewable_pct lookup failed for %s: %s", region_id, exc)

    return _calculate_cat3(estimated_kwh, renewable_pct, carbon_intensity_gco2_kwh)


def estimate_cat3_upstream_sync(
    estimated_kwh: float,
    renewable_pct: float,
    carbon_intensity_gco2_kwh: float,
) -> Cat3Result:
    """
    Synchronous Cat3 calculation using pre-fetched values.
    renewable_pct should be in [0.0, 1.0].
    """
    return _calculate_cat3(estimated_kwh, renewable_pct, carbon_intensity_gco2_kwh)


def _calculate_cat3(
    estimated_kwh: float,
    renewable_pct: float,
    carbon_intensity_gco2_kwh: float,
) -> Cat3Result:
    fossil_pct = 1.0 - _clamp(renewable_pct, 0.0, 1.0)
    blended_upstream = (fossil_pct * _FOSSIL_UPSTREAM_FACTOR) + (renewable_pct * _RENEWABLE_UPSTREAM_FACTOR)
    total_factor = blended_upstream + _TD_LOSS_FACTOR
    additional_kwh = estimated_kwh * total_factor
    cat3_co2e_kg = additional_kwh * carbon_intensity_gco2_kwh / 1000.0

    return Cat3Result(
        co2e_kg=cat3_co2e_kg,
        upstream_factor_used=total_factor,
        confidence="medium",
    )


# ---------------------------------------------------------------------------
# Category 12 — End-of-life treatment
# ---------------------------------------------------------------------------

async def estimate_cat12_eol_async(
    hardware_family: str,
    resource_share: float,
    charge_period_start: Any,
    charge_period_end: Any,
    db_session: Any,
) -> Cat12Result:
    """
    Async version: looks up eol_co2e_kg from hardware_carbon_coefficients.
    """
    from sqlalchemy import text

    eol_co2e_kg: float = _DEFAULT_EOL_CO2E_KG
    lifespan_hours: int = _DEFAULT_LIFESPAN_HOURS

    try:
        result = await db_session.execute(
            text(
                "SELECT eol_co2e_kg, lifespan_hours "
                "FROM hardware_carbon_coefficients "
                "WHERE hardware_family = :hf LIMIT 1"
            ),
            {"hf": hardware_family},
        )
        row = result.fetchone()
        if row:
            if row[0] is not None:
                eol_co2e_kg = float(row[0])
            if row[1] is not None:
                lifespan_hours = int(row[1])
    except Exception as exc:
        logger.warning("Cat12 EOL lookup failed for %s: %s", hardware_family, exc)

    return _calculate_cat12(eol_co2e_kg, lifespan_hours, resource_share, charge_period_start, charge_period_end)


def estimate_cat12_eol_sync(
    hardware_family: str,
    resource_share: float,
    charge_period_start: Any,
    charge_period_end: Any,
    eol_co2e_kg: float = _DEFAULT_EOL_CO2E_KG,
    lifespan_hours: int = _DEFAULT_LIFESPAN_HOURS,
) -> Cat12Result:
    """Synchronous Cat12 calculation using pre-fetched values."""
    return _calculate_cat12(eol_co2e_kg, lifespan_hours, resource_share, charge_period_start, charge_period_end)


def _calculate_cat12(
    eol_co2e_kg: float,
    lifespan_hours: int,
    resource_share: float,
    charge_period_start: Any,
    charge_period_end: Any,
) -> Cat12Result:
    hours = _usage_hours(charge_period_start, charge_period_end)
    eol_per_hour = eol_co2e_kg / lifespan_hours if lifespan_hours > 0 else 0.0
    cat12_co2e_kg = eol_per_hour * hours * resource_share

    return Cat12Result(
        co2e_kg=cat12_co2e_kg,
        confidence="low",
    )


# ---------------------------------------------------------------------------
# Scope 3 aggregator
# ---------------------------------------------------------------------------

def _aggregate_confidence(
    cat1: Cat1Result,
    cat3: Cat3Result,
    cat12: Cat12Result,
) -> Literal["high", "medium", "low"]:
    levels = {"high": 3, "medium": 2, "low": 1}
    min_level = min(levels[cat1.confidence], levels[cat3.confidence], levels[cat12.confidence])
    return {3: "high", 2: "medium", 1: "low"}[min_level]  # type: ignore[return-value]


async def estimate_scope3_async(
    service_category: str,
    resource_type: str | None,
    provider: str | None,
    consumed_quantity: float | None,
    consumed_unit: str | None,
    charge_period_start: Any,
    charge_period_end: Any,
    estimated_kwh: float,
    region_id: str | None,
    carbon_intensity_gco2_kwh: float,
    db_session: Any,
) -> Scope3Estimate:
    """
    Async Scope 3 aggregator. Calls all three category estimators.
    """
    cat1 = await estimate_cat1_embodied_async(
        service_category=service_category,
        resource_type=resource_type,
        provider=provider,
        consumed_quantity=consumed_quantity,
        consumed_unit=consumed_unit,
        charge_period_start=charge_period_start,
        charge_period_end=charge_period_end,
        db_session=db_session,
    )

    cat3 = await estimate_cat3_upstream_async(
        estimated_kwh=estimated_kwh,
        region_id=region_id,
        carbon_intensity_gco2_kwh=carbon_intensity_gco2_kwh,
        db_session=db_session,
    )

    cat12 = await estimate_cat12_eol_async(
        hardware_family=cat1.hardware_family,
        resource_share=cat1.resource_share,
        charge_period_start=charge_period_start,
        charge_period_end=charge_period_end,
        db_session=db_session,
    )

    total = cat1.co2e_kg + cat3.co2e_kg + cat12.co2e_kg
    confidence = _aggregate_confidence(cat1, cat3, cat12)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return Scope3Estimate(
        cat1=cat1,
        cat3=cat3,
        cat12=cat12,
        scope3_total_co2e_kg=total,
        confidence=confidence,
        methodology_ref=f"cloudcarbon_scope3_v1_{ts}",
    )


def estimate_scope3_sync(
    cat1: Cat1Result,
    cat3: Cat3Result,
    cat12: Cat12Result,
) -> Scope3Estimate:
    """
    Synchronous Scope 3 aggregator using pre-computed category results.
    Used in unit tests and batch pre-fetch patterns.
    """
    total = cat1.co2e_kg + cat3.co2e_kg + cat12.co2e_kg
    confidence = _aggregate_confidence(cat1, cat3, cat12)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return Scope3Estimate(
        cat1=cat1,
        cat3=cat3,
        cat12=cat12,
        scope3_total_co2e_kg=total,
        confidence=confidence,
        methodology_ref=f"cloudcarbon_scope3_v1_{ts}",
    )
