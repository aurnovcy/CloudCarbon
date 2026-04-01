"""
PolicyRule model — tenant-defined governance rules for GreenOps enforcement.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin, TimestampMixin


class PolicyRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "policy_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # exclude_region / min_co2e_threshold / require_approval / water_stress_block
    rule_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Rule DSL stored as JSONB
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="policy_rules")  # type: ignore[name-defined]  # noqa: F821
    created_by_user: Mapped["User | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="policy_rules", foreign_keys=[created_by]
    )

    def __repr__(self) -> str:
        return f"<PolicyRule id={self.id} name={self.name!r} active={self.active}>"
