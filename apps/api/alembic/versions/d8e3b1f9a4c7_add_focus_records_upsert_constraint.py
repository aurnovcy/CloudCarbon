"""add_focus_records_upsert_constraint

Revision ID: d8e3b1f9a4c7
Revises: c4f2a8e1b5d9
Create Date: 2026-04-09 00:00:01.000000

Adds a unique constraint on focus_records
(tenant_id, cloud_account_id, billing_period_start, resource_id, service_name)
required by the ON CONFLICT DO UPDATE (upsert) in the ingestion service.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d8e3b1f9a4c7"
down_revision = "c4f2a8e1b5d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_focus_records_upsert_key",
        "focus_records",
        ["tenant_id", "cloud_account_id", "billing_period_start", "resource_id", "service_name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_focus_records_upsert_key", "focus_records", type_="unique")
