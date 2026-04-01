"""
CloudCarbon BaseAgent Abstract Class.

All five agents inherit from this class. The run() method implements the
detect → act → notify lifecycle with dry-run and approval-gate safety.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_types import AgentAction, AgentConfig, AgentFinding, AgentRunSummary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for all CloudCarbon autonomous agents.

    Subclasses must implement:
      - detect() → list[AgentFinding]
      - act(finding) → AgentAction

    The run() method orchestrates the full lifecycle:
      1. detect() — analyse data, return findings
      2. For each finding: act() if dry_run=False and approval not required,
         otherwise log as dry_run or pending_approval
      3. send_notifications() — deliver alerts via configured channels
    """

    def __init__(
        self,
        config: AgentConfig,
        db: "AsyncSession",
        dry_run: bool,
    ) -> None:
        self.config = config
        self.db = db
        self.dry_run = dry_run
        self.actions_taken: list[AgentAction] = []
        self._started_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def detect(self) -> list[AgentFinding]:
        """
        Analyse data for the tenant and return a list of findings.
        Must NOT take any infrastructure action.
        """

    @abstractmethod
    async def act(self, finding: AgentFinding) -> AgentAction:
        """
        Take action on a single finding.
        Only called when dry_run=False and require_approval_for_actions=False.
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> AgentRunSummary:
        """
        Execute the full agent lifecycle: detect → act/log → notify.
        Returns an AgentRunSummary with all actions taken.
        """
        self._started_at = datetime.now(timezone.utc)
        findings: list[AgentFinding] = []
        error: str | None = None

        try:
            findings = await self.detect()
            logger.info(
                "Agent %s detected %d findings for tenant %s",
                self.config.agent_type,
                len(findings),
                self.config.tenant_id,
            )

            for finding in findings:
                if not self.dry_run and not self.config.require_approval_for_actions:
                    try:
                        action = await self.act(finding)
                        self.actions_taken.append(action)
                    except Exception as exc:
                        logger.exception(
                            "Agent %s act() failed for finding %s: %s",
                            self.config.agent_type,
                            finding.description,
                            exc,
                        )
                        self.actions_taken.append(
                            AgentAction(
                                finding=finding,
                                status="failed",
                                action_type="unknown",
                                error=str(exc),
                            )
                        )
                else:
                    # Dry-run or approval required — log intent without acting
                    status = "dry_run" if self.dry_run else "pending_approval"
                    self.actions_taken.append(
                        AgentAction(
                            finding=finding,
                            status=status,
                            action_type=self._infer_action_type(finding),
                        )
                    )

            await self.send_notifications(findings)

        except Exception as exc:
            logger.exception(
                "Agent %s run() failed: %s",
                self.config.agent_type,
                exc,
            )
            error = str(exc)

        return AgentRunSummary(
            findings=len(findings),
            actions=self.actions_taken,
            started_at=self._started_at,
            completed_at=datetime.now(timezone.utc),
            error=error,
        )

    # ------------------------------------------------------------------
    # Notification dispatch
    # ------------------------------------------------------------------

    async def send_notifications(self, findings: list[AgentFinding]) -> None:
        """Dispatch findings to all configured notification channels."""
        if not findings:
            return

        # Import here to avoid circular imports at module load time
        from notifications import notify_email, notify_slack, notify_webhook

        for channel in self.config.notification_channels:
            channel_type = channel.get("type", "").lower()
            try:
                if channel_type == "slack":
                    await notify_slack(
                        webhook_url=channel["webhook_url"],
                        findings=findings,
                        agent_type=self.config.agent_type,
                    )
                elif channel_type == "email":
                    await notify_email(
                        address=channel["address"],
                        findings=findings,
                        agent_type=self.config.agent_type,
                    )
                elif channel_type == "webhook":
                    await notify_webhook(
                        url=channel["url"],
                        payload={
                            "agent_type": self.config.agent_type,
                            "tenant_id": str(self.config.tenant_id),
                            "findings": [
                                {
                                    "service_name": f.service_name,
                                    "region": f.region,
                                    "metric": f.metric,
                                    "severity": f.severity,
                                    "description": f.description,
                                    "current_value": f.current_value,
                                    "baseline_mean": f.baseline_mean,
                                }
                                for f in findings
                            ],
                        },
                        signing_secret=channel.get("signing_secret", ""),
                    )
                else:
                    logger.warning("Unknown notification channel type: %s", channel_type)
            except Exception as exc:
                logger.exception(
                    "Notification delivery failed for channel %s: %s",
                    channel_type,
                    exc,
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_action_type(self, finding: AgentFinding) -> str:
        """Infer the action type from the agent type and finding metric."""
        mapping = {
            "anomaly_detector": "notify",
            "carbon_spike_monitor": "notify",
            "rightsizing_agent": "rightsize",
            "idle_reaper": "stop_instance",
            "green_scheduler": "schedule_recommendation",
        }
        return mapping.get(self.config.agent_type, "notify")
