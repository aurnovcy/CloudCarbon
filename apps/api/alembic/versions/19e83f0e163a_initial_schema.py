"""initial_schema

Revision ID: 19e83f0e163a
Revises:
Create Date: 2026-04-01 00:00:00.000000

Creates all CloudCarbon tables:
  tenants, users, cloud_accounts, focus_records, enriched_records,
  recommendations, agent_runs, audit_logs, policy_rules, api_keys
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "19e83f0e163a"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("plan", sa.String(50), server_default="free", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("auth_provider", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "cloud_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("credentials_ref", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cloud_accounts_tenant_id", "cloud_accounts", ["tenant_id"])

    op.create_table(
        "focus_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloud_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("service_name", sa.String(255), nullable=True),
        sa.Column("service_category", sa.String(100), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(500), nullable=True),
        sa.Column("resource_type", sa.String(255), nullable=True),
        sa.Column("usage_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column("usage_unit", sa.String(100), nullable=True),
        sa.Column("cost_usd", sa.Numeric(20, 6), nullable=False),
        sa.Column("list_cost_usd", sa.Numeric(20, 6), nullable=True),
        sa.Column("currency", sa.String(10), server_default="USD", nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_focus_records_tenant_period", "focus_records", ["tenant_id", "billing_period_start"])
    op.create_index("ix_focus_records_tenant_provider", "focus_records", ["tenant_id", "provider"])
    op.create_index("ix_focus_records_cloud_account_id", "focus_records", ["cloud_account_id"])

    op.create_table(
        "enriched_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("focus_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope1_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope2_co2e_kg_location", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope2_co2e_kg_market", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope3_cat1_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope3_cat3_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope3_cat12_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope3_total_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("scope3_confidence", sa.String(10), nullable=True),
        sa.Column("scope3_methodology_ref", sa.String(100), nullable=True),
        sa.Column("total_co2e_kg", sa.Numeric(20, 6), nullable=True),
        sa.Column("carbon_intensity_gco2_kwh", sa.Numeric(10, 4), nullable=True),
        sa.Column("estimated_kwh", sa.Numeric(20, 6), nullable=True),
        sa.Column("hardware_family", sa.String(255), nullable=True),
        sa.Column("resource_share", sa.Numeric(6, 4), nullable=True),
        sa.Column("water_litres", sa.Numeric(20, 4), nullable=True),
        sa.Column("wue_litres_per_kwh", sa.Numeric(10, 4), nullable=True),
        sa.Column("water_stress_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("water_stress_adjusted_litres", sa.Numeric(20, 4), nullable=True),
        sa.Column("water_data_source", sa.String(30), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("enrichment_version", sa.String(20), nullable=True),
        sa.ForeignKeyConstraint(["focus_record_id"], ["focus_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("focus_record_id"),
    )
    op.create_index("ix_enriched_records_tenant", "enriched_records", ["tenant_id"])

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("resource_id", sa.String(500), nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("service_name", sa.String(255), nullable=True),
        sa.Column("cost_impact_monthly_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("co2e_impact_monthly_kg", sa.Numeric(12, 4), nullable=True),
        sa.Column("water_impact_monthly_litres", sa.Numeric(12, 2), nullable=True),
        sa.Column("impact_score", sa.Numeric(6, 4), nullable=True),
        sa.Column("cost_weight_used", sa.Numeric(4, 2), nullable=True),
        sa.Column("carbon_weight_used", sa.Numeric(4, 2), nullable=True),
        sa.Column("water_weight_used", sa.Numeric(4, 2), nullable=True),
        sa.Column("complexity", sa.String(10), nullable=True),
        sa.Column("implementation_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("methodology_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendations_tenant_status", "recommendations", ["tenant_id", "status"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actions_taken", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(500), nullable=True),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_tenant_created_at", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])

    op.create_table(
        "policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rule_type", sa.String(50), nullable=True),
        sa.Column("condition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_rules_tenant_id", "policy_rules", ["tenant_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("policy_rules")
    op.drop_table("audit_logs")
    op.drop_table("agent_runs")
    op.drop_table("recommendations")
    op.drop_table("enriched_records")
    op.drop_table("focus_records")
    op.drop_table("cloud_accounts")
    op.drop_table("users")
    op.drop_table("tenants")
