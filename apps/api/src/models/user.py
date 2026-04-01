"""
User model — platform user within a tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin, TimestampMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Roles hierarchy: admin > engineer > analyst > viewer
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    auth_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")  # type: ignore[name-defined]  # noqa: F821
    audit_logs: Mapped[list["AuditLog"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "AuditLog", back_populates="user", foreign_keys="AuditLog.user_id"
    )
    policy_rules: Mapped[list["PolicyRule"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "PolicyRule", back_populates="created_by_user", foreign_keys="PolicyRule.created_by"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "ApiKey", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
