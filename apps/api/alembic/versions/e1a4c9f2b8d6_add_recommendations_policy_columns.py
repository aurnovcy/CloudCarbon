"""add_recommendations_policy_columns

Revision ID: e1a4c9f2b8d6
Revises: d8e3b1f9a4c7
Create Date: 2026-04-09 00:00:02.000000

Adds policy-related and snooze columns to the recommendations table
that are referenced by the recommendations router but missing from
the initial schema migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e1a4c9f2b8d6"
down_revision = "d8e3b1f9a4c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("policy_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("recommendations", sa.Column("policy_block_reason", sa.Text(), nullable=True))
    op.add_column("recommendations", sa.Column("requires_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("recommendations", sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations", "snooze_until")
    op.drop_column("recommendations", "requires_approval")
    op.drop_column("recommendations", "policy_block_reason")
    op.drop_column("recommendations", "policy_blocked")
