"""
AgentRun model — execution record for an autonomous agent.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # anomaly_detector / carbon_spike_monitor / rightsizing_agent / idle_reaper / green_scheduler
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # running / completed / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # scheduled / manual / alert
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actions_taken: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="agent_runs")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id} agent_type={self.agent_type!r} status={self.status!r}>"
