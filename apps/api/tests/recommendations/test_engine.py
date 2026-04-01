"""
Unit tests for the recommendation engine.

Tests cover:
  - generate_rightsize_recommendations
  - generate_terminate_idle_recommendations
  - generate_schedule_off_hours_recommendations
  - generate_reserved_instance_recommendations
  - _normalize_and_score
  - Impact score ordering
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.recommendations import (
    EnrichedRecord,
    RecommendationInput,
    Recommendation,
    generate_rightsize_recommendations,
    generate_terminate_idle_recommendations,
    generate_schedule_off_hours_recommendations,
    generate_reserved_instance_recommendations,
    _normalize_and_score,
    _is_non_prod,
    _is_keep_tagged,
    _is_batch_workload,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_record(
    resource_id: str = "res-001",
    provider: str = "AWS",
    region: str = "us-east-1",
    service_name: str = "Amazon EC2",
    service_category: str = "Compute",
    cost: float = 100.0,
    co2e: float = 10.0,
    water: float = 5.0,
    kwh: float = 0.001,  # very low = idle
    tags: dict | None = None,
    resource_name: str | None = None,
) -> EnrichedRecord:
    return EnrichedRecord(
        resource_id=resource_id,
        provider_name=provider,
        region_id=region,
        service_name=service_name,
        service_category=service_category,
        effective_cost=cost,
        total_co2e_kg=co2e,
        water_litres=water,
        water_stress_adjusted_litres=water * 1.2,
        estimated_kwh=kwh,
        carbon_intensity_gco2_kwh=475.0,
        water_stress_score=2.0,
        tags=tags or {},
        resource_name=resource_name,
    )


def _make_high_kwh_record(**kwargs) -> EnrichedRecord:
    """Record with high kWh (not idle, not low utilisation)."""
    return _make_record(kwh=10.0, **kwargs)


# ---------------------------------------------------------------------------
# Test: _is_non_prod
# ---------------------------------------------------------------------------

class TestIsNonProd:
    def test_env_tag_dev(self):
        recs = [_make_record(tags={"env": "dev"})]
        assert _is_non_prod(recs) is True

    def test_env_tag_staging(self):
        recs = [_make_record(tags={"env": "staging"})]
        assert _is_non_prod(recs) is True

    def test_env_tag_production(self):
        recs = [_make_record(tags={"env": "production"})]
        assert _is_non_prod(recs) is False

    def test_resource_name_contains_dev(self):
        recs = [_make_record(resource_name="my-dev-server")]
        assert _is_non_prod(recs) is True

    def test_resource_name_contains_test(self):
        recs = [_make_record(resource_name="test-worker-01")]
        assert _is_non_prod(recs) is True

    def test_production_resource_name(self):
        recs = [_make_record(resource_name="prod-api-server")]
        assert _is_non_prod(recs) is False

    def test_empty_tags_and_name(self):
        recs = [_make_record()]
        assert _is_non_prod(recs) is False


# ---------------------------------------------------------------------------
# Test: _is_keep_tagged
# ---------------------------------------------------------------------------

class TestIsKeepTagged:
    def test_keep_true(self):
        recs = [_make_record(tags={"keep": "true"})]
        assert _is_keep_tagged(recs) is True

    def test_do_not_terminate_true(self):
        recs = [_make_record(tags={"do-not-terminate": "yes"})]
        assert _is_keep_tagged(recs) is True

    def test_no_keep_tag(self):
        recs = [_make_record(tags={"env": "dev"})]
        assert _is_keep_tagged(recs) is False

    def test_keep_false(self):
        recs = [_make_record(tags={"keep": "false"})]
        assert _is_keep_tagged(recs) is False


# ---------------------------------------------------------------------------
# Test: _is_batch_workload
# ---------------------------------------------------------------------------

class TestIsBatchWorkload:
    def test_batch_tag_true(self):
        recs = [_make_record(tags={"batch": "true"})]
        assert _is_batch_workload(recs) is True

    def test_emr_service_name(self):
        recs = [_make_record(service_name="Amazon EMR")]
        assert _is_batch_workload(recs) is True

    def test_glue_service_name(self):
        recs = [_make_record(service_name="AWS Glue")]
        assert _is_batch_workload(recs) is True

    def test_regular_compute(self):
        recs = [_make_record(service_name="Amazon EC2")]
        assert _is_batch_workload(recs) is False


# ---------------------------------------------------------------------------
# Test: generate_rightsize_recommendations
# ---------------------------------------------------------------------------

class TestRightsizeRecommendations:
    def test_low_utilisation_flagged(self):
        # kwh=0.001 << max_possible_kwh → utilisation < 25%
        recs = [_make_record(resource_id="res-001", kwh=0.001) for _ in range(5)]
        results = generate_rightsize_recommendations(recs)
        assert len(results) == 1
        assert results[0].type == "rightsize"
        assert results[0].resource_id == "res-001"

    def test_cost_impact_is_50_pct(self):
        recs = [_make_record(resource_id="res-001", cost=200.0, kwh=0.001) for _ in range(3)]
        results = generate_rightsize_recommendations(recs)
        assert len(results) == 1
        assert abs(results[0].cost_impact_monthly_usd - 300.0) < 0.01  # 3×200×0.5

    def test_co2e_impact_is_50_pct(self):
        recs = [_make_record(resource_id="res-001", co2e=20.0, kwh=0.001) for _ in range(2)]
        results = generate_rightsize_recommendations(recs)
        assert abs(results[0].co2e_impact_monthly_kg - 20.0) < 0.01  # 2×20×0.5

    def test_complexity_is_low(self):
        recs = [_make_record(kwh=0.001)]
        results = generate_rightsize_recommendations(recs)
        assert results[0].complexity == "low"

    def test_implementation_steps_not_empty(self):
        recs = [_make_record(kwh=0.001)]
        results = generate_rightsize_recommendations(recs)
        assert len(results[0].implementation_steps) >= 3

    def test_high_utilisation_not_flagged(self):
        # kwh=10.0 → high utilisation
        recs = [_make_high_kwh_record(resource_id="res-002")]
        results = generate_rightsize_recommendations(recs)
        assert len(results) == 0

    def test_multiple_resources(self):
        low_recs = [_make_record(resource_id="low-res", kwh=0.001)]
        high_recs = [_make_high_kwh_record(resource_id="high-res")]
        results = generate_rightsize_recommendations(low_recs + high_recs)
        assert len(results) == 1
        assert results[0].resource_id == "low-res"


# ---------------------------------------------------------------------------
# Test: generate_terminate_idle_recommendations
# ---------------------------------------------------------------------------

class TestTerminateIdleRecommendations:
    def _idle_records(self, resource_id: str, count: int = 15) -> list[EnrichedRecord]:
        return [_make_record(resource_id=resource_id, kwh=0.001) for _ in range(count)]

    def test_idle_resource_flagged(self):
        recs = self._idle_records("idle-res", count=15)
        results = generate_terminate_idle_recommendations(recs)
        assert len(results) == 1
        assert results[0].type == "terminate_idle"

    def test_fewer_than_14_days_not_flagged(self):
        recs = self._idle_records("idle-res", count=10)
        results = generate_terminate_idle_recommendations(recs)
        assert len(results) == 0

    def test_keep_tagged_excluded(self):
        recs = [_make_record(resource_id="keep-res", kwh=0.001, tags={"keep": "true"})
                for _ in range(20)]
        results = generate_terminate_idle_recommendations(recs)
        assert len(results) == 0

    def test_do_not_terminate_excluded(self):
        recs = [_make_record(resource_id="dnt-res", kwh=0.001, tags={"do-not-terminate": "true"})
                for _ in range(20)]
        results = generate_terminate_idle_recommendations(recs)
        assert len(results) == 0

    def test_full_cost_impact(self):
        recs = [_make_record(resource_id="idle-res", cost=50.0, kwh=0.001) for _ in range(15)]
        results = generate_terminate_idle_recommendations(recs)
        assert abs(results[0].cost_impact_monthly_usd - 750.0) < 0.01  # 15×50

    def test_complexity_is_low(self):
        recs = self._idle_records("idle-res", count=15)
        results = generate_terminate_idle_recommendations(recs)
        assert results[0].complexity == "low"


# ---------------------------------------------------------------------------
# Test: generate_schedule_off_hours_recommendations
# ---------------------------------------------------------------------------

class TestScheduleOffHoursRecommendations:
    def test_dev_env_flagged(self):
        recs = [_make_record(resource_id="dev-res", tags={"env": "dev"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert len(results) == 1
        assert results[0].type == "schedule_off_hours"

    def test_staging_env_flagged(self):
        recs = [_make_record(resource_id="stg-res", tags={"env": "staging"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert len(results) == 1

    def test_production_not_flagged(self):
        recs = [_make_record(resource_id="prod-res", tags={"env": "production"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert len(results) == 0

    def test_cost_impact_is_60_pct(self):
        recs = [_make_record(resource_id="dev-res", cost=100.0, tags={"env": "dev"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert abs(results[0].cost_impact_monthly_usd - 60.0) < 0.01

    def test_co2e_impact_is_60_pct(self):
        recs = [_make_record(resource_id="dev-res", co2e=50.0, tags={"env": "dev"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert abs(results[0].co2e_impact_monthly_kg - 30.0) < 0.01

    def test_complexity_is_low(self):
        recs = [_make_record(resource_id="dev-res", tags={"env": "dev"})]
        results = generate_schedule_off_hours_recommendations(recs)
        assert results[0].complexity == "low"

    def test_dev_name_keyword(self):
        recs = [_make_record(resource_id="dev-server", resource_name="dev-worker-01")]
        results = generate_schedule_off_hours_recommendations(recs)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Test: generate_reserved_instance_recommendations
# ---------------------------------------------------------------------------

class TestReservedInstanceRecommendations:
    def test_30_day_resource_flagged(self):
        recs = [_make_record(resource_id="ri-res", cost=10.0) for _ in range(30)]
        results = generate_reserved_instance_recommendations(recs)
        assert len(results) == 1
        assert results[0].type == "reserved_instance"

    def test_fewer_than_30_days_not_flagged(self):
        recs = [_make_record(resource_id="ri-res") for _ in range(29)]
        results = generate_reserved_instance_recommendations(recs)
        assert len(results) == 0

    def test_cost_impact_is_30_pct(self):
        recs = [_make_record(resource_id="ri-res", cost=100.0) for _ in range(30)]
        results = generate_reserved_instance_recommendations(recs)
        assert abs(results[0].cost_impact_monthly_usd - 900.0) < 0.01  # 30×100×0.3

    def test_co2e_impact_is_zero(self):
        recs = [_make_record(resource_id="ri-res") for _ in range(30)]
        results = generate_reserved_instance_recommendations(recs)
        assert results[0].co2e_impact_monthly_kg == 0.0

    def test_water_impact_is_zero(self):
        recs = [_make_record(resource_id="ri-res") for _ in range(30)]
        results = generate_reserved_instance_recommendations(recs)
        assert results[0].water_impact_monthly_litres == 0.0

    def test_already_reserved_excluded(self):
        recs = [
            _make_record(resource_id="ri-res", tags={"pricing_model": "reserved"})
            for _ in range(30)
        ]
        results = generate_reserved_instance_recommendations(recs)
        assert len(results) == 0

    def test_complexity_is_medium(self):
        recs = [_make_record(resource_id="ri-res") for _ in range(30)]
        results = generate_reserved_instance_recommendations(recs)
        assert results[0].complexity == "medium"


# ---------------------------------------------------------------------------
# Test: _normalize_and_score
# ---------------------------------------------------------------------------

class TestNormalizeAndScore:
    def _make_rec(self, cost: float, co2e: float, water: float) -> Recommendation:
        return Recommendation(
            type="rightsize",
            resource_id="r1",
            provider="AWS",
            region="us-east-1",
            service_name="EC2",
            cost_impact_monthly_usd=cost,
            co2e_impact_monthly_kg=co2e,
            water_impact_monthly_litres=water,
            impact_score=0.0,
            cost_weight_used=0.0,
            carbon_weight_used=0.0,
            water_weight_used=0.0,
            complexity="low",
            implementation_steps=[],
            methodology_notes="",
        )

    def test_single_recommendation_gets_zero_score(self):
        recs = [self._make_rec(100.0, 10.0, 5.0)]
        result = _normalize_and_score(recs, 0.6, 0.3, 0.1)
        # With single item, all normalized values are 0 → score = 0
        assert result[0].impact_score == 0.0

    def test_highest_impact_gets_score_one(self):
        recs = [
            self._make_rec(100.0, 10.0, 5.0),
            self._make_rec(0.0, 0.0, 0.0),
        ]
        result = _normalize_and_score(recs, 0.6, 0.3, 0.1)
        # First rec is max in all dimensions → score = 0.6+0.3+0.1 = 1.0
        assert abs(result[0].impact_score - 1.0) < 0.001

    def test_lowest_impact_gets_score_zero(self):
        recs = [
            self._make_rec(100.0, 10.0, 5.0),
            self._make_rec(0.0, 0.0, 0.0),
        ]
        result = _normalize_and_score(recs, 0.6, 0.3, 0.1)
        assert result[1].impact_score == 0.0

    def test_weights_stored_on_recommendations(self):
        recs = [self._make_rec(100.0, 10.0, 5.0)]
        result = _normalize_and_score(recs, 0.5, 0.4, 0.1)
        assert result[0].cost_weight_used == 0.5
        assert result[0].carbon_weight_used == 0.4
        assert result[0].water_weight_used == 0.1

    def test_intermediate_score_is_weighted_sum(self):
        recs = [
            self._make_rec(100.0, 0.0, 0.0),  # only cost
            self._make_rec(0.0, 100.0, 0.0),  # only co2e
            self._make_rec(0.0, 0.0, 100.0),  # only water
        ]
        result = _normalize_and_score(recs, 0.6, 0.3, 0.1)
        scores = {r.type + str(r.cost_impact_monthly_usd): r.impact_score for r in result}
        # Cost-only rec: normalized_cost=1, others=0 → 0.6
        assert abs(result[0].impact_score - 0.6) < 0.001
        # CO2e-only rec: normalized_co2e=1, others=0 → 0.3
        assert abs(result[1].impact_score - 0.3) < 0.001
        # Water-only rec: normalized_water=1, others=0 → 0.1
        assert abs(result[2].impact_score - 0.1) < 0.001

    def test_empty_list_returns_empty(self):
        result = _normalize_and_score([], 0.6, 0.3, 0.1)
        assert result == []
