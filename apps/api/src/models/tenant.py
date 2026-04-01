"""
Tenant model — top-level organisational unit (multi-tenancy).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin, TimestampMixin


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(50), server_default="free", nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)

    # Relationships
    users: Mapped[list["User"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="tenant", cascade="all, delete-orphan"
    )
    cloud_accounts: Mapped[list["CloudAccount"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "CloudAccount", back_populates="tenant", cascade="all, delete-orphan"
    )
    focus_records: Mapped[list["FocusRecord"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "FocusRecord", back_populates="tenant", cascade="all, delete-orphan"
    )
    enriched_records: Mapped[list["EnrichedRecord"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "EnrichedRecord", back_populates="tenant", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list["Recommendation"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Recommendation", back_populates="tenant", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AgentRun", back_populates="tenant", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AuditLog", back_populates="tenant", cascade="all, delete-orphan"
    )
    policy_rules: Mapped[list["PolicyRule"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "PolicyRule", back_populates="tenant", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "ApiKey", back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"
