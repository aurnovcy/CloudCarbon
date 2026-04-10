"""
CloudAccount model — a connected cloud provider account within a tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin, TimestampMixin


class CloudAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cloud_accounts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Enum: aws, azure, gcp, alibaba
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    account_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted vault reference — never store raw credentials
    credentials_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default="active", nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="cloud_accounts")  # type: ignore[name-defined]  # noqa: F821
    focus_records: Mapped[list["FocusRecord"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "FocusRecord", back_populates="cloud_account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<CloudAccount id={self.id} provider={self.provider!r} account_identifier={self.account_identifier!r}>"
