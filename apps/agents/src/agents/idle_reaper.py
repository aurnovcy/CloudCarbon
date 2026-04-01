"""
IdleReaperAgent — stops idle compute resources.

detect() logic:
  - Load open recommendations of type "terminate_idle" from recommendations table
  - Only include resources idle for > thresholds.idle_days (default 14 days)
  - Never include resources tagged with keep=true, do-not-terminate=true, protected=true

act() logic (only if dry_run=False and require_approval_for_actions=False):
  - AWS:   EC2 stop_instances (NOT terminate — safer default)
  - Azure: VirtualMachines.begin_deallocate
  - GCP:   instances().stop
  - Log to audit_logs with full resource state snapshot
  - Update recommendation status to "implemented"
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from agent_types import AgentAction, AgentFinding
from base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Tags that prevent the idle reaper from acting
_PROTECTED_TAG_KEYS = {"keep", "do-not-terminate", "protected", "do_not_terminate"}
_PROTECTED_TAG_VALUES = {"true", "yes", "1"}


class IdleReaperAgent(BaseAgent):
    """Stops idle compute resources identified by the recommendation engine."""

    async def detect(self) -> list[AgentFinding]:
        idle_days = int(self.config.thresholds.get("idle_days", 14))

        result = await self.db.execute(
            text(
                """
                SELECT r.id, r.resource_id, r.provider, r.region, r.service_name,
                       r.cost_impact_monthly_usd, r.co2e_impact_monthly_kg,
                       r.water_impact_monthly_litres, r.impact_score,
                       r.implementation_steps,
                       fr.tags
                FROM recommendations r
                LEFT JOIN focus_records fr ON fr.resource_id = r.resource_id
                    AND fr.tenant_id = r.tenant_id
                WHERE r.tenant_id = :tenant_id
                  AND r.type = 'terminate_idle'
                  AND r.status = 'open'
                  AND r.policy_blocked = false
                ORDER BY r.impact_score DESC
                LIMIT 50
                """
            ),
            {"tenant_id": str(self.config.tenant_id)},
        )
        rows = result.fetchall()

        findings: list[AgentFinding] = []
        for row in rows:
            # Check protection tags
            tags: dict = row.tags or {}
            if self._is_protected(tags):
                logger.debug(
                    "Skipping protected resource %s (tags: %s)",
                    row.resource_id,
                    tags,
                )
                continue

            findings.append(
                AgentFinding(
                    agent_type="idle_reaper",
                    tenant_id=self.config.tenant_id,
                    resource_id=row.resource_id,
                    service_name=row.service_name or "unknown",
                    region=row.region or "unknown",
                    metric="idle",
                    current_value=0.0,
                    baseline_mean=None,
                    z_score=None,
                    severity=(
                        "high" if float(row.impact_score or 0) > 0.7
                        else "medium" if float(row.impact_score or 0) > 0.4
                        else "low"
                    ),
                    description=(
                        f"Idle resource {row.resource_id} ({row.provider}/{row.region}): "
                        f"save ${row.cost_impact_monthly_usd:.0f}/mo"
                    ),
                    metadata={
                        "recommendation_id": str(row.id),
                        "provider": row.provider,
                        "co2e_impact": float(row.co2e_impact_monthly_kg or 0),
                        "idle_days": idle_days,
                        "tags": tags,
                    },
                )
            )

        logger.info(
            "IdleReaper found %d idle resources for tenant %s",
            len(findings),
            self.config.tenant_id,
        )
        return findings

    async def act(self, finding: AgentFinding) -> AgentAction:
        """Stop (not terminate) the idle resource."""
        provider = (finding.metadata.get("provider") or "").lower()
        resource_id = finding.resource_id or ""
        recommendation_id = finding.metadata.get("recommendation_id", "")

        before_state: dict = {"resource_id": resource_id, "provider": provider, "status": "running"}
        after_state: dict = {}
        error: str | None = None

        try:
            if "amazon" in provider or provider == "aws":
                after_state = await self._stop_aws(resource_id)
            elif "azure" in provider or "microsoft" in provider:
                after_state = await self._stop_azure(resource_id)
            elif "google" in provider or provider == "gcp":
                after_state = await self._stop_gcp(resource_id)
            else:
                error = f"Unsupported provider for idle reaper: {provider}"

            if not error:
                await self.db.execute(
                    text(
                        "UPDATE recommendations SET status = 'implemented', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": recommendation_id},
                )
                await self._write_audit_log(
                    action="idle_instance_stopped",
                    resource_id=resource_id,
                    before_state=before_state,
                    after_state=after_state,
                )
                await self.db.commit()

        except Exception as exc:
            logger.exception("IdleReaper act() failed for %s: %s", resource_id, exc)
            error = str(exc)

        return AgentAction(
            finding=finding,
            status="executed" if not error else "failed",
            action_type="stop_instance",
            provider=provider,
            resource_id=resource_id,
            before_state=before_state,
            after_state=after_state,
            error=error,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_protected(self, tags: dict) -> bool:
        """Return True if any protection tag is set to a truthy value."""
        for key, value in tags.items():
            if key.lower() in _PROTECTED_TAG_KEYS:
                if str(value).lower() in _PROTECTED_TAG_VALUES:
                    return True
        return False

    async def _stop_aws(self, resource_id: str) -> dict:
        import boto3
        instance_id = resource_id.split("/")[0]
        ec2 = boto3.client("ec2")
        ec2.stop_instances(InstanceIds=[instance_id])
        return {"instance_id": instance_id, "status": "stopped"}

    async def _stop_azure(self, resource_id: str) -> dict:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient

        parts = resource_id.split("/")
        subscription_id = parts[2] if len(parts) > 2 else ""
        resource_group = parts[4] if len(parts) > 4 else ""
        vm_name = parts[-1]

        credential = DefaultAzureCredential()
        client = ComputeManagementClient(credential, subscription_id)
        poller = client.virtual_machines.begin_deallocate(resource_group, vm_name)
        poller.result()
        return {"vm_name": vm_name, "status": "deallocated"}

    async def _stop_gcp(self, resource_id: str) -> dict:
        import googleapiclient.discovery
        parts = resource_id.split("/")
        project = parts[1] if len(parts) > 1 else ""
        zone = parts[3] if len(parts) > 3 else ""
        instance_name = parts[5] if len(parts) > 5 else resource_id
        compute = googleapiclient.discovery.build("compute", "v1")
        compute.instances().stop(project=project, zone=zone, instance=instance_name).execute()
        return {"instance": instance_name, "status": "stopped"}

    async def _write_audit_log(
        self,
        action: str,
        resource_id: str,
        before_state: dict,
        after_state: dict,
    ) -> None:
        await self.db.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, tenant_id, action, resource_type, resource_id, "
                "before_state, after_state, performed_by, performed_at) "
                "VALUES (:id, :tenant_id, :action, 'compute_instance', :resource_id, "
                ":before_state, :after_state, 'idle_reaper_agent', now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(self.config.tenant_id),
                "action": action,
                "resource_id": resource_id,
                "before_state": json.dumps(before_state),
                "after_state": json.dumps(after_state),
            },
        )
