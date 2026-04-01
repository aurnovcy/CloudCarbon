"""
CloudCarbon Agent Runner entry point.

Agents implemented in Session 3+:
- anomaly_detector
- carbon_spike_monitor
- rightsizing_agent
- idle_reaper
- green_scheduler
"""
from __future__ import annotations

import asyncio
import structlog

logger = structlog.get_logger(__name__)


async def main() -> None:
    logger.info("CloudCarbon Agent Runner starting — Session 2+ will populate agents")
    # TODO: initialise APScheduler, register agents, start event loop
    await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
