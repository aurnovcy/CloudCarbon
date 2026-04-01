"""
RightsizingAgent — acts on open "rightsize" recommendations.

detect() logic:
  - Load open recommendations of type "rightsize" from recommendations table
  - Filter by thresholds.min_cost_impact_usd (default $50/month)
  - Filter by thresholds.min_co2e_impact_kg (default 10 kg/month)

act() logic (only if dry_run=False and require_approval_for_actions=False):
  - AWS:   EC2 stop → modify_instance_attribute → start
  - Azure: VirtualMachines.begin_deallocate → resize
  - GCP:   instances().stop → setMachineType
  - Log before/after state to audit_logs
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


class RightsizingAgent(BaseAgent):
    """Acts on open rightsize recommendations from the recommendations table."""

    async def detect(self) -> list[AgentFinding]:
        min_cost = float(self.config.thresholds.get("min_cost_impact_usd", 50.0))
        min_co2e = float(self.config.thresholds.get("min_co2e_impact_kg", 10.0))

        result = await self.db.execute(
            text(
                """
                SELECT id, resource_id, provider, region, service_name,
                       cost_impact_monthly_usd, co2e_impact_monthly_kg,
                       water_impact_monthly_litres, impact_score, complexity,
                       implementation_steps, methodology_notes
                FROM recommendations
                WHERE tenant_id = :tenant_id
                  AND type = 'rightsize'
                  AND status = 'open'
                  AND policy_blocked = false
                  AND cost_impact_monthly_usd >= :min_cost
                  AND co2e_impact_monthly_kg >= :min_co2e
                ORDER BY impact_score DESC
                LIMIT 50
                """
            ),
            {
                "tenant_id": str(self.config.tenant_id),
                "min_cost": min_cost,
                "min_co2e": min_co2e,
            },
        )
        rows = result.fetchall()

        findings: list[AgentFinding] = []
        for row in rows:
            findings.append(
                AgentFinding(
                    agent_type="rightsizing_agent",
                    tenant_id=self.config.tenant_id,
                    resource_id=row.resource_id,
                    service_name=row.service_name or "unknown",
                    region=row.region or "unknown",
                    metric="cost",
                    current_value=float(row.cost_impact_monthly_usd or 0),
                    baseline_mean=None,
                    z_score=None,
                    severity=(
                        "high" if float(row.impact_score or 0) > 0.7
                        else "medium" if float(row.impact_score or 0) > 0.4
                        else "low"
                    ),
                    description=(
                        f"Rightsize {row.resource_id} ({row.provider}/{row.region}): "
                        f"save ${row.cost_impact_monthly_usd:.0f}/mo, "
                        f"{row.co2e_impact_monthly_kg:.1f} kg CO2e/mo"
                    ),
                    metadata={
                        "recommendation_id": str(row.id),
                        "provider": row.provider,
                        "co2e_impact": float(row.co2e_impact_monthly_kg or 0),
                        "water_impact": float(row.water_impact_monthly_litres or 0),
                        "complexity": row.complexity,
                        "implementation_steps": row.implementation_steps or [],
                    },
                )
            )

        logger.info(
            "RightsizingAgent found %d recommendations for tenant %s",
            len(findings),
            self.config.tenant_id,
        )
        return findings

    async def act(self, finding: AgentFinding) -> AgentAction:
        """Execute the rightsize action for the given finding."""
        provider = (finding.metadata.get("provider") or "").lower()
        resource_id = finding.resource_id or ""
        recommendation_id = finding.metadata.get("recommendation_id", "")

        before_state: dict = {"resource_id": resource_id, "provider": provider}
        after_state: dict = {}
        error: str | None = None

        try:
            if provider == "amazon web services" or provider == "aws":
                after_state = await self._act_aws(resource_id, finding)
            elif provider == "microsoft azure" or provider == "azure":
                after_state = await self._act_azure(resource_id, finding)
            elif provider == "google cloud platform" or provider == "gcp":
                after_state = await self._act_gcp(resource_id, finding)
            else:
                error = f"Unsupported provider for rightsizing: {provider}"

            if not error:
                # Update recommendation status
                await self.db.execute(
                    text(
                        "UPDATE recommendations SET status = 'implemented', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": recommendation_id},
                )
                # Write audit log
                await self._write_audit_log(
                    action="rightsize_executed",
                    resource_id=resource_id,
                    before_state=before_state,
                    after_state=after_state,
                )
                await self.db.commit()

        except Exception as exc:
            logger.exception("Rightsize act() failed for %s: %s", resource_id, exc)
            error = str(exc)

        return AgentAction(
            finding=finding,
            status="executed" if not error else "failed",
            action_type="rightsize",
            provider=provider,
            resource_id=resource_id,
            before_state=before_state,
            after_state=after_state,
            error=error,
        )

    # ------------------------------------------------------------------
    # Provider-specific implementations
    # ------------------------------------------------------------------

    async def _act_aws(self, resource_id: str, finding: AgentFinding) -> dict:
        """
        AWS EC2 rightsize sequence:
          1. describe_instances (capture before state)
          2. stop_instances (wait for stopped)
          3. modify_instance_attribute (change instance type)
          4. start_instances
        """
        import boto3

        # Extract instance ID and target type from resource_id
        # resource_id format: "i-0abc123def456/t3.small" or just "i-0abc123def456"
        parts = resource_id.split("/")
        instance_id = parts[0]
        target_type = parts[1] if len(parts) > 1 else "t3.small"

        ec2 = boto3.client("ec2")

        # 1. Describe (capture before state)
        desc = ec2.describe_instances(InstanceIds=[instance_id])
        instance = desc["Reservations"][0]["Instances"][0]
        current_type = instance["InstanceType"]
        before = {"instance_id": instance_id, "instance_type": current_type}

        # 2. Stop
        ec2.stop_instances(InstanceIds=[instance_id])
        waiter = ec2.get_waiter("instance_stopped")
        waiter.wait(InstanceIds=[instance_id])

        # 3. Modify
        ec2.modify_instance_attribute(
            InstanceId=instance_id,
            InstanceType={"Value": target_type},
        )

        # 4. Start
        ec2.start_instances(InstanceIds=[instance_id])

        return {**before, "new_instance_type": target_type, "status": "resized"}

    async def _act_azure(self, resource_id: str, finding: AgentFinding) -> dict:
        """
        Azure VM rightsize sequence:
          1. begin_deallocate (wait for deallocated)
          2. resize (update VM size)
        """
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient

        # resource_id format: "/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{name}"
        parts = resource_id.split("/")
        subscription_id = parts[2] if len(parts) > 2 else ""
        resource_group = parts[4] if len(parts) > 4 else ""
        vm_name = parts[-1]
        target_size = finding.metadata.get("target_size", "Standard_B2s")

        credential = DefaultAzureCredential()
        client = ComputeManagementClient(credential, subscription_id)

        # Get current size
        vm = client.virtual_machines.get(resource_group, vm_name)
        current_size = vm.hardware_profile.vm_size

        # Deallocate
        poller = client.virtual_machines.begin_deallocate(resource_group, vm_name)
        poller.result()

        # Resize
        vm.hardware_profile.vm_size = target_size
        poller = client.virtual_machines.begin_create_or_update(resource_group, vm_name, vm)
        poller.result()

        return {
            "vm_name": vm_name,
            "old_size": current_size,
            "new_size": target_size,
            "status": "resized",
        }

    async def _act_gcp(self, resource_id: str, finding: AgentFinding) -> dict:
        """
        GCP instance rightsize sequence:
          1. instances().stop
          2. instances().setMachineType
        """
        import googleapiclient.discovery

        # resource_id format: "projects/{project}/zones/{zone}/instances/{name}"
        parts = resource_id.split("/")
        project = parts[1] if len(parts) > 1 else ""
        zone = parts[3] if len(parts) > 3 else ""
        instance_name = parts[5] if len(parts) > 5 else resource_id
        target_machine_type = finding.metadata.get("target_machine_type", "n1-standard-1")

        compute = googleapiclient.discovery.build("compute", "v1")

        # Get current machine type
        inst = compute.instances().get(project=project, zone=zone, instance=instance_name).execute()
        current_type = inst["machineType"].split("/")[-1]

        # Stop
        op = compute.instances().stop(project=project, zone=zone, instance=instance_name).execute()
        # (In production, poll the operation until done)

        # Set machine type
        machine_type_url = f"zones/{zone}/machineTypes/{target_machine_type}"
        compute.instances().setMachineType(
            project=project,
            zone=zone,
            instance=instance_name,
            body={"machineType": machine_type_url},
        ).execute()

        return {
            "instance": instance_name,
            "old_machine_type": current_type,
            "new_machine_type": target_machine_type,
            "status": "resized",
        }

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
                ":before_state, :after_state, 'rightsizing_agent', now())"
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
