"""
Recommendation model — actionable GreenOps optimisation suggestion.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin


class Recommendation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # rightsize / terminate_idle / region_migrate / schedule_off_hours / reserved_instance / workload_shift_green
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # open / implemented / dismissed / snoozed
    status: Mapped[str] = mapped_column(String(20), server_default="open", nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Impact metrics
    cost_impact_monthly_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    co2e_impact_monthly_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    water_impact_monthly_litres: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Scoring
    impact_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    cost_weight_used: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    carbon_weight_used: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    water_weight_used: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)

    # Implementation
    complexity: Mapped[str | None] = mapped_column(String(10), nullable=True)  # low / medium / high
    implementation_steps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    methodology_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="recommendations")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:
        return f"<Recommendation id={self.id} type={self.type!r} status={self.status!r}>"
