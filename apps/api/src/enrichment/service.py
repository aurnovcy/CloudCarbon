"""
EnrichmentService — orchestrates carbon and water enrichment for FOCUS records.

Loads FocusRecord rows from the database, calls the carbon-models estimation
library, and upserts results into the enriched_records table.

Enrichment pipeline per record:
  1. estimate_kwh()          — energy estimation
  2. calculate_scope2_async() — Scope 2 carbon (location + market)
  3. estimate_scope3_async()  — Scope 3 (Cat1 embodied, Cat3 upstream, Cat12 EOL)
  4. estimate_water_async()   — water consumption + stress adjustment
  5. Upsert into enriched_records
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ENRICHMENT_VERSION = "1.0.0"
CARBON_MODELS_PATH = "/home/ubuntu/cloudcarbon/packages/carbon-models/src"

# Ensure carbon_models is importable at runtime
import sys as _sys
if CARBON_MODELS_PATH not in _sys.path:
    _sys.path.insert(0, CARBON_MODELS_PATH)

from carbon_models import (
    estimate_kwh,
    calculate_scope2_async,
    get_scope1,
    estimate_scope3_async,
    estimate_water_async,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BatchEnrichmentResult:
    """Summary of a batch enrichment run."""
    processed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# EnrichmentService
# ---------------------------------------------------------------------------

class EnrichmentService:
    """
    Orchestrates carbon and water enrichment for FOCUS billing records.

    Usage:
        svc = EnrichmentService()
        result = await svc.enrich_record(focus_record_id, db)
        batch  = await svc.enrich_batch(tenant_id, db, limit=500)
    """

    def __init__(self, enrichment_version: str = ENRICHMENT_VERSION) -> None:
        self.enrichment_version = enrichment_version

    # ------------------------------------------------------------------
    # Single-record enrichment
    # ------------------------------------------------------------------

    async def enrich_record(
        self,
        focus_record_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Enrich a single FocusRecord and upsert into enriched_records.

        Args:
            focus_record_id: UUID of the focus_records row.
            db: Active AsyncSession.

        Returns:
            Dict representation of the upserted enriched_records row.

        Raises:
            ValueError: If the focus_record is not found.
        """
        # Load the focus_record
        result = await db.execute(
            text(
                "SELECT id, tenant_id, service_category, consumed_quantity, consumed_unit, "
                "resource_type, region_id, effective_cost, provider_name, "
                "charge_period_start, charge_period_end "
                "FROM focus_records WHERE id = :fid LIMIT 1"
            ),
            {"fid": str(focus_record_id)},
        )
        row = result.fetchone()
        if not row:
            raise ValueError(f"FocusRecord {focus_record_id} not found")

        (
            rec_id, tenant_id, service_category, consumed_quantity, consumed_unit,
            resource_type, region_id, effective_cost, provider_name,
            charge_period_start, charge_period_end,
        ) = row

        # 1. Energy estimation
        kwh = estimate_kwh(
            service_category=service_category or "",
            consumed_quantity=float(consumed_quantity) if consumed_quantity is not None else None,
            consumed_unit=consumed_unit,
            resource_type=resource_type,
            cost_usd=float(effective_cost) if effective_cost is not None else 0.0,
        )

        # 2. Scope 1 (always zero for cloud customers)
        scope1 = get_scope1()

        # 3. Scope 2
        scope2 = await calculate_scope2_async(kwh, region_id, db)

        # 4. Scope 3
        scope3 = await estimate_scope3_async(
            service_category=service_category or "",
            resource_type=resource_type,
            provider=provider_name,
            consumed_quantity=float(consumed_quantity) if consumed_quantity is not None else None,
            consumed_unit=consumed_unit,
            charge_period_start=charge_period_start,
            charge_period_end=charge_period_end,
            estimated_kwh=kwh,
            region_id=region_id,
            carbon_intensity_gco2_kwh=scope2.intensity_gco2_kwh,
            db_session=db,
        )

        # 5. Water
        water = await estimate_water_async(kwh, region_id, db)

        # 6. Total
        total_co2e_kg = scope1.co2e_kg + scope2.location_kg + scope3.scope3_total_co2e_kg

        # 7. Upsert into enriched_records
        now = datetime.now(tz=timezone.utc)
        enriched_data = {
            "id": str(uuid.uuid4()),
            "focus_record_id": str(rec_id),
            "tenant_id": str(tenant_id),
            "scope1_co2e_kg": scope1.co2e_kg,
            "scope2_co2e_kg_location": scope2.location_kg,
            "scope2_co2e_kg_market": scope2.market_kg,
            "scope3_cat1_co2e_kg": scope3.cat1.co2e_kg,
            "scope3_cat3_co2e_kg": scope3.cat3.co2e_kg,
            "scope3_cat12_co2e_kg": scope3.cat12.co2e_kg,
            "scope3_total_co2e_kg": scope3.scope3_total_co2e_kg,
            "scope3_confidence": scope3.confidence,
            "scope3_methodology_ref": scope3.methodology_ref,
            "total_co2e_kg": total_co2e_kg,
            "carbon_intensity_gco2_kwh": scope2.intensity_gco2_kwh,
            "estimated_kwh": kwh,
            "hardware_family": scope3.cat1.hardware_family,
            "resource_share": scope3.cat1.resource_share,
            "water_litres": water.water_litres,
            "wue_litres_per_kwh": water.wue_litres_per_kwh,
            "water_stress_score": water.water_stress_score,
            "water_stress_adjusted_litres": water.water_stress_adjusted_litres,
            "water_data_source": water.water_data_source,
            "enriched_at": now,
            "enrichment_version": self.enrichment_version,
        }

        # PostgreSQL upsert: update on conflict with focus_record_id unique key
        stmt = text("""
            INSERT INTO enriched_records (
                id, focus_record_id, tenant_id,
                scope1_co2e_kg, scope2_co2e_kg_location, scope2_co2e_kg_market,
                scope3_cat1_co2e_kg, scope3_cat3_co2e_kg, scope3_cat12_co2e_kg,
                scope3_total_co2e_kg, scope3_confidence, scope3_methodology_ref,
                total_co2e_kg, carbon_intensity_gco2_kwh, estimated_kwh,
                hardware_family, resource_share,
                water_litres, wue_litres_per_kwh, water_stress_score,
                water_stress_adjusted_litres, water_data_source,
                enriched_at, enrichment_version
            ) VALUES (
                :id, :focus_record_id, :tenant_id,
                :scope1_co2e_kg, :scope2_co2e_kg_location, :scope2_co2e_kg_market,
                :scope3_cat1_co2e_kg, :scope3_cat3_co2e_kg, :scope3_cat12_co2e_kg,
                :scope3_total_co2e_kg, :scope3_confidence, :scope3_methodology_ref,
                :total_co2e_kg, :carbon_intensity_gco2_kwh, :estimated_kwh,
                :hardware_family, :resource_share,
                :water_litres, :wue_litres_per_kwh, :water_stress_score,
                :water_stress_adjusted_litres, :water_data_source,
                :enriched_at, :enrichment_version
            )
            ON CONFLICT (focus_record_id) DO UPDATE SET
                scope1_co2e_kg = EXCLUDED.scope1_co2e_kg,
                scope2_co2e_kg_location = EXCLUDED.scope2_co2e_kg_location,
                scope2_co2e_kg_market = EXCLUDED.scope2_co2e_kg_market,
                scope3_cat1_co2e_kg = EXCLUDED.scope3_cat1_co2e_kg,
                scope3_cat3_co2e_kg = EXCLUDED.scope3_cat3_co2e_kg,
                scope3_cat12_co2e_kg = EXCLUDED.scope3_cat12_co2e_kg,
                scope3_total_co2e_kg = EXCLUDED.scope3_total_co2e_kg,
                scope3_confidence = EXCLUDED.scope3_confidence,
                scope3_methodology_ref = EXCLUDED.scope3_methodology_ref,
                total_co2e_kg = EXCLUDED.total_co2e_kg,
                carbon_intensity_gco2_kwh = EXCLUDED.carbon_intensity_gco2_kwh,
                estimated_kwh = EXCLUDED.estimated_kwh,
                hardware_family = EXCLUDED.hardware_family,
                resource_share = EXCLUDED.resource_share,
                water_litres = EXCLUDED.water_litres,
                wue_litres_per_kwh = EXCLUDED.wue_litres_per_kwh,
                water_stress_score = EXCLUDED.water_stress_score,
                water_stress_adjusted_litres = EXCLUDED.water_stress_adjusted_litres,
                water_data_source = EXCLUDED.water_data_source,
                enriched_at = EXCLUDED.enriched_at,
                enrichment_version = EXCLUDED.enrichment_version
        """)
        await db.execute(stmt, enriched_data)
        await db.commit()

        logger.info(
            "Enriched focus_record %s: %.4f kWh, %.4f kg CO2e total",
            focus_record_id, kwh, total_co2e_kg,
        )
        return enriched_data

    # ------------------------------------------------------------------
    # Batch enrichment
    # ------------------------------------------------------------------

    async def enrich_batch(
        self,
        tenant_id: uuid.UUID,
        db: AsyncSession,
        limit: int = 1000,
        concurrency: int = 10,
    ) -> BatchEnrichmentResult:
        """
        Enrich all unenriched FocusRecords for a tenant.

        Queries focus_records where no enriched_record exists yet and
        processes them in concurrent batches.

        Args:
            tenant_id: Tenant UUID.
            db: Active AsyncSession.
            limit: Maximum number of records to process in this run.
            concurrency: Number of concurrent enrichment tasks.

        Returns:
            BatchEnrichmentResult with processed/failed counts.
        """
        start_time = datetime.now(tz=timezone.utc)
        result = BatchEnrichmentResult()

        # Find unenriched records
        rows = await db.execute(
            text("""
                SELECT fr.id FROM focus_records fr
                LEFT JOIN enriched_records er ON er.focus_record_id = fr.id
                WHERE fr.tenant_id = :tid AND er.id IS NULL
                ORDER BY fr.created_at ASC
                LIMIT :lim
            """),
            {"tid": str(tenant_id), "lim": limit},
        )
        record_ids = [row[0] for row in rows.fetchall()]

        if not record_ids:
            logger.info("No unenriched records found for tenant %s", tenant_id)
            return result

        logger.info(
            "Starting batch enrichment: %d records for tenant %s",
            len(record_ids), tenant_id,
        )

        # Process in concurrent chunks
        semaphore = asyncio.Semaphore(concurrency)

        async def enrich_one(rec_id: Any) -> None:
            async with semaphore:
                try:
                    await self.enrich_record(uuid.UUID(str(rec_id)), db)
                    result.processed += 1
                except Exception as exc:
                    result.failed += 1
                    error_msg = f"Record {rec_id}: {exc}"
                    result.errors.append(error_msg)
                    logger.warning("Enrichment failed for record %s: %s", rec_id, exc)

        await asyncio.gather(*[enrich_one(rid) for rid in record_ids])

        duration = (datetime.now(tz=timezone.utc) - start_time).total_seconds()
        result.duration_seconds = duration

        logger.info(
            "Batch enrichment complete: %d processed, %d failed in %.1fs",
            result.processed, result.failed, duration,
        )
        return result

    # ------------------------------------------------------------------
    # Re-enrich all records (methodology update)
    # ------------------------------------------------------------------

    async def re_enrich_all(
        self,
        tenant_id: uuid.UUID,
        enrichment_version: str,
        db: AsyncSession,
        batch_size: int = 500,
    ) -> BatchEnrichmentResult:
        """
        Re-run enrichment on ALL records for a tenant.

        Used when the estimation methodology is updated. Sets the new
        enrichment_version on all updated records.

        Args:
            tenant_id: Tenant UUID.
            enrichment_version: New version string to stamp on records.
            db: Active AsyncSession.
            batch_size: Records per batch.

        Returns:
            BatchEnrichmentResult with total counts.
        """
        original_version = self.enrichment_version
        self.enrichment_version = enrichment_version

        try:
            start_time = datetime.now(tz=timezone.utc)
            total_result = BatchEnrichmentResult()

            # Get all focus_record IDs for the tenant
            rows = await db.execute(
                text("SELECT id FROM focus_records WHERE tenant_id = :tid ORDER BY created_at ASC"),
                {"tid": str(tenant_id)},
            )
            all_ids = [row[0] for row in rows.fetchall()]

            logger.info(
                "Re-enriching %d records for tenant %s (version: %s)",
                len(all_ids), tenant_id, enrichment_version,
            )

            # Process in batches
            for i in range(0, len(all_ids), batch_size):
                batch_ids = all_ids[i : i + batch_size]
                semaphore = asyncio.Semaphore(10)

                async def enrich_one(rec_id: Any) -> None:
                    async with semaphore:
                        try:
                            await self.enrich_record(uuid.UUID(str(rec_id)), db)
                            total_result.processed += 1
                        except Exception as exc:
                            total_result.failed += 1
                            total_result.errors.append(f"Record {rec_id}: {exc}")

                await asyncio.gather(*[enrich_one(rid) for rid in batch_ids])

            total_result.duration_seconds = (
                datetime.now(tz=timezone.utc) - start_time
            ).total_seconds()
            return total_result

        finally:
            self.enrichment_version = original_version

    # ------------------------------------------------------------------
    # Summary query
    # ------------------------------------------------------------------

    async def get_summary(
        self,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Return aggregated enrichment statistics for a tenant.

        Returns:
            Dict with total_records_enriched, total_co2e_kg, total_scope3_co2e_kg,
            scope3_pct_of_total, total_water_litres, last_enriched_at, coverage_pct.
        """
        enriched_result = await db.execute(
            text("""
                SELECT
                    COUNT(*)                          AS total_enriched,
                    SUM(total_co2e_kg)                AS total_co2e_kg,
                    SUM(scope3_total_co2e_kg)         AS total_scope3_co2e_kg,
                    SUM(water_litres)                 AS total_water_litres,
                    MAX(enriched_at)                  AS last_enriched_at
                FROM enriched_records
                WHERE tenant_id = :tid
            """),
            {"tid": str(tenant_id)},
        )
        row = enriched_result.fetchone()

        total_enriched = int(row[0] or 0)
        total_co2e = float(row[1] or 0.0)
        total_scope3 = float(row[2] or 0.0)
        total_water = float(row[3] or 0.0)
        last_enriched_at = row[4]

        # Total focus records for coverage calculation
        focus_result = await db.execute(
            text("SELECT COUNT(*) FROM focus_records WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        total_focus = int(focus_result.scalar() or 0)

        scope3_pct = (total_scope3 / total_co2e * 100.0) if total_co2e > 0 else 0.0
        coverage_pct = (total_enriched / total_focus * 100.0) if total_focus > 0 else 0.0

        return {
            "total_records_enriched": total_enriched,
            "total_co2e_kg": total_co2e,
            "total_scope3_co2e_kg": total_scope3,
            "scope3_pct_of_total": round(scope3_pct, 2),
            "total_water_litres": total_water,
            "last_enriched_at": last_enriched_at.isoformat() if last_enriched_at else None,
            "coverage_pct": round(coverage_pct, 2),
            "total_focus_records": total_focus,
        }
