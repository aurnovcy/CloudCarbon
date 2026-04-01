"""Add agent_configs table

Revision ID: 7f4a1c9e2d8b
Revises: 3a7c2e8f1b9d
Create Date: 2026-04-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "7f4a1c9e2d8b"
down_revision = "3a7c2e8f1b9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("thresholds", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.Column("notification_channels", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("require_approval_for_actions", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_configs_tenant_id", "agent_configs", ["tenant_id"])
    op.create_index("ix_agent_configs_agent_type", "agent_configs", ["agent_type"])
    op.create_unique_constraint(
        "uq_agent_configs_tenant_agent_type",
        "agent_configs",
        ["tenant_id", "agent_type"],
    )


def downgrade() -> None:
    op.drop_table("agent_configs")
