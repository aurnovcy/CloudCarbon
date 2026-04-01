"""
CloudCarbon Agent Runner.

Uses APScheduler (AsyncIOScheduler) to load agent configurations from the
database and schedule each enabled agent according to its cron expression.

Each agent run:
  1. Creates an agent_runs row with status="running"
  2. Executes the agent (detect → act → notify)
  3. Updates the row to status="completed" or "failed"
  4. Writes summary and actions_taken JSON
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from agent_types import AgentConfig, AgentRunSummary

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://cloudcarbon:cloudcarbon@localhost:5432/cloudcarbon",
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

def _get_agent_class(agent_type: str):
    """Lazily import and return the agent class for the given type."""
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
    cls = registry.get(agent_type)
    if cls is None:
        raise ValueError(f"Unknown agent_type: {agent_type!r}")
    return cls


# ---------------------------------------------------------------------------
# Load agent configs from DB
# ---------------------------------------------------------------------------

async def load_agent_configs() -> list[AgentConfig]:
    """Load all enabled agent configs from the database."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id, tenant_id, agent_type, enabled, dry_run, "
                "cron_expression, thresholds, notification_channels, "
                "require_approval_for_actions "
                "FROM agent_configs WHERE enabled = true"
            )
        )
        rows = result.fetchall()

    configs: list[AgentConfig] = []
    for row in rows:
        configs.append(
            AgentConfig(
                id=row.id,
                tenant_id=row.tenant_id,
                agent_type=row.agent_type,
                enabled=row.enabled,
                dry_run=row.dry_run,
                cron_expression=row.cron_expression,
                thresholds=row.thresholds or {},
                notification_channels=row.notification_channels or [],
                require_approval_for_actions=row.require_approval_for_actions,
            )
        )
    log.info("Loaded agent configs", count=len(configs))
    return configs


# ---------------------------------------------------------------------------
# Run a single agent
# ---------------------------------------------------------------------------

async def run_agent(config: AgentConfig, dry_run_override: bool | None = None) -> str:
    """
    Execute a single agent run:
      1. Insert agent_runs row with status=running
      2. Run the agent
      3. Update the row with result
    Returns the run_id (UUID string).
    """
    run_id = str(uuid.uuid4())
    dry_run = dry_run_override if dry_run_override is not None else config.dry_run

    async with AsyncSessionLocal() as session:
        # Create the run record
        await session.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, tenant_id, agent_type, status, trigger, started_at, dry_run) "
                "VALUES (:id, :tenant_id, :agent_type, 'running', 'scheduled', now(), :dry_run)"
            ),
            {
                "id": run_id,
                "tenant_id": str(config.tenant_id),
                "agent_type": config.agent_type,
                "dry_run": dry_run,
            },
        )
        await session.commit()

    summary: AgentRunSummary | None = None
    status = "completed"
    error_msg: str | None = None

    try:
        agent_cls = _get_agent_class(config.agent_type)
        async with AsyncSessionLocal() as session:
            agent = agent_cls(config=config, db=session, dry_run=dry_run)
            summary = await agent.run()

        if summary.error:
            status = "failed"
            error_msg = summary.error

    except Exception as exc:
        log.exception("Agent run failed", agent_type=config.agent_type, run_id=run_id, error=str(exc))
        status = "failed"
        error_msg = str(exc)

    # Update the run record
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET "
                "status = :status, "
                "completed_at = now(), "
                "actions_taken = :actions, "
                "summary = :summary "
                "WHERE id = :id"
            ),
            {
                "id": run_id,
                "status": status,
                "actions": __import__("json").dumps(
                    summary.actions_taken_json if summary else []
                ),
                "summary": __import__("json").dumps(
                    {
                        "findings": summary.findings if summary else 0,
                        "actions_count": len(summary.actions) if summary else 0,
                        "dry_run": dry_run,
                        "error": error_msg,
                    }
                ),
            },
        )
        await session.commit()

    log.info(
        "Agent run complete",
        run_id=run_id,
        agent_type=config.agent_type,
        status=status,
        findings=summary.findings if summary else 0,
    )
    return run_id


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

async def start_scheduler() -> AsyncIOScheduler:
    """Load configs and schedule all enabled agents. Returns the running scheduler."""
    scheduler = AsyncIOScheduler()
    configs = await load_agent_configs()

    for config in configs:
        if not config.cron_expression:
            log.warning(
                "Agent has no cron_expression, skipping",
                agent_type=config.agent_type,
                tenant_id=str(config.tenant_id),
            )
            continue

        try:
            # APScheduler uses 5-field cron; strip seconds field if 6-field provided
            cron_parts = config.cron_expression.strip().split()
            if len(cron_parts) == 6:
                # Drop seconds field (first field)
                cron_expr = " ".join(cron_parts[1:])
            else:
                cron_expr = config.cron_expression

            trigger = CronTrigger.from_crontab(cron_expr)
            scheduler.add_job(
                run_agent,
                trigger=trigger,
                args=[config],
                id=f"{config.tenant_id}_{config.agent_type}",
                replace_existing=True,
                misfire_grace_time=300,
            )
            log.info(
                "Scheduled agent",
                agent_type=config.agent_type,
                tenant_id=str(config.tenant_id),
                cron=cron_expr,
            )
        except Exception as exc:
            log.error(
                "Failed to schedule agent",
                agent_type=config.agent_type,
                error=str(exc),
            )

    scheduler.start()
    log.info("Scheduler started", job_count=len(scheduler.get_jobs()))
    return scheduler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Main entry point for the agents service."""
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    log.info("CloudCarbon Agent Runner starting")
    scheduler = await start_scheduler()

    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down scheduler")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
