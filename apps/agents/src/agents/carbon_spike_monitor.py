"""
CarbonSpikeMonitorAgent — monitors for sudden carbon emission spikes.

detect() logic:
  - Query enriched_records for the tenant, last 7 days vs. prior 7 days
  - For each service+region: compute co2e_pct_change
  - Flag if co2e_pct_change > threshold (default 25%)
  - Also flag if carbon_intensity_gco2_kwh increased by > 15% (grid dirtier)

act() is proactive-only — always returns notification_sent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from agent_types import AgentAction, AgentFinding
from base_agent import BaseAgent

logger = logging.getLogger(__name__)


class CarbonSpikeMonitorAgent(BaseAgent):
    """Monitors for sudden carbon emission spikes and grid intensity changes."""

    async def detect(self) -> list[AgentFinding]:
        spike_threshold = float(
            self.config.thresholds.get("co2e_spike_pct", 0.25)
        )
        intensity_threshold = float(
            self.config.thresholds.get("carbon_intensity_change_pct", 0.15)
        )

        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(days=7)
        prior_start = now - timedelta(days=14)

        result = await self.db.execute(
            text(
                """
                SELECT
                    service_name,
                    region_id AS region,
                    SUM(CASE WHEN charge_period_start >= :recent_start
                             THEN total_co2e_kg ELSE 0 END)               AS co2e_recent,
                    SUM(CASE WHEN charge_period_start < :recent_start
                             THEN total_co2e_kg ELSE 0 END)               AS co2e_prior,
                    AVG(CASE WHEN charge_period_start >= :recent_start
                             THEN carbon_intensity_gco2_kwh END)          AS intensity_recent,
                    AVG(CASE WHEN charge_period_start < :recent_start
                             THEN carbon_intensity_gco2_kwh END)          AS intensity_prior
                FROM enriched_records
                WHERE tenant_id = :tenant_id
                  AND charge_period_start >= :prior_start
                GROUP BY service_name, region_id
                HAVING SUM(total_co2e_kg) > 0
                """
            ),
            {
                "tenant_id": str(self.config.tenant_id),
                "recent_start": recent_start,
                "prior_start": prior_start,
            },
        )
        rows = result.fetchall()

        findings: list[AgentFinding] = []

        for row in rows:
            service = row.service_name or "unknown"
            region = row.region or "unknown"
            co2e_recent = float(row.co2e_recent or 0)
            co2e_prior = float(row.co2e_prior or 0)
            intensity_recent = float(row.intensity_recent or 0)
            intensity_prior = float(row.intensity_prior or 0)

            # CO2e spike check
            if co2e_prior > 0:
                pct_change = (co2e_recent - co2e_prior) / co2e_prior
                if pct_change > spike_threshold:
                    severity = (
                        "high" if pct_change > spike_threshold * 2
                        else "medium" if pct_change > spike_threshold * 1.5
                        else "low"
                    )
                    findings.append(
                        AgentFinding(
                            agent_type="carbon_spike_monitor",
                            tenant_id=self.config.tenant_id,
                            resource_id=None,
                            service_name=service,
                            region=region,
                            metric="co2e",
                            current_value=co2e_recent,
                            baseline_mean=co2e_prior,
                            z_score=None,
                            severity=severity,
                            description=(
                                f"Carbon spike in {service}/{region}: "
                                f"+{pct_change:.1%} vs prior 7 days "
                                f"({co2e_recent:.1f} vs {co2e_prior:.1f} kg CO2e)"
                            ),
                            metadata={
                                "co2e_recent_7d": co2e_recent,
                                "co2e_prior_7d": co2e_prior,
                                "pct_change": pct_change,
                            },
                        )
                    )

            # Carbon intensity change check (grid became dirtier)
            if intensity_prior > 0:
                intensity_change = (intensity_recent - intensity_prior) / intensity_prior
                if intensity_change > intensity_threshold:
                    findings.append(
                        AgentFinding(
                            agent_type="carbon_spike_monitor",
                            tenant_id=self.config.tenant_id,
                            resource_id=None,
                            service_name=service,
                            region=region,
                            metric="carbon_intensity",
                            current_value=intensity_recent,
                            baseline_mean=intensity_prior,
                            z_score=None,
                            severity="medium",
                            description=(
                                f"Grid carbon intensity increased in {region}: "
                                f"+{intensity_change:.1%} "
                                f"({intensity_recent:.0f} vs {intensity_prior:.0f} gCO2/kWh)"
                            ),
                            metadata={
                                "intensity_recent": intensity_recent,
                                "intensity_prior": intensity_prior,
                                "carbon_intensity_change": intensity_change,
                            },
                        )
                    )

        logger.info(
            "CarbonSpikeMonitor found %d spikes for tenant %s",
            len(findings),
            self.config.tenant_id,
        )
        return findings

    async def act(self, finding: AgentFinding) -> AgentAction:
        """Carbon spike monitoring is proactive-only — no infrastructure action."""
        return AgentAction(
            finding=finding,
            status="notification_sent",
            action_type="notify",
        )
