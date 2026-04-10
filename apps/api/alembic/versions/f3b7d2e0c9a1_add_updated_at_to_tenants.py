"""add_updated_at_to_tenants

Revision ID: f3b7d2e0c9a1
Revises: e1a4c9f2b8d6
Create Date: 2026-04-09 00:00:03.000000

Adds the missing updated_at column to the tenants table so that the
SQLAlchemy ORM (which inherits TimestampMixin) can query it correctly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "f3b7d2e0c9a1"
down_revision = "e1a4c9f2b8d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "updated_at")
