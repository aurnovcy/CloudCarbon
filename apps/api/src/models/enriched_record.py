"""
EnrichedRecord model — carbon, energy, and water enrichment for a FOCUS record.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.mixins import UUIDPrimaryKeyMixin


class EnrichedRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "enriched_records"
    __table_args__ = (
        Index("ix_enriched_records_tenant", "tenant_id"),
    )

    focus_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("focus_records.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # GHG Protocol scopes
    scope1_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    scope2_co2e_kg_location: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    scope2_co2e_kg_market: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    scope3_cat1_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)   # embodied carbon
    scope3_cat3_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)   # upstream energy
    scope3_cat12_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)  # end-of-life hardware
    scope3_total_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    scope3_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)  # high / medium / low
    scope3_methodology_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Totals
    total_co2e_kg: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)  # scope1 + scope2_location + scope3_total
    carbon_intensity_gco2_kwh: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    estimated_kwh: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    hardware_family: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_share: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    # Water
    water_litres: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    wue_litres_per_kwh: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    water_stress_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)  # WRI Aqueduct 0–5
    water_stress_adjusted_litres: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    water_data_source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # provider_disclosed / cooling_type_estimate

    # Metadata
    enriched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    enrichment_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    focus_record: Mapped["FocusRecord"] = relationship("FocusRecord", back_populates="enriched_record")  # type: ignore[name-defined]  # noqa: F821
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="enriched_records")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:
        return f"<EnrichedRecord id={self.id} total_co2e_kg={self.total_co2e_kg}>"
