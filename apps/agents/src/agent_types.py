"""
CloudCarbon Agent Type Definitions.

Shared dataclasses used by both apps/agents (runner) and apps/api (control API).
These are pure-Python dataclasses with no SQLAlchemy dependency so they can be
imported in any context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# Agent configuration (mirrors the agent_configs DB row)
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Mirrors the agent_configs database row."""
    id: UUID
    tenant_id: UUID
    agent_type: str
    enabled: bool
    dry_run: bool
    cron_expression: str | None
    thresholds: dict[str, Any]
    notification_channels: list[dict[str, Any]]
    require_approval_for_actions: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Agent findings and actions
# ---------------------------------------------------------------------------

@dataclass
class AgentFinding:
    """A single anomaly or opportunity detected by an agent."""
    agent_type: str
    tenant_id: UUID
    resource_id: str | None
    service_name: str
    region: str
    metric: str                    # "cost" | "co2e" | "water" | "idle" | "carbon_intensity"
    current_value: float
    baseline_mean: float | None
    z_score: float | None
    severity: str                  # "low" | "medium" | "high"
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentAction:
    """A single action taken (or logged in dry-run) by an agent."""
    finding: AgentFinding
    status: str                    # "executed" | "dry_run" | "pending_approval" | "notification_sent" | "failed"
    action_type: str = ""          # "rightsize" | "stop_instance" | "schedule_recommendation" | "notify"
    provider: str = ""
    resource_id: str = ""
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    executed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentRunSummary:
    """Summary returned by BaseAgent.run()."""
    findings: int
    actions: list[AgentAction]
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    error: str | None = None

    @property
    def actions_taken_json(self) -> list[dict[str, Any]]:
        """Serialise actions to a JSON-safe list for storage in agent_runs.actions_taken."""
        result = []
        for a in self.actions:
            result.append({
                "status": a.status,
                "action_type": a.action_type,
                "provider": a.provider,
                "resource_id": a.resource_id,
                "finding": {
                    "service_name": a.finding.service_name,
                    "region": a.finding.region,
                    "metric": a.finding.metric,
                    "current_value": a.finding.current_value,
                    "baseline_mean": a.finding.baseline_mean,
                    "z_score": a.finding.z_score,
                    "severity": a.finding.severity,
                    "description": a.finding.description,
                },
                "before_state": a.before_state,
                "after_state": a.after_state,
                "error": a.error,
                "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            })
        return result
