"""
Agent Control API Router (synchronous).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from dependencies.auth import get_current_user, require_role
from models.user import User
from schemas.agents import (
    AgentConfigPatchRequest,
    AgentConfigResponse,
    AgentRunListResponse,
    AgentRunResponse,
    ApproveActionRequest,
    ApproveActionResponse,
    ManualRunRequest,
    ManualRunResponse,
)

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("", response_model=list[AgentConfigResponse])
def list_agents(
    current_user: User = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
) -> list[AgentConfigResponse]:
    result = db.execute(
        text("""
            SELECT ac.id, ac.tenant_id, ac.agent_type, ac.enabled, ac.dry_run,
                   ac.cron_expression, ac.thresholds, ac.notification_channels,
                   ac.require_approval_for_actions, ac.created_at, ac.updated_at,
                   ar.summary AS last_run_summary
            FROM agent_configs ac
            LEFT JOIN LATERAL (
                SELECT summary FROM agent_runs
                WHERE tenant_id = ac.tenant_id AND agent_type = ac.agent_type
                ORDER BY started_at DESC LIMIT 1
            ) ar ON true
            WHERE ac.tenant_id = :tenant_id
            ORDER BY ac.agent_type
        """),
        {"tenant_id": str(current_user.tenant_id)},
    )
    rows = result.fetchall()

    configs = []
    for row in rows:
        last_run = None
        if row.last_run_summary:
            try:
                last_run = json.loads(row.last_run_summary) if isinstance(row.last_run_summary, str) else row.last_run_summary
            except Exception:
                last_run = None

        configs.append(AgentConfigResponse(
            id=row.id, tenant_id=row.tenant_id, agent_type=row.agent_type,
            enabled=row.enabled, dry_run=row.dry_run, cron_expression=row.cron_expression,
            thresholds=row.thresholds or {}, notification_channels=row.notification_channels or [],
            require_approval_for_actions=row.require_approval_for_actions,
            created_at=row.created_at, updated_at=row.updated_at, last_run_summary=last_run,
        ))
    return configs


@router.patch("/{agent_type}", response_model=AgentConfigResponse)
def update_agent_config(
    agent_type: str,
    body: AgentConfigPatchRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> AgentConfigResponse:
    result = db.execute(
        text("SELECT id FROM agent_configs WHERE tenant_id = :tenant_id AND agent_type = :agent_type"),
        {"tenant_id": str(current_user.tenant_id), "agent_type": agent_type},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent config not found: {agent_type}")

    config_id = row.id
    updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.dry_run is not None:
        updates["dry_run"] = body.dry_run
    if body.cron_expression is not None:
        updates["cron_expression"] = body.cron_expression
    if body.thresholds is not None:
        updates["thresholds"] = json.dumps(body.thresholds)
    if body.notification_channels is not None:
        updates["notification_channels"] = json.dumps(body.notification_channels)
    if body.require_approval_for_actions is not None:
        updates["require_approval_for_actions"] = body.require_approval_for_actions

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    db.execute(text(f"UPDATE agent_configs SET {set_clause} WHERE id = :id"), {**updates, "id": str(config_id)})
    db.execute(text("""
        INSERT INTO audit_logs (id, tenant_id, action, resource_type, resource_id, before_state, after_state, performed_by, performed_at)
        VALUES (:id, :tenant_id, 'update_agent_config', 'agent_config', :resource_id, :before_state, :after_state, :user_id, now())
    """), {
        "id": str(uuid.uuid4()), "tenant_id": str(current_user.tenant_id),
        "resource_id": str(config_id), "before_state": "{}",
        "after_state": json.dumps(body.model_dump(exclude_none=True)), "user_id": str(current_user.id),
    })
    db.commit()

    result = db.execute(
        text("SELECT id, tenant_id, agent_type, enabled, dry_run, cron_expression, thresholds, notification_channels, require_approval_for_actions, created_at, updated_at FROM agent_configs WHERE id = :id"),
        {"id": str(config_id)},
    )
    row = result.fetchone()
    return AgentConfigResponse(
        id=row.id, tenant_id=row.tenant_id, agent_type=row.agent_type,
        enabled=row.enabled, dry_run=row.dry_run, cron_expression=row.cron_expression,
        thresholds=row.thresholds or {}, notification_channels=row.notification_channels or [],
        require_approval_for_actions=row.require_approval_for_actions,
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.post("/{agent_type}/run", response_model=ManualRunResponse, status_code=202)
def trigger_agent_run(
    agent_type: str,
    body: ManualRunRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role("engineer")),
    db: Session = Depends(get_db),
) -> ManualRunResponse:
    valid_types = {"anomaly_detector", "carbon_spike_monitor", "rightsizing_agent", "idle_reaper", "green_scheduler"}
    if agent_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"Unknown agent_type. Valid types: {sorted(valid_types)}")

    result = db.execute(
        text("SELECT id, tenant_id, agent_type, enabled, dry_run, cron_expression, thresholds, notification_channels, require_approval_for_actions FROM agent_configs WHERE tenant_id = :tenant_id AND agent_type = :agent_type"),
        {"tenant_id": str(current_user.tenant_id), "agent_type": agent_type},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent config not found for type: {agent_type}")

    effective_dry_run = body.dry_run
    if not body.dry_run and current_user.role != "admin":
        effective_dry_run = True

    job_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    db.execute(text("""
        INSERT INTO agent_runs (id, tenant_id, agent_type, status, trigger, started_at, dry_run)
        VALUES (:id, :tenant_id, :agent_type, 'running', 'manual', now(), :dry_run)
    """), {"id": str(job_id), "tenant_id": str(current_user.tenant_id), "agent_type": agent_type, "dry_run": effective_dry_run})
    db.commit()

    try:
        from agent_types import AgentConfig
        config = AgentConfig(
            id=row.id, tenant_id=row.tenant_id, agent_type=row.agent_type,
            enabled=row.enabled, dry_run=effective_dry_run, cron_expression=row.cron_expression,
            thresholds=row.thresholds or {}, notification_channels=row.notification_channels or [],
            require_approval_for_actions=row.require_approval_for_actions,
        )
    except ImportError:
        config = None

    def _run_in_background():
        import asyncio
        _db = SessionLocal()
        try:
            from agents.anomaly_detector import AnomalyDetectorAgent
            from agents.carbon_spike_monitor import CarbonSpikeMonitorAgent
            from agents.green_scheduler import GreenSchedulerAgent
            from agents.idle_reaper import IdleReaperAgent
            from agents.rightsizing_agent import RightsizingAgent

            registry = {
                "anomaly_detector": AnomalyDetectorAgent,
                "carbon_spike_monitor": CarbonSpikeMonitorAgent,
                "rightsizing_agent": RightsizingAgent,
                "idle_reaper": IdleReaperAgent,
                "green_scheduler": GreenSchedulerAgent,
            }
            agent_cls = registry[agent_type]
            agent = agent_cls(config=config, db=_db, dry_run=effective_dry_run)
            summary = asyncio.run(agent.run())

            _db.execute(text("""
                UPDATE agent_runs SET status = 'completed', completed_at = now(),
                actions_taken = :actions, summary = :summary WHERE id = :id
            """), {"id": str(job_id), "actions": json.dumps(summary.actions_taken_json),
                  "summary": json.dumps({"findings": summary.findings, "error": summary.error})})
            _db.commit()
        except Exception as exc:
            _db.execute(text("UPDATE agent_runs SET status = 'failed', completed_at = now(), summary = :summary WHERE id = :id"),
                       {"id": str(job_id), "summary": json.dumps({"error": str(exc)})})
            _db.commit()
        finally:
            _db.close()

    background_tasks.add_task(_run_in_background)
    return ManualRunResponse(job_id=job_id, agent_type=agent_type, dry_run=effective_dry_run, status="started", started_at=started_at)


@router.get("/runs", response_model=AgentRunListResponse)
def list_agent_runs(
    agent_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
) -> AgentRunListResponse:
    filters = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": str(current_user.tenant_id), "limit": limit, "offset": offset}

    if agent_type:
        filters.append("agent_type = :agent_type")
        params["agent_type"] = agent_type
    if status:
        filters.append("status = :status")
        params["status"] = status

    where = " AND ".join(filters)
    total = db.execute(text(f"SELECT COUNT(*) FROM agent_runs WHERE {where}"), params).scalar() or 0
    result = db.execute(text(f"SELECT id, tenant_id, agent_type, status, trigger, started_at, completed_at, dry_run, actions_taken, summary FROM agent_runs WHERE {where} ORDER BY started_at DESC LIMIT :limit OFFSET :offset"), params)
    rows = result.fetchall()

    items = []
    for row in rows:
        actions = json.loads(row.actions_taken) if row.actions_taken and isinstance(row.actions_taken, str) else row.actions_taken
        summary = json.loads(row.summary) if row.summary and isinstance(row.summary, str) else row.summary
        items.append(AgentRunResponse(
            id=row.id, tenant_id=row.tenant_id, agent_type=row.agent_type,
            status=row.status, trigger=row.trigger, started_at=row.started_at,
            completed_at=row.completed_at, dry_run=row.dry_run, actions_taken=actions, summary=summary,
        ))
    return AgentRunListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(
    run_id: uuid.UUID,
    current_user: User = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
) -> AgentRunResponse:
    result = db.execute(
        text("SELECT id, tenant_id, agent_type, status, trigger, started_at, completed_at, dry_run, actions_taken, summary FROM agent_runs WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(run_id), "tenant_id": str(current_user.tenant_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent run not found")

    actions = json.loads(row.actions_taken) if row.actions_taken and isinstance(row.actions_taken, str) else row.actions_taken
    summary = json.loads(row.summary) if row.summary and isinstance(row.summary, str) else row.summary
    return AgentRunResponse(
        id=row.id, tenant_id=row.tenant_id, agent_type=row.agent_type, status=row.status,
        trigger=row.trigger, started_at=row.started_at, completed_at=row.completed_at,
        dry_run=row.dry_run, actions_taken=actions, summary=summary,
    )


@router.post("/runs/{run_id}/approve", response_model=ApproveActionResponse)
def approve_agent_action(
    run_id: uuid.UUID,
    body: ApproveActionRequest,
    current_user: User = Depends(require_role("engineer")),
    db: Session = Depends(get_db),
) -> ApproveActionResponse:
    result = db.execute(
        text("SELECT id, tenant_id, agent_type, status, actions_taken, dry_run FROM agent_runs WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(run_id), "tenant_id": str(current_user.tenant_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent run not found")

    actions = json.loads(row.actions_taken) if row.actions_taken and isinstance(row.actions_taken, str) else (row.actions_taken or [])

    if body.action_index >= len(actions):
        raise HTTPException(status_code=422, detail=f"Action index {body.action_index} out of range")

    action = actions[body.action_index]
    if action.get("status") != "pending_approval":
        raise HTTPException(status_code=422, detail=f"Action {body.action_index} is not in pending_approval status")

    approved_at = datetime.now(timezone.utc)
    actions[body.action_index]["status"] = "approved"
    actions[body.action_index]["approved_by"] = str(current_user.id)
    actions[body.action_index]["approved_at"] = approved_at.isoformat()
    if body.notes:
        actions[body.action_index]["approval_notes"] = body.notes

    db.execute(text("UPDATE agent_runs SET actions_taken = :actions WHERE id = :id"),
               {"actions": json.dumps(actions), "id": str(run_id)})
    db.execute(text("""
        INSERT INTO audit_logs (id, tenant_id, action, resource_type, resource_id, before_state, after_state, performed_by, performed_at)
        VALUES (:id, :tenant_id, 'approve_agent_action', 'agent_run', :resource_id, :before_state, :after_state, :user_id, now())
    """), {
        "id": str(uuid.uuid4()), "tenant_id": str(current_user.tenant_id), "resource_id": str(run_id),
        "before_state": json.dumps({"status": "pending_approval"}),
        "after_state": json.dumps({"status": "approved", "action_index": body.action_index, "notes": body.notes}),
        "user_id": str(current_user.id),
    })
    db.commit()

    return ApproveActionResponse(run_id=run_id, action_index=body.action_index, status="approved", approved_at=approved_at, notes=body.notes)
