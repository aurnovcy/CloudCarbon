"""
EnrichmentService — synchronous carbon and water enrichment for FOCUS records.
Uses sync variants of carbon_models estimation functions.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ENRICHMENT_VERSION = "1.0.0"

# Resolve carbon_models path relative to project root
_src_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.abspath(os.path.join(_src_dir, "..", "..", "..", ".."))
_carbon_models_path = os.path.join(_project_root, "packages", "carbon-models", "src")
if os.path.isdir(_carbon_models_path) and _carbon_models_path not in sys.path:
    sys.path.insert(0, _carbon_models_path)

try:
    from carbon_models import (
        estimate_kwh,
        calculate_scope2_sync,
        get_scope1,
        estimate_scope3_sync,
        estimate_water_sync,
    )
    _CARBON_MODELS_AVAILABLE = True
except ImportError:
    logger.warning("carbon_models package not available — enrichment will use zero-values")
    _CARBON_MODELS_AVAILABLE = False


@dataclass
class BatchEnrichmentResult:
    processed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class EnrichmentService:
    def __init__(self, enrichment_version: str = ENRICHMENT_VERSION) -> None:
        self.enrichment_version = enrichment_version

    def enrich_record(self, focus_record_id: uuid.UUID, db: Session) -> dict[str, Any]:
        result = db.execute(
            text("""
                SELECT id, tenant_id, service_category, consumed_quantity, consumed_unit,
                       resource_type, region_id, effective_cost, provider_name,
                       charge_period_start, charge_period_end
                FROM focus_records WHERE id = :fid LIMIT 1
            """),
            {"fid": str(focus_record_id)},
        )
        row = result.fetchone()
        if not row:
            raise ValueError(f"FocusRecord {focus_record_id} not found")

        (rec_id, tenant_id, service_category, consumed_quantity, consumed_unit,
         resource_type, region_id, effective_cost, provider_name,
         charge_period_start, charge_period_end) = row

        if not _CARBON_MODELS_AVAILABLE:
            kwh = 0.0
            scope1_co2e = 0.0
            scope2_location = 0.0
            scope2_market = 0.0
            scope3_cat1 = 0.0
            scope3_cat3 = 0.0
            scope3_cat12 = 0.0
            scope3_total = 0.0
            scope3_confidence = "low"
            scope3_methodology_ref = "unavailable"
            carbon_intensity = 475.0
            hardware_family = None
            resource_share = None
            water_litres = 0.0
            wue = 0.0
            water_stress = 0.0
            water_stress_adjusted = 0.0
            water_data_source = "unavailable"
        else:
            kwh = estimate_kwh(
                service_category=service_category or "",
                consumed_quantity=float(consumed_quantity) if consumed_quantity is not None else None,
                consumed_unit=consumed_unit,
                resource_type=resource_type,
                cost_usd=float(effective_cost) if effective_cost is not None else 0.0,
            )
            scope1 = get_scope1()
            scope2 = calculate_scope2_sync(kwh, region_id, db)
            scope3 = estimate_scope3_sync(
                service_category=service_category or "", resource_type=resource_type,
                provider=provider_name,
                consumed_quantity=float(consumed_quantity) if consumed_quantity is not None else None,
                consumed_unit=consumed_unit, charge_period_start=charge_period_start,
                charge_period_end=charge_period_end, estimated_kwh=kwh,
                region_id=region_id, carbon_intensity_gco2_kwh=scope2.intensity_gco2_kwh,
                db_session=db,
            )
            water = estimate_water_sync(kwh, region_id, db)

            scope1_co2e = scope1.co2e_kg
            scope2_location = scope2.location_kg
            scope2_market = scope2.market_kg
            scope3_cat1 = scope3.cat1.co2e_kg
            scope3_cat3 = scope3.cat3.co2e_kg
            scope3_cat12 = scope3.cat12.co2e_kg
            scope3_total = scope3.scope3_total_co2e_kg
            scope3_confidence = scope3.confidence
            scope3_methodology_ref = scope3.methodology_ref
            carbon_intensity = scope2.intensity_gco2_kwh
            hardware_family = scope3.cat1.hardware_family
            resource_share = scope3.cat1.resource_share
            water_litres = water.water_litres
            wue = water.wue_litres_per_kwh
            water_stress = water.water_stress_score
            water_stress_adjusted = water.water_stress_adjusted_litres
            water_data_source = water.water_data_source

        total_co2e_kg = scope1_co2e + scope2_location + scope3_total
        now = datetime.now(tz=timezone.utc)
        enriched_data = {
            "id": str(uuid.uuid4()), "focus_record_id": str(rec_id), "tenant_id": str(tenant_id),
            "scope1_co2e_kg": scope1_co2e, "scope2_co2e_kg_location": scope2_location,
            "scope2_co2e_kg_market": scope2_market, "scope3_cat1_co2e_kg": scope3_cat1,
            "scope3_cat3_co2e_kg": scope3_cat3, "scope3_cat12_co2e_kg": scope3_cat12,
            "scope3_total_co2e_kg": scope3_total, "scope3_confidence": scope3_confidence,
            "scope3_methodology_ref": scope3_methodology_ref, "total_co2e_kg": total_co2e_kg,
            "carbon_intensity_gco2_kwh": carbon_intensity, "estimated_kwh": kwh,
            "hardware_family": hardware_family, "resource_share": resource_share,
            "water_litres": water_litres, "wue_litres_per_kwh": wue,
            "water_stress_score": water_stress, "water_stress_adjusted_litres": water_stress_adjusted,
            "water_data_source": water_data_source, "enriched_at": now,
            "enrichment_version": self.enrichment_version,
        }

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
        db.execute(stmt, enriched_data)
        db.commit()
        return enriched_data

    def enrich_batch(self, tenant_id: uuid.UUID, db: Session, limit: int = 1000) -> BatchEnrichmentResult:
        start_time = datetime.now(tz=timezone.utc)
        result = BatchEnrichmentResult()

        rows = db.execute(
            text("""
                SELECT fr.id FROM focus_records fr
                LEFT JOIN enriched_records er ON er.focus_record_id = fr.id
                WHERE fr.tenant_id = :tid AND er.id IS NULL
                ORDER BY fr.created_at ASC LIMIT :lim
            """),
            {"tid": str(tenant_id), "lim": limit},
        )
        record_ids = [row[0] for row in rows.fetchall()]

        if not record_ids:
            return result

        for rec_id in record_ids:
            try:
                self.enrich_record(uuid.UUID(str(rec_id)), db)
                result.processed += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"Record {rec_id}: {exc}")
                logger.warning("Enrichment failed for record %s: %s", rec_id, exc)

        result.duration_seconds = (datetime.now(tz=timezone.utc) - start_time).total_seconds()
        return result

    def re_enrich_all(self, tenant_id: uuid.UUID, enrichment_version: str, db: Session, batch_size: int = 500) -> BatchEnrichmentResult:
        original_version = self.enrichment_version
        self.enrichment_version = enrichment_version
        try:
            start_time = datetime.now(tz=timezone.utc)
            total_result = BatchEnrichmentResult()

            rows = db.execute(
                text("SELECT id FROM focus_records WHERE tenant_id = :tid ORDER BY created_at ASC"),
                {"tid": str(tenant_id)},
            )
            all_ids = [row[0] for row in rows.fetchall()]

            for rec_id in all_ids:
                try:
                    self.enrich_record(uuid.UUID(str(rec_id)), db)
                    total_result.processed += 1
                except Exception as exc:
                    total_result.failed += 1
                    total_result.errors.append(f"Record {rec_id}: {exc}")

            total_result.duration_seconds = (datetime.now(tz=timezone.utc) - start_time).total_seconds()
            return total_result
        finally:
            self.enrichment_version = original_version

    def get_summary(self, tenant_id: uuid.UUID, db: Session) -> dict[str, Any]:
        enriched_result = db.execute(
            text("""
                SELECT COUNT(*), SUM(total_co2e_kg), SUM(scope3_total_co2e_kg),
                       SUM(water_litres), MAX(enriched_at)
                FROM enriched_records WHERE tenant_id = :tid
            """),
            {"tid": str(tenant_id)},
        )
        row = enriched_result.fetchone()
        total_enriched = int(row[0] or 0)
        total_co2e = float(row[1] or 0.0)
        total_scope3 = float(row[2] or 0.0)
        total_water = float(row[3] or 0.0)
        last_enriched_at = row[4]

        total_focus = int(db.execute(text("SELECT COUNT(*) FROM focus_records WHERE tenant_id = :tid"), {"tid": str(tenant_id)}).scalar() or 0)
        scope3_pct = (total_scope3 / total_co2e * 100.0) if total_co2e > 0 else 0.0
        coverage_pct = (total_enriched / total_focus * 100.0) if total_focus > 0 else 0.0

        return {
            "total_records_enriched": total_enriched, "total_co2e_kg": total_co2e,
            "total_scope3_co2e_kg": total_scope3, "scope3_pct_of_total": round(scope3_pct, 2),
            "total_water_litres": total_water,
            "last_enriched_at": last_enriched_at.isoformat() if last_enriched_at else None,
            "coverage_pct": round(coverage_pct, 2), "total_focus_records": total_focus,
        }
