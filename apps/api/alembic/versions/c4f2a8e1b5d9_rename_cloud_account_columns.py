"""rename_cloud_account_columns

Revision ID: c4f2a8e1b5d9
Revises: 7f4a1c9e2d8b
Create Date: 2026-04-09 00:00:00.000000

Renames cloud_accounts columns to match the API schema:
  account_id -> account_identifier
  display_name -> name

Also adds updated_at timestamps to tables that use TimestampMixin.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c4f2a8e1b5d9"
down_revision = "7f4a1c9e2d8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename cloud_accounts columns
    op.alter_column("cloud_accounts", "account_id", new_column_name="account_identifier")
    op.alter_column("cloud_accounts", "display_name", new_column_name="name", nullable=False,
                    existing_nullable=True, existing_type=sa.String(255))

    # Add updated_at to tables that use TimestampMixin
    for table in ("users", "cloud_accounts"):
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in ("users", "cloud_accounts"):
        op.drop_column(table, "updated_at")

    op.alter_column("cloud_accounts", "account_identifier", new_column_name="account_id")
    op.alter_column("cloud_accounts", "name", new_column_name="display_name",
                    nullable=True, existing_nullable=False, existing_type=sa.String(255))
