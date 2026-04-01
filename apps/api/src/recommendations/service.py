"""
CloudCarbon RecommendationService.

Orchestrates the recommendation engine for a tenant:
  1. Load enriched_records from the last 30 days
  2. Load active policy_rules for the tenant
  3. Call generate_all_recommendations from carbon-models
  4. Upsert results into the recommendations table
  5. Return count of recommendations generated
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Add carbon-models to path
sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.recommendations import (
    EnrichedRecord as CarbonEnrichedRecord,
    RecommendationInput,
    Recommendation,
    generate_all_recommendations,
)
from carbon_models.policies import PolicyRule as CarbonPolicyRule

logger = logging.getLogger(__name__)


@dataclass
class WeightConfig:
    cost_weight: float = 0.6
    carbon_weight: float = 0.3
    water_weight: float = 0.1


async def _load_enriched_records(
    tenant_id: UUID,
    db: AsyncSession,
    days: int = 30,
) -> list[CarbonEnrichedRecord]:
    """Load enriched records for the last N days from the database."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = text("""
        SELECT
            fr.resource_id,
            fr.provider_name,
            fr.region_id,
            fr.service_name,
            fr.service_category,
            fr.effective_cost,
            er.total_co2e_kg,
            er.water_litres,
            er.water_stress_adjusted_litres,
            er.estimated_kwh,
            er.carbon_intensity_gco2_kwh,
            er.water_stress_score,
            fr.tags,
            fr.charge_period_start,
            fr.charge_period_end,
            fr.resource_name
        FROM focus_records fr
        JOIN enriched_records er ON er.focus_record_id = fr.id
        WHERE fr.tenant_id = :tenant_id
          AND fr.charge_period_start >= :cutoff
        ORDER BY fr.resource_id, fr.charge_period_start
    """)
    result = await db.execute(sql, {"tenant_id": str(tenant_id), "cutoff": cutoff})
    rows = result.fetchall()

    records: list[CarbonEnrichedRecord] = []
    for row in rows:
        records.append(CarbonEnrichedRecord(
            resource_id=row[0] or "",
            provider_name=row[1] or "",
            region_id=row[2] or "",
            service_name=row[3] or "",
            service_category=row[4] or "",
            effective_cost=float(row[5] or 0),
            total_co2e_kg=float(row[6] or 0),
            water_litres=float(row[7] or 0),
            water_stress_adjusted_litres=float(row[8] or 0),
            estimated_kwh=float(row[9] or 0),
            carbon_intensity_gco2_kwh=float(row[10] or 475.0),
            water_stress_score=float(row[11]) if row[11] is not None else None,
            tags=row[12] or {},
            charge_period_start=row[13],
            charge_period_end=row[14],
            resource_name=row[15],
        ))

    logger.info("Loaded %d enriched records for tenant %s", len(records), tenant_id)
    return records


async def _load_policy_rules(
    tenant_id: UUID,
    db: AsyncSession,
) -> list[CarbonPolicyRule]:
    """Load active policy rules for the tenant."""
    sql = text("""
        SELECT id, name, rule_type, condition, description
        FROM policy_rules
        WHERE tenant_id = :tenant_id AND is_active = TRUE
    """)
    result = await db.execute(sql, {"tenant_id": str(tenant_id)})
    rows = result.fetchall()

    rules: list[CarbonPolicyRule] = []
    for row in rows:
        rules.append(CarbonPolicyRule(
            id=str(row[0]),
            name=row[1] or "",
            rule_type=row[2] or "",
            condition=row[3] or {},
            is_active=True,
            description=row[4] or "",
        ))

    logger.info("Loaded %d policy rules for tenant %s", len(rules), tenant_id)
    return rules


async def _upsert_recommendations(
    tenant_id: UUID,
    recommendations: list[Recommendation],
    db: AsyncSession,
) -> None:
    """Upsert recommendations into the recommendations table."""
    if not recommendations:
        return

    now = datetime.now(timezone.utc)

    for rec in recommendations:
        rec_id = str(uuid4())
        sql = text("""
            INSERT INTO recommendations (
                id, tenant_id, type, resource_id, provider, region,
                service_name, cost_impact_monthly_usd, co2e_impact_monthly_kg,
                water_impact_monthly_litres, impact_score, cost_weight_used,
                carbon_weight_used, water_weight_used, complexity,
                implementation_steps, methodology_notes, policy_blocked,
                policy_block_reason, requires_approval, status, created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :type, :resource_id, :provider, :region,
                :service_name, :cost_impact, :co2e_impact, :water_impact,
                :impact_score, :cost_weight, :carbon_weight, :water_weight,
                :complexity, :steps, :notes, :blocked, :block_reason,
                :requires_approval, 'open', :now, :now
            )
            ON CONFLICT (tenant_id, type, resource_id)
            DO UPDATE SET
                cost_impact_monthly_usd = EXCLUDED.cost_impact_monthly_usd,
                co2e_impact_monthly_kg  = EXCLUDED.co2e_impact_monthly_kg,
                water_impact_monthly_litres = EXCLUDED.water_impact_monthly_litres,
                impact_score            = EXCLUDED.impact_score,
                cost_weight_used        = EXCLUDED.cost_weight_used,
                carbon_weight_used      = EXCLUDED.carbon_weight_used,
                water_weight_used       = EXCLUDED.water_weight_used,
                complexity              = EXCLUDED.complexity,
                implementation_steps    = EXCLUDED.implementation_steps,
                methodology_notes       = EXCLUDED.methodology_notes,
                policy_blocked          = EXCLUDED.policy_blocked,
                policy_block_reason     = EXCLUDED.policy_block_reason,
                requires_approval       = EXCLUDED.requires_approval,
                updated_at              = EXCLUDED.updated_at
        """)
        await db.execute(sql, {
            "id": rec_id,
            "tenant_id": str(tenant_id),
            "type": rec.type,
            "resource_id": rec.resource_id,
            "provider": rec.provider,
            "region": rec.region,
            "service_name": rec.service_name,
            "cost_impact": rec.cost_impact_monthly_usd,
            "co2e_impact": rec.co2e_impact_monthly_kg,
            "water_impact": rec.water_impact_monthly_litres,
            "impact_score": rec.impact_score,
            "cost_weight": rec.cost_weight_used,
            "carbon_weight": rec.carbon_weight_used,
            "water_weight": rec.water_weight_used,
            "complexity": rec.complexity,
            "steps": rec.implementation_steps,
            "notes": rec.methodology_notes,
            "blocked": rec.policy_blocked,
            "block_reason": rec.policy_block_reason,
            "requires_approval": rec.requires_approval,
            "now": now,
        })

    await db.commit()
    logger.info("Upserted %d recommendations for tenant %s", len(recommendations), tenant_id)


async def run_recommendations(
    tenant_id: UUID,
    weights: WeightConfig,
    db: AsyncSession,
) -> int:
    """
    Full recommendation pipeline for a tenant.

    Returns:
        Count of recommendations generated and upserted.
    """
    records = await _load_enriched_records(tenant_id, db)
    if not records:
        logger.info("No enriched records found for tenant %s — skipping", tenant_id)
        return 0

    policy_rules = await _load_policy_rules(tenant_id, db)

    rec_input = RecommendationInput(
        tenant_id=tenant_id,
        records=records,
        cost_weight=weights.cost_weight,
        carbon_weight=weights.carbon_weight,
        water_weight=weights.water_weight,
    )

    recommendations = await generate_all_recommendations(
        input=rec_input,
        db_session=db,
        policy_rules=policy_rules,
    )

    await _upsert_recommendations(tenant_id, recommendations, db)
    return len(recommendations)


async def get_recommendations_summary(
    tenant_id: UUID,
    db: AsyncSession,
) -> dict:
    """
    Return aggregated recommendation statistics for the tenant.
    """
    sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'open' AND NOT policy_blocked) AS open_count,
            COALESCE(SUM(cost_impact_monthly_usd) FILTER (WHERE status = 'open' AND NOT policy_blocked), 0) AS total_cost_opp,
            COALESCE(SUM(co2e_impact_monthly_kg) FILTER (WHERE status = 'open' AND NOT policy_blocked), 0) AS total_co2e_opp,
            COALESCE(SUM(water_impact_monthly_litres) FILTER (WHERE status = 'open' AND NOT policy_blocked), 0) AS total_water_opp
        FROM recommendations
        WHERE tenant_id = :tenant_id
    """)
    row = (await db.execute(sql, {"tenant_id": str(tenant_id)})).fetchone()

    # By type breakdown
    by_type_sql = text("""
        SELECT type,
               COUNT(*) AS count,
               COALESCE(SUM(cost_impact_monthly_usd), 0) AS cost_usd,
               COALESCE(SUM(co2e_impact_monthly_kg), 0) AS co2e_kg
        FROM recommendations
        WHERE tenant_id = :tenant_id AND status = 'open' AND NOT policy_blocked
        GROUP BY type
        ORDER BY co2e_kg DESC
    """)
    by_type_rows = (await db.execute(by_type_sql, {"tenant_id": str(tenant_id)})).fetchall()

    # By provider breakdown
    by_prov_sql = text("""
        SELECT provider,
               COUNT(*) AS count,
               COALESCE(SUM(cost_impact_monthly_usd), 0) AS cost_usd,
               COALESCE(SUM(co2e_impact_monthly_kg), 0) AS co2e_kg
        FROM recommendations
        WHERE tenant_id = :tenant_id AND status = 'open' AND NOT policy_blocked
        GROUP BY provider
        ORDER BY co2e_kg DESC
    """)
    by_prov_rows = (await db.execute(by_prov_sql, {"tenant_id": str(tenant_id)})).fetchall()

    return {
        "open_count": int(row[0] or 0),
        "total_cost_opportunity_usd": float(row[1] or 0),
        "total_co2e_opportunity_kg": float(row[2] or 0),
        "total_water_opportunity_litres": float(row[3] or 0),
        "by_type": [
            {
                "type": r[0],
                "count": int(r[1]),
                "cost_usd": float(r[2]),
                "co2e_kg": float(r[3]),
            }
            for r in by_type_rows
        ],
        "by_provider": [
            {
                "provider": r[0],
                "count": int(r[1]),
                "cost_usd": float(r[2]),
                "co2e_kg": float(r[3]),
            }
            for r in by_prov_rows
        ],
    }
