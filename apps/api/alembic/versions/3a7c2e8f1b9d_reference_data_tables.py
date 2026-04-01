"""reference_data_tables

Revision ID: 3a7c2e8f1b9d
Revises: 19e83f0e163a
Create Date: 2026-04-01 00:00:01.000000

Creates four reference data tables used by the enrichment pipeline:
  region_carbon_intensity
  region_water
  hardware_carbon_coefficients
  instance_hardware_map
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "3a7c2e8f1b9d"
down_revision = "19e83f0e163a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # region_carbon_intensity
    # ------------------------------------------------------------------
    op.create_table(
        "region_carbon_intensity",
        sa.Column("region_id", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("region_name", sa.String(255), nullable=True),
        sa.Column("grid_zone", sa.String(100), nullable=True,
                  comment="Electricity Maps zone code"),
        sa.Column("carbon_intensity_gco2_kwh", sa.Numeric(10, 4), nullable=True,
                  comment="Location-based grid carbon intensity (gCO2eq/kWh)"),
        sa.Column("carbon_intensity_market_gco2_kwh", sa.Numeric(10, 4), nullable=True,
                  comment="Market-based carbon intensity including renewable PPAs"),
        sa.Column("renewable_pct", sa.Numeric(5, 2), nullable=True,
                  comment="Percentage of electricity from renewable sources"),
        sa.Column("last_updated", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("region_id"),
    )
    op.create_index("ix_rci_provider", "region_carbon_intensity", ["provider"])

    # ------------------------------------------------------------------
    # region_water
    # ------------------------------------------------------------------
    op.create_table(
        "region_water",
        sa.Column("region_id", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("wue_litres_per_kwh", sa.Numeric(10, 4), nullable=True,
                  comment="Water Usage Effectiveness (litres per kWh IT load)"),
        sa.Column("cooling_type", sa.String(50), nullable=True,
                  comment="air / evaporative / liquid_immersion / hybrid"),
        sa.Column("water_data_source", sa.String(30), nullable=True,
                  comment="provider_disclosed / cooling_type_estimate"),
        sa.Column("wri_aqueduct_score", sa.Numeric(4, 2), nullable=True,
                  comment="WRI Aqueduct water stress score (0-5)"),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("region_id"),
    )
    op.create_index("ix_rw_provider", "region_water", ["provider"])

    # ------------------------------------------------------------------
    # hardware_carbon_coefficients
    # ------------------------------------------------------------------
    op.create_table(
        "hardware_carbon_coefficients",
        sa.Column("hardware_family", sa.String(255), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=True),
        sa.Column("mfg_co2e_kg", sa.Numeric(10, 4), nullable=True,
                  comment="Embodied carbon for manufacturing one server (kgCO2eq)"),
        sa.Column("lifespan_hours", sa.Integer(), server_default="35040", nullable=False,
                  comment="Expected server lifespan in hours (default 4 years = 35040h)"),
        sa.Column("eol_co2e_kg", sa.Numeric(10, 4), nullable=True,
                  comment="End-of-life treatment carbon (kgCO2eq)"),
        sa.Column("source", sa.String(100), nullable=True,
                  comment="boavizta / estimate"),
        sa.Column("last_updated", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("hardware_family"),
    )

    # ------------------------------------------------------------------
    # instance_hardware_map
    # ------------------------------------------------------------------
    op.create_table(
        "instance_hardware_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_type", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("hardware_family", sa.String(255), nullable=True),
        sa.Column("vcpu_count", sa.Integer(), nullable=True),
        sa.Column("ram_gb", sa.Numeric(8, 2), nullable=True),
        sa.Column("total_vcpu_on_host", sa.Integer(), nullable=True,
                  comment="Total physical vCPUs on the underlying host"),
        sa.Column("total_ram_gb_on_host", sa.Numeric(8, 2), nullable=True,
                  comment="Total RAM on the underlying host in GB"),
        sa.ForeignKeyConstraint(
            ["hardware_family"],
            ["hardware_carbon_coefficients.hardware_family"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_type", "provider", name="uq_instance_provider"),
    )
    op.create_index("ix_ihm_provider", "instance_hardware_map", ["provider"])
    op.create_index("ix_ihm_hardware_family", "instance_hardware_map", ["hardware_family"])


def downgrade() -> None:
    op.drop_table("instance_hardware_map")
    op.drop_table("hardware_carbon_coefficients")
    op.drop_table("region_water")
    op.drop_table("region_carbon_intensity")
