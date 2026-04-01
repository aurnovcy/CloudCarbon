"""
GreenSchedulerAgent — recommends greener regions for batch workloads.

detect() logic:
  - Query enriched_records for batch workloads (batch=true tag, or known batch services)
  - For each batch workload, look up alternative regions with lower carbon intensity
    in the same continent (≥30% lower intensity, not significantly worse water stress)
  - Calculate projected CO2e savings and cost delta

act() logic:
  - Always dry-run only (even if dry_run=False) — emits a structured JSON recommendation
  - Never modifies infrastructure directly
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from agent_types import AgentAction, AgentFinding
from base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Known batch service names (case-insensitive substring match)
_BATCH_SERVICE_KEYWORDS = {
    "batch", "dataflow", "glue", "emr", "spark", "databricks",
    "data factory", "synapse", "bigquery", "dataproc",
}

# Continent groupings for region comparison
_REGION_CONTINENTS: dict[str, str] = {
    # AWS
    "us-east-1": "north_america", "us-east-2": "north_america",
    "us-west-1": "north_america", "us-west-2": "north_america",
    "ca-central-1": "north_america",
    "eu-west-1": "europe", "eu-west-2": "europe", "eu-west-3": "europe",
    "eu-central-1": "europe", "eu-north-1": "europe",
    "ap-southeast-1": "asia_pacific", "ap-southeast-2": "asia_pacific",
    "ap-northeast-1": "asia_pacific", "ap-south-1": "asia_pacific",
    # Azure
    "eastus": "north_america", "westus": "north_america", "westus2": "north_america",
    "northeurope": "europe", "westeurope": "europe", "uksouth": "europe",
    "southeastasia": "asia_pacific", "australiaeast": "asia_pacific",
    # GCP
    "us-central1": "north_america", "us-east1": "north_america",
    "europe-west1": "europe", "europe-west4": "europe",
    "asia-east1": "asia_pacific", "asia-southeast1": "asia_pacific",
}


class GreenSchedulerAgent(BaseAgent):
    """Recommends greener regions for batch workloads."""

    async def detect(self) -> list[AgentFinding]:
        intensity_improvement_threshold = float(
            self.config.thresholds.get("min_intensity_improvement_pct", 0.30)
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        # Query batch workloads
        result = await self.db.execute(
            text(
                """
                SELECT
                    fr.resource_id,
                    fr.service_name,
                    er.region_id AS region,
                    fr.provider_name AS provider,
                    fr.tags,
                    AVG(er.carbon_intensity_gco2_kwh) AS avg_intensity,
                    AVG(er.water_stress_score)         AS avg_water_stress,
                    SUM(er.total_co2e_kg)              AS total_co2e,
                    SUM(er.estimated_kwh)              AS total_kwh,
                    SUM(fr.effective_cost)             AS total_cost
                FROM focus_records fr
                JOIN enriched_records er
                    ON er.focus_record_id = fr.id
                    AND er.tenant_id = fr.tenant_id
                WHERE fr.tenant_id = :tenant_id
                  AND fr.charge_period_start >= :cutoff
                GROUP BY fr.resource_id, fr.service_name, er.region_id, fr.provider_name, fr.tags
                HAVING SUM(er.estimated_kwh) > 0
                """
            ),
            {"tenant_id": str(self.config.tenant_id), "cutoff": cutoff},
        )
        rows = result.fetchall()

        # Load region carbon intensity reference data
        intensity_ref = await self._load_region_intensities()

        findings: list[AgentFinding] = []

        for row in rows:
            tags: dict = row.tags or {}
            service_lower = (row.service_name or "").lower()

            # Check if this is a batch workload
            is_batch = tags.get("batch", "").lower() in {"true", "yes", "1"}
            is_batch = is_batch or any(kw in service_lower for kw in _BATCH_SERVICE_KEYWORDS)

            if not is_batch:
                continue

            current_region = row.region or "unknown"
            current_intensity = float(row.avg_intensity or 0)
            current_water_stress = float(row.avg_water_stress or 0)
            total_kwh = float(row.total_kwh or 0)
            total_co2e = float(row.total_co2e or 0)

            if current_intensity <= 0 or total_kwh <= 0:
                continue

            # Find a greener alternative in the same continent
            continent = _REGION_CONTINENTS.get(current_region)
            best_alt = self._find_best_alternative(
                current_region=current_region,
                current_intensity=current_intensity,
                current_water_stress=current_water_stress,
                intensity_ref=intensity_ref,
                continent=continent,
                improvement_threshold=intensity_improvement_threshold,
            )

            if best_alt is None:
                continue

            alt_region, alt_intensity, alt_water_stress = best_alt
            improvement_pct = (current_intensity - alt_intensity) / current_intensity
            co2e_savings = total_kwh * (current_intensity - alt_intensity) / 1000  # kg

            findings.append(
                AgentFinding(
                    agent_type="green_scheduler",
                    tenant_id=self.config.tenant_id,
                    resource_id=row.resource_id,
                    service_name=row.service_name or "unknown",
                    region=current_region,
                    metric="co2e",
                    current_value=current_intensity,
                    baseline_mean=alt_intensity,
                    z_score=None,
                    severity=(
                        "high" if improvement_pct > 0.5
                        else "medium" if improvement_pct > 0.3
                        else "low"
                    ),
                    description=(
                        f"Batch workload {row.resource_id} in {current_region} "
                        f"({current_intensity:.0f} gCO2/kWh) could move to {alt_region} "
                        f"({alt_intensity:.0f} gCO2/kWh, -{improvement_pct:.0%} intensity)"
                    ),
                    metadata={
                        "current_region": current_region,
                        "recommended_region": alt_region,
                        "current_intensity": current_intensity,
                        "recommended_intensity": alt_intensity,
                        "improvement_pct": improvement_pct,
                        "co2e_savings_kg_monthly": co2e_savings,
                        "current_water_stress": current_water_stress,
                        "recommended_water_stress": alt_water_stress,
                        "provider": row.provider,
                    },
                )
            )

        logger.info(
            "GreenScheduler found %d batch workload opportunities for tenant %s",
            len(findings),
            self.config.tenant_id,
        )
        return findings

    async def act(self, finding: AgentFinding) -> AgentAction:
        """
        Green scheduler always emits a structured JSON recommendation.
        Never modifies infrastructure directly.
        """
        recommendation = {
            "workload_id": finding.resource_id,
            "service_name": finding.service_name,
            "current_region": finding.metadata.get("current_region"),
            "recommended_region": finding.metadata.get("recommended_region"),
            "current_intensity_gco2_kwh": finding.metadata.get("current_intensity"),
            "recommended_intensity_gco2_kwh": finding.metadata.get("recommended_intensity"),
            "improvement_pct": finding.metadata.get("improvement_pct"),
            "co2e_savings_kg_monthly": finding.metadata.get("co2e_savings_kg_monthly"),
            "current_water_stress": finding.metadata.get("current_water_stress"),
            "recommended_water_stress": finding.metadata.get("recommended_water_stress"),
            "implementation_guide_url": "docs/green-scheduling.md",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return AgentAction(
            finding=finding,
            status="dry_run",  # Always dry_run — never modifies infrastructure
            action_type="schedule_recommendation",
            provider=finding.metadata.get("provider", ""),
            resource_id=finding.resource_id or "",
            before_state={},
            after_state=recommendation,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_region_intensities(self) -> dict[str, dict]:
        """Load carbon intensity and water stress reference data."""
        result = await self.db.execute(
            text(
                "SELECT region_id, carbon_intensity_gco2_kwh, water_stress_score "
                "FROM region_carbon_intensity"
            )
        )
        return {
            row.region_id: {
                "intensity": float(row.carbon_intensity_gco2_kwh or 0),
                "water_stress": float(row.water_stress_score or 0),
            }
            for row in result.fetchall()
        }

    def _find_best_alternative(
        self,
        current_region: str,
        current_intensity: float,
        current_water_stress: float,
        intensity_ref: dict[str, dict],
        continent: str | None,
        improvement_threshold: float,
    ) -> tuple[str, float, float] | None:
        """
        Find the lowest-intensity region in the same continent that is at least
        improvement_threshold better and does not have significantly worse water stress.
        Returns (region_id, intensity, water_stress) or None.
        """
        best: tuple[str, float, float] | None = None
        best_intensity = current_intensity * (1 - improvement_threshold)

        for region_id, data in intensity_ref.items():
            if region_id == current_region:
                continue

            # Same continent check
            if continent and _REGION_CONTINENTS.get(region_id) != continent:
                continue

            alt_intensity = data["intensity"]
            alt_water_stress = data["water_stress"]

            if alt_intensity <= 0:
                continue

            # Must be at least improvement_threshold better
            if alt_intensity >= current_intensity * (1 - improvement_threshold):
                continue

            # Water stress must not be significantly worse (allow up to 1.0 increase)
            if current_water_stress > 0 and alt_water_stress > current_water_stress + 1.0:
                continue

            if alt_intensity < best_intensity:
                best_intensity = alt_intensity
                best = (region_id, alt_intensity, alt_water_stress)

        return best
