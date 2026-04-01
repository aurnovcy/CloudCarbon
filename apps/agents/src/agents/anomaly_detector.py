"""
AnomalyDetectorAgent — detects cost, carbon, and water anomalies using
Z-score and IQR methods over a 60-day rolling window.

detect() logic:
  - Query enriched_records for the tenant, last 60 days, aggregated daily
    by service + region
  - For each time series, compute rolling 30-day mean and std
  - Flag if Z-score of the most recent 3 days > threshold (default 3.0)
  - Also flag using IQR: value > Q3 + 1.5 × IQR
  - Apply to: cost_usd (effective_cost), total_co2e_kg, water_litres

act() is proactive-only — always returns notification_sent.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import text

from agent_types import AgentAction, AgentConfig, AgentFinding
from base_agent import BaseAgent

logger = logging.getLogger(__name__)

_METRICS = [
    ("cost_usd", "effective_cost"),
    ("co2e", "total_co2e_kg"),
    ("water", "water_litres"),
]


class AnomalyDetectorAgent(BaseAgent):
    """Detects cost, carbon, and water anomalies using Z-score and IQR methods."""

    async def detect(self) -> list[AgentFinding]:
        zscore_threshold = float(
            self.config.thresholds.get("zscore_threshold", 3.0)
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)

        result = await self.db.execute(
            text(
                """
                SELECT
                    service_name,
                    region_id AS region,
                    DATE(charge_period_start) AS day,
                    SUM(effective_cost)    AS cost_usd,
                    SUM(total_co2e_kg)     AS co2e,
                    SUM(water_litres)      AS water
                FROM enriched_records
                WHERE tenant_id = :tenant_id
                  AND charge_period_start >= :cutoff
                GROUP BY service_name, region_id, DATE(charge_period_start)
                ORDER BY service_name, region_id, day
                """
            ),
            {"tenant_id": str(self.config.tenant_id), "cutoff": cutoff},
        )
        rows = result.fetchall()

        # Group into time series: (service, region, metric) → list of daily values
        series: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for row in rows:
            key_base = (row.service_name or "unknown", row.region or "unknown")
            series[(*key_base, "cost_usd")].append(float(row.cost_usd or 0))
            series[(*key_base, "co2e")].append(float(row.co2e or 0))
            series[(*key_base, "water")].append(float(row.water or 0))

        findings: list[AgentFinding] = []

        for (service, region, metric), values in series.items():
            if len(values) < 7:
                continue  # Not enough data for meaningful statistics

            arr = np.array(values, dtype=float)
            # Use the last 30 days as baseline (or all available if < 30)
            baseline = arr[:-3] if len(arr) > 3 else arr
            recent = arr[-3:]  # Last 3 days

            mean = float(np.mean(baseline))
            std = float(np.std(baseline))

            # IQR method
            q1, q3 = float(np.percentile(baseline, 25)), float(np.percentile(baseline, 75))
            iqr = q3 - q1
            iqr_upper = q3 + 1.5 * iqr

            for val in recent:
                z = (val - mean) / std if std > 0 else 0.0
                is_zscore_anomaly = abs(z) > zscore_threshold
                is_iqr_anomaly = val > iqr_upper and iqr > 0

                if not (is_zscore_anomaly or is_iqr_anomaly):
                    continue

                severity = (
                    "high" if abs(z) > zscore_threshold * 1.5
                    else "medium" if abs(z) > zscore_threshold
                    else "low"
                )

                findings.append(
                    AgentFinding(
                        agent_type="anomaly_detector",
                        tenant_id=self.config.tenant_id,
                        resource_id=None,
                        service_name=service,
                        region=region,
                        metric=metric,
                        current_value=val,
                        baseline_mean=mean,
                        z_score=z,
                        severity=severity,
                        description=(
                            f"{metric} anomaly in {service}/{region}: "
                            f"value={val:.2f}, mean={mean:.2f}, z={z:.2f}"
                        ),
                        metadata={
                            "iqr_upper": iqr_upper,
                            "is_zscore": is_zscore_anomaly,
                            "is_iqr": is_iqr_anomaly,
                        },
                    )
                )

        logger.info(
            "AnomalyDetector found %d anomalies for tenant %s",
            len(findings),
            self.config.tenant_id,
        )
        return findings

    async def act(self, finding: AgentFinding) -> AgentAction:
        """Anomaly detection is proactive-only — no infrastructure action."""
        return AgentAction(
            finding=finding,
            status="notification_sent",
            action_type="notify",
        )
