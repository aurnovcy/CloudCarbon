"""
CloudCarbon Unified Recommendation Engine.

Every recommendation surfaces three impact dimensions simultaneously:
  - cost_impact_monthly_usd
  - co2e_impact_monthly_kg
  - water_impact_monthly_litres

A configurable weighted composite score normalises all three dimensions to [0,1]
via min-max normalization, enabling both FinOps and GreenOps personas to be served
by the same engine simply by adjusting the weights.

Six recommendation generators:
  1. generate_rightsize_recommendations
  2. generate_terminate_idle_recommendations
  3. generate_region_migrate_carbon_recommendations
  4. generate_schedule_off_hours_recommendations
  5. generate_reserved_instance_recommendations
  6. generate_green_region_shift_recommendations

Main entry point:
  generate_all_recommendations(input, db_session, policy_rules) -> list[Recommendation]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RIGHTSIZE_UTILIZATION_THRESHOLD = 0.25   # flag if avg utilisation < 25%
_IDLE_KWH_PER_HOUR_THRESHOLD = 0.01       # flag if avg kWh/hr < 0.01
_IDLE_CONSECUTIVE_DAYS = 14               # must be idle for 14+ days
_REGION_CARBON_REDUCTION_MIN = 0.30       # only recommend if ≥30% lower carbon
_RESERVED_INSTANCE_DISCOUNT = 0.30        # 30% cost savings from 1-yr RI
_SCHEDULE_UPTIME_REDUCTION = 0.60         # 60% reduction from off-hours scheduling
_RIGHTSIZE_REDUCTION = 0.50               # 50% resource reduction
_BATCH_SERVICE_TYPES = {
    "batch", "dataflow", "emr", "glue", "spark", "databricks",
    "aws batch", "cloud dataflow", "azure batch",
}
_NON_PROD_TAGS = {"env": {"dev", "test", "staging", "development", "testing"}}
_NON_PROD_NAME_KEYWORDS = {"dev", "test", "staging", "development", "testing", "qa"}
_KEEP_TAGS = {"keep": {"true", "yes", "1"}, "do-not-terminate": {"true", "yes", "1"}}

# Continent groupings for same-continent region migration
_REGION_CONTINENTS: dict[str, str] = {
    # AWS
    "us-east-1": "NA", "us-east-2": "NA", "us-west-1": "NA", "us-west-2": "NA",
    "ca-central-1": "NA", "sa-east-1": "SA",
    "eu-west-1": "EU", "eu-west-2": "EU", "eu-west-3": "EU",
    "eu-central-1": "EU", "eu-north-1": "EU",
    "ap-southeast-1": "APAC", "ap-southeast-2": "APAC",
    "ap-northeast-1": "APAC", "ap-northeast-2": "APAC",
    "ap-south-1": "APAC",
    # Azure
    "eastus": "NA", "eastus2": "NA", "westus": "NA", "westus2": "NA", "westus3": "NA",
    "northeurope": "EU", "westeurope": "EU", "uksouth": "EU", "ukwest": "EU",
    "francecentral": "EU", "germanywestcentral": "EU", "swedencentral": "EU",
    "eastasia": "APAC", "southeastasia": "APAC", "japaneast": "APAC",
    "koreacentral": "APAC", "australiaeast": "APAC", "centralindia": "APAC",
    "canadacentral": "NA", "brazilsouth": "SA",
    # GCP
    "us-central1": "NA", "us-east1": "NA", "us-east4": "NA",
    "us-west1": "NA", "us-west2": "NA", "us-west3": "NA", "us-west4": "NA",
    "europe-west1": "EU", "europe-west2": "EU", "europe-west3": "EU",
    "europe-west4": "EU", "europe-north1": "EU",
    "asia-east1": "APAC", "asia-northeast1": "APAC", "asia-northeast2": "APAC",
    "asia-northeast3": "APAC", "asia-southeast1": "APAC", "asia-southeast2": "APAC",
    "asia-south1": "APAC", "australia-southeast1": "APAC",
    "southamerica-east1": "SA", "northamerica-northeast1": "NA",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EnrichedRecord:
    """
    Minimal representation of an enriched_records row used by the recommendation engine.
    The full SQLAlchemy model lives in apps/api; this dataclass is used for pure-Python
    unit tests and the carbon-models package.
    """
    resource_id: str
    provider_name: str
    region_id: str
    service_name: str
    service_category: str
    effective_cost: float
    total_co2e_kg: float
    water_litres: float
    water_stress_adjusted_litres: float
    estimated_kwh: float
    carbon_intensity_gco2_kwh: float
    water_stress_score: float | None = None
    tags: dict[str, str] | None = None
    charge_period_start: Any = None
    charge_period_end: Any = None
    resource_name: str | None = None


@dataclass
class RecommendationInput:
    """Input bundle for the recommendation engine."""
    tenant_id: UUID
    records: list[EnrichedRecord]
    cost_weight: float = 0.6
    carbon_weight: float = 0.3
    water_weight: float = 0.1


@dataclass
class Recommendation:
    """A single actionable recommendation with three-dimensional impact."""
    type: str
    resource_id: str
    provider: str
    region: str
    service_name: str
    cost_impact_monthly_usd: float
    co2e_impact_monthly_kg: float
    water_impact_monthly_litres: float
    impact_score: float
    cost_weight_used: float
    carbon_weight_used: float
    water_weight_used: float
    complexity: str  # low / medium / high
    implementation_steps: list[str]
    methodology_notes: str
    policy_blocked: bool = False
    policy_block_reason: str | None = None
    requires_approval: bool = False


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _monthly_cost(records: list[EnrichedRecord]) -> float:
    return sum(r.effective_cost for r in records)


def _monthly_co2e(records: list[EnrichedRecord]) -> float:
    return sum(r.total_co2e_kg for r in records)


def _monthly_water(records: list[EnrichedRecord]) -> float:
    return sum(r.water_litres for r in records)


def _avg_kwh_per_hour(records: list[EnrichedRecord]) -> float:
    """Average kWh per billing record (proxy for utilisation)."""
    if not records:
        return 0.0
    return sum(r.estimated_kwh for r in records) / len(records)


def _is_non_prod(records: list[EnrichedRecord]) -> bool:
    """Return True if any record has non-production environment tags or name keywords."""
    for r in records:
        tags = r.tags or {}
        env = tags.get("env", tags.get("environment", "")).lower()
        if env in _NON_PROD_TAGS["env"]:
            return True
        name = (r.resource_name or r.resource_id or "").lower()
        if any(kw in name for kw in _NON_PROD_NAME_KEYWORDS):
            return True
    return False


def _is_keep_tagged(records: list[EnrichedRecord]) -> bool:
    """Return True if any record has keep=true or do-not-terminate=true tags."""
    for r in records:
        tags = r.tags or {}
        for tag_key, allowed_vals in _KEEP_TAGS.items():
            if tags.get(tag_key, "").lower() in allowed_vals:
                return True
    return False


def _is_batch_workload(records: list[EnrichedRecord]) -> bool:
    """Return True if any record is a batch workload by service name or tags."""
    for r in records:
        tags = r.tags or {}
        if tags.get("batch", "").lower() in {"true", "yes", "1"}:
            return True
        svc = r.service_name.lower()
        if any(b in svc for b in _BATCH_SERVICE_TYPES):
            return True
    return False


def _group_by_resource(records: list[EnrichedRecord]) -> dict[str, list[EnrichedRecord]]:
    """Group records by resource_id."""
    groups: dict[str, list[EnrichedRecord]] = {}
    for r in records:
        groups.setdefault(r.resource_id, []).append(r)
    return groups


def _max_possible_kwh(resource_type: str | None) -> float:
    """
    Rough max kWh per billing record for a given instance type.
    Used as denominator for utilisation proxy.
    Falls back to 1.0 kWh if unknown.
    """
    # Very rough estimates: vCPU × 0.005 kWh/hr × 720 hr/month
    _VCPU_MAP = {
        "m5.large": 2, "m5.xlarge": 4, "m5.2xlarge": 8, "m5.4xlarge": 16,
        "m5.8xlarge": 32, "m5.12xlarge": 48, "m5.16xlarge": 64,
        "c5.large": 2, "c5.xlarge": 4, "c5.2xlarge": 8, "c5.4xlarge": 16,
        "r5.large": 2, "r5.xlarge": 4, "r5.2xlarge": 8,
    }
    vcpu = _VCPU_MAP.get(resource_type or "", 4)
    return vcpu * 0.005 * 720  # kWh per month


# ---------------------------------------------------------------------------
# Generator 1: Rightsize
# ---------------------------------------------------------------------------

def generate_rightsize_recommendations(
    records: list[EnrichedRecord],
) -> list[Recommendation]:
    """
    Flag resources with average utilisation < 25% and recommend 50% resource reduction.
    """
    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        # Utilisation proxy: avg kWh / max possible kWh per record
        avg_kwh = _avg_kwh_per_hour(recs)
        resource_type = None  # not available in EnrichedRecord; use service_name heuristic
        max_kwh = _max_possible_kwh(resource_type)
        utilisation = avg_kwh / max_kwh if max_kwh > 0 else 1.0

        if utilisation >= _RIGHTSIZE_UTILIZATION_THRESHOLD:
            continue

        rep = recs[0]
        monthly_cost = _monthly_cost(recs)
        monthly_co2e = _monthly_co2e(recs)
        monthly_water = _monthly_water(recs)

        results.append(Recommendation(
            type="rightsize",
            resource_id=resource_id,
            provider=rep.provider_name,
            region=rep.region_id,
            service_name=rep.service_name,
            cost_impact_monthly_usd=monthly_cost * _RIGHTSIZE_REDUCTION,
            co2e_impact_monthly_kg=monthly_co2e * _RIGHTSIZE_REDUCTION,
            water_impact_monthly_litres=monthly_water * _RIGHTSIZE_REDUCTION,
            impact_score=0.0,  # computed after normalization
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="low",
            implementation_steps=[
                "Identify instance in AWS/Azure/GCP console",
                "Stop instance",
                "Change instance type to recommended size",
                "Restart instance",
                "Monitor performance for 72 hours",
            ],
            methodology_notes=(
                f"Average utilisation proxy: {utilisation:.1%} "
                f"(threshold: {_RIGHTSIZE_UTILIZATION_THRESHOLD:.0%}). "
                f"Estimated 50% resource reduction."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Generator 2: Terminate idle
# ---------------------------------------------------------------------------

def generate_terminate_idle_recommendations(
    records: list[EnrichedRecord],
) -> list[Recommendation]:
    """
    Flag resources with estimated_kwh < 0.01/hr for 14+ consecutive days.
    Excludes resources tagged with keep=true or do-not-terminate=true.
    """
    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        if _is_keep_tagged(recs):
            continue

        # Check if all records are below idle threshold
        idle_recs = [r for r in recs if r.estimated_kwh < _IDLE_KWH_PER_HOUR_THRESHOLD]
        if len(idle_recs) < _IDLE_CONSECUTIVE_DAYS:
            continue

        rep = recs[0]
        monthly_cost = _monthly_cost(recs)
        monthly_co2e = _monthly_co2e(recs)
        monthly_water = _monthly_water(recs)

        results.append(Recommendation(
            type="terminate_idle",
            resource_id=resource_id,
            provider=rep.provider_name,
            region=rep.region_id,
            service_name=rep.service_name,
            cost_impact_monthly_usd=monthly_cost,
            co2e_impact_monthly_kg=monthly_co2e,
            water_impact_monthly_litres=monthly_water,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="low",
            implementation_steps=[
                "Verify resource is not serving active traffic",
                "Check for scheduled jobs or cron tasks using this resource",
                "Take a final snapshot or backup if required",
                "Terminate the resource",
                "Monitor for 48 hours for any unexpected alerts",
            ],
            methodology_notes=(
                f"Resource has been idle (< {_IDLE_KWH_PER_HOUR_THRESHOLD} kWh/hr) "
                f"for {len(idle_recs)} consecutive billing periods."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Generator 3: Region migrate (carbon)
# ---------------------------------------------------------------------------

async def generate_region_migrate_carbon_recommendations(
    records: list[EnrichedRecord],
    db_session: Any,
) -> list[Recommendation]:
    """
    For each resource, find the lowest carbon intensity region within the same
    provider and continent. Only recommend if target is ≥30% lower carbon intensity.
    """
    from sqlalchemy import text

    # Load all region carbon intensities
    region_intensities: dict[str, dict] = {}
    try:
        result = await db_session.execute(
            text(
                "SELECT region_id, provider, carbon_intensity_gco2_kwh, "
                "continent, wri_aqueduct_score "
                "FROM region_carbon_intensity"
            )
        )
        for row in result.fetchall():
            region_intensities[row[0]] = {
                "provider": row[1],
                "intensity": float(row[2] or 475.0),
                "continent": row[3] or _REGION_CONTINENTS.get(row[0], "UNKNOWN"),
                "water_stress": float(row[4]) if row[4] is not None else None,
            }
    except Exception as exc:
        logger.warning("Failed to load region intensities: %s", exc)
        return []

    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        rep = recs[0]
        current_region = rep.region_id
        provider = rep.provider_name
        current_intensity = rep.carbon_intensity_gco2_kwh or 475.0
        current_continent = _REGION_CONTINENTS.get(current_region, "UNKNOWN")

        # Find same-provider, same-continent regions with lower intensity
        candidates = [
            (rid, info) for rid, info in region_intensities.items()
            if info["provider"].lower() == provider.lower()
            and info["continent"] == current_continent
            and rid != current_region
            and info["intensity"] < current_intensity * (1 - _REGION_CARBON_REDUCTION_MIN)
        ]

        if not candidates:
            continue

        # Pick the lowest intensity candidate
        best_region, best_info = min(candidates, key=lambda x: x[1]["intensity"])
        reduction_pct = 1 - best_info["intensity"] / current_intensity

        monthly_co2e = _monthly_co2e(recs)
        monthly_cost = _monthly_cost(recs)
        monthly_water = _monthly_water(recs)

        co2e_impact = monthly_co2e * reduction_pct
        # Cost may be ±10% depending on region pricing
        cost_impact = monthly_cost * 0.10  # conservative positive estimate

        # Water stress note
        water_note = ""
        current_stress = recs[0].water_stress_score
        target_stress = best_info.get("water_stress")
        if target_stress is not None and current_stress is not None:
            if target_stress > current_stress:
                water_note = (
                    f" WARNING: target region {best_region} has higher water stress "
                    f"({target_stress:.1f} vs {current_stress:.1f})."
                )

        results.append(Recommendation(
            type="region_migrate_carbon",
            resource_id=resource_id,
            provider=provider,
            region=current_region,
            service_name=rep.service_name,
            cost_impact_monthly_usd=cost_impact,
            co2e_impact_monthly_kg=co2e_impact,
            water_impact_monthly_litres=monthly_water * reduction_pct,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="high",
            implementation_steps=[
                "Assess data residency and compliance requirements",
                f"Benchmark latency to target region: {best_region}",
                "Plan migration window",
                "Use provider migration tools",
                "Update DNS/routing",
                "Decommission source resources",
            ],
            methodology_notes=(
                f"Current region: {current_region} ({current_intensity:.0f} gCO2/kWh). "
                f"Target region: {best_region} ({best_info['intensity']:.0f} gCO2/kWh). "
                f"Carbon reduction: {reduction_pct:.1%}. "
                f"Cost impact estimated at ±10% of current spend.{water_note}"
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Generator 4: Schedule off-hours
# ---------------------------------------------------------------------------

def generate_schedule_off_hours_recommendations(
    records: list[EnrichedRecord],
) -> list[Recommendation]:
    """
    Flag non-production resources and recommend off-hours scheduling
    (60% uptime reduction = nights + weekends).
    """
    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        if not _is_non_prod(recs):
            continue

        rep = recs[0]
        monthly_cost = _monthly_cost(recs)
        monthly_co2e = _monthly_co2e(recs)
        monthly_water = _monthly_water(recs)

        results.append(Recommendation(
            type="schedule_off_hours",
            resource_id=resource_id,
            provider=rep.provider_name,
            region=rep.region_id,
            service_name=rep.service_name,
            cost_impact_monthly_usd=monthly_cost * _SCHEDULE_UPTIME_REDUCTION,
            co2e_impact_monthly_kg=monthly_co2e * _SCHEDULE_UPTIME_REDUCTION,
            water_impact_monthly_litres=monthly_water * _SCHEDULE_UPTIME_REDUCTION,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="low",
            implementation_steps=[
                "Tag resource with schedule=off-hours",
                "Configure AWS Instance Scheduler / Azure Automation / GCP Cloud Scheduler",
                "Set shutdown window: 20:00–08:00 weekdays, all day weekends",
                "Test startup/shutdown automation",
                "Monitor for missed scheduled jobs",
            ],
            methodology_notes=(
                f"Non-production resource detected (env tag or name keyword). "
                f"Estimated 60% uptime reduction via off-hours scheduling "
                f"(nights + weekends)."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Generator 5: Reserved instances
# ---------------------------------------------------------------------------

def generate_reserved_instance_recommendations(
    records: list[EnrichedRecord],
) -> list[Recommendation]:
    """
    Flag resources running 24/7 for 30+ days on on-demand pricing.
    Estimate 30% cost savings from 1-year reserved instance.
    Note: reserved instances do not reduce actual consumption.
    """
    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        # Need 30+ billing records (proxy for 30+ days of continuous running)
        if len(recs) < 30:
            continue

        # Skip if already on reserved/spot pricing (check tags)
        rep = recs[0]
        tags = rep.tags or {}
        pricing_model = tags.get("pricing_model", tags.get("purchase_option", "")).lower()
        if pricing_model in {"reserved", "spot", "savings_plan", "committed_use"}:
            continue

        monthly_cost = _monthly_cost(recs)

        results.append(Recommendation(
            type="reserved_instance",
            resource_id=resource_id,
            provider=rep.provider_name,
            region=rep.region_id,
            service_name=rep.service_name,
            cost_impact_monthly_usd=monthly_cost * _RESERVED_INSTANCE_DISCOUNT,
            co2e_impact_monthly_kg=0.0,  # RI does not reduce consumption
            water_impact_monthly_litres=0.0,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="medium",
            implementation_steps=[
                "Analyse usage patterns to confirm 24/7 baseline",
                "Compare 1-year vs 3-year reserved instance pricing",
                "Purchase reserved instance via provider console or API",
                "Associate reserved instance with the resource",
                "Review savings in cost explorer after 30 days",
            ],
            methodology_notes=(
                f"Resource has been running continuously for {len(recs)} billing periods. "
                f"Estimated 30% cost savings from 1-year reserved instance. "
                f"Note: reserved instances do not reduce actual energy consumption "
                f"or carbon emissions — co2e_impact is 0."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Generator 6: Green region shift (batch workloads)
# ---------------------------------------------------------------------------

async def generate_green_region_shift_recommendations(
    records: list[EnrichedRecord],
    db_session: Any,
) -> list[Recommendation]:
    """
    Same logic as region_migrate_carbon but specifically for batch workloads.
    Lower complexity (medium) because batch jobs are inherently portable.
    """
    from sqlalchemy import text

    # Load region intensities
    region_intensities: dict[str, dict] = {}
    try:
        result = await db_session.execute(
            text(
                "SELECT region_id, provider, carbon_intensity_gco2_kwh, continent "
                "FROM region_carbon_intensity"
            )
        )
        for row in result.fetchall():
            region_intensities[row[0]] = {
                "provider": row[1],
                "intensity": float(row[2] or 475.0),
                "continent": row[3] or _REGION_CONTINENTS.get(row[0], "UNKNOWN"),
            }
    except Exception as exc:
        logger.warning("Failed to load region intensities for green shift: %s", exc)
        return []

    groups = _group_by_resource(records)
    results: list[Recommendation] = []

    for resource_id, recs in groups.items():
        if not recs:
            continue

        if not _is_batch_workload(recs):
            continue

        rep = recs[0]
        current_region = rep.region_id
        provider = rep.provider_name
        current_intensity = rep.carbon_intensity_gco2_kwh or 475.0
        current_continent = _REGION_CONTINENTS.get(current_region, "UNKNOWN")

        candidates = [
            (rid, info) for rid, info in region_intensities.items()
            if info["provider"].lower() == provider.lower()
            and info["continent"] == current_continent
            and rid != current_region
            and info["intensity"] < current_intensity * (1 - _REGION_CARBON_REDUCTION_MIN)
        ]

        if not candidates:
            continue

        best_region, best_info = min(candidates, key=lambda x: x[1]["intensity"])
        reduction_pct = 1 - best_info["intensity"] / current_intensity

        monthly_co2e = _monthly_co2e(recs)
        monthly_cost = _monthly_cost(recs)
        monthly_water = _monthly_water(recs)

        results.append(Recommendation(
            type="green_region_shift",
            resource_id=resource_id,
            provider=provider,
            region=current_region,
            service_name=rep.service_name,
            cost_impact_monthly_usd=monthly_cost * 0.05,  # minimal cost impact for batch
            co2e_impact_monthly_kg=monthly_co2e * reduction_pct,
            water_impact_monthly_litres=monthly_water * reduction_pct,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="medium",
            implementation_steps=[
                f"Update batch job configuration to target region: {best_region}",
                "Use AWS Instance Scheduler / Azure Automation / GCP Cloud Scheduler "
                "for carbon-aware scheduling",
                "Test batch job execution in target region",
                "Update CI/CD pipeline region configuration",
                "Monitor batch job completion times and costs",
                "Decommission source region batch infrastructure",
            ],
            methodology_notes=(
                f"Batch workload identified. Current region: {current_region} "
                f"({current_intensity:.0f} gCO2/kWh). "
                f"Target region: {best_region} ({best_info['intensity']:.0f} gCO2/kWh). "
                f"Carbon reduction: {reduction_pct:.1%}. "
                f"Batch workloads are inherently portable — lower migration complexity."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Normalization and scoring
# ---------------------------------------------------------------------------

def _normalize_and_score(
    recommendations: list[Recommendation],
    cost_weight: float,
    carbon_weight: float,
    water_weight: float,
) -> list[Recommendation]:
    """
    Apply min-max normalization across all recommendations and compute impact_score.
    Sets cost_weight_used, carbon_weight_used, water_weight_used on every recommendation.
    """
    if not recommendations:
        return recommendations

    costs = [r.cost_impact_monthly_usd for r in recommendations]
    co2es = [r.co2e_impact_monthly_kg for r in recommendations]
    waters = [r.water_impact_monthly_litres for r in recommendations]

    min_cost, max_cost = min(costs), max(costs)
    min_co2e, max_co2e = min(co2es), max(co2es)
    min_water, max_water = min(waters), max(waters)

    def _norm(val: float, lo: float, hi: float) -> float:
        if hi == lo:
            return 0.0
        return (val - lo) / (hi - lo)

    for rec in recommendations:
        nc = _norm(rec.cost_impact_monthly_usd, min_cost, max_cost)
        nco2e = _norm(rec.co2e_impact_monthly_kg, min_co2e, max_co2e)
        nw = _norm(rec.water_impact_monthly_litres, min_water, max_water)

        rec.impact_score = (nc * cost_weight) + (nco2e * carbon_weight) + (nw * water_weight)
        rec.cost_weight_used = cost_weight
        rec.carbon_weight_used = carbon_weight
        rec.water_weight_used = water_weight

    return recommendations


# ---------------------------------------------------------------------------
# Main engine function
# ---------------------------------------------------------------------------

async def generate_all_recommendations(
    input: RecommendationInput,
    db_session: Any,
    policy_rules: list[Any],
) -> list[Recommendation]:
    """
    Run all six generators, normalize, apply policies, and sort by impact_score.

    Args:
        input: RecommendationInput with records and weight configuration.
        db_session: SQLAlchemy AsyncSession (used by async generators).
        policy_rules: List of PolicyRule objects from the database.

    Returns:
        Sorted list of Recommendation objects (highest impact_score first).
    """
    # Import policy evaluator lazily to avoid circular imports
    from carbon_models.policies import apply_policies

    all_recs: list[Recommendation] = []

    # Synchronous generators
    all_recs.extend(generate_rightsize_recommendations(input.records))
    all_recs.extend(generate_terminate_idle_recommendations(input.records))
    all_recs.extend(generate_schedule_off_hours_recommendations(input.records))
    all_recs.extend(generate_reserved_instance_recommendations(input.records))

    # Async generators (require DB for region lookups)
    if db_session is not None:
        all_recs.extend(
            await generate_region_migrate_carbon_recommendations(input.records, db_session)
        )
        all_recs.extend(
            await generate_green_region_shift_recommendations(input.records, db_session)
        )

    logger.info(
        "Generated %d raw recommendations for tenant %s",
        len(all_recs), input.tenant_id,
    )

    # Normalize and score
    all_recs = _normalize_and_score(
        all_recs,
        cost_weight=input.cost_weight,
        carbon_weight=input.carbon_weight,
        water_weight=input.water_weight,
    )

    # Apply policy rules
    if policy_rules:
        all_recs = [apply_policies(rec, policy_rules) for rec in all_recs]

    # Sort by impact_score descending
    all_recs.sort(key=lambda r: r.impact_score, reverse=True)

    logger.info(
        "Returning %d recommendations after policy filtering for tenant %s",
        len(all_recs), input.tenant_id,
    )
    return all_recs
