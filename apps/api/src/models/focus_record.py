"""
FocusRecord model — normalised FOCUS 1.0 billing record.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin


class FocusRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "focus_records"
    __table_args__ = (
        Index("ix_focus_records_tenant_period", "tenant_id", "billing_period_start"),
        Index("ix_focus_records_tenant_provider", "tenant_id", "provider"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    cloud_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    billing_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    usage_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    usage_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    list_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), server_default="USD", nullable=False)
    tags: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="focus_records")  # type: ignore[name-defined]  # noqa: F821
    cloud_account: Mapped["CloudAccount"] = relationship("CloudAccount", back_populates="focus_records")  # type: ignore[name-defined]  # noqa: F821
    enriched_record: Mapped["EnrichedRecord | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "EnrichedRecord", back_populates="focus_record", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<FocusRecord id={self.id} provider={self.provider!r} cost_usd={self.cost_usd}>"
