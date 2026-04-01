"""
Unit tests for carbon_models.scope3 — Scope 3 estimation.

Tests use the synchronous helper functions with pre-fetched values.
No database or network calls are made.
"""
from __future__ import annotations

import sys
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.scope3 import (
    Cat1Result,
    Cat3Result,
    Cat12Result,
    Scope3Estimate,
    _usage_hours,
    _clamp,
    estimate_cat1_embodied_sync,
    estimate_cat3_upstream_sync,
    estimate_cat12_eol_sync,
    estimate_scope3_sync,
    _DEFAULT_MFG_CO2E_KG,
    _DEFAULT_LIFESPAN_HOURS,
    _DEFAULT_EOL_CO2E_KG,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(offset_hours: float = 0.0) -> datetime:
    return datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(hours=offset_hours)


class TestUsageHours:
    """Tests for _usage_hours() helper."""

    def test_one_hour(self) -> None:
        assert _usage_hours(_dt(0), _dt(1)) == pytest.approx(1.0)

    def test_720_hours(self) -> None:
        assert _usage_hours(_dt(0), _dt(720)) == pytest.approx(720.0)

    def test_none_start_returns_fallback(self) -> None:
        assert _usage_hours(None, _dt(1)) == pytest.approx(1.0)

    def test_none_end_returns_fallback(self) -> None:
        assert _usage_hours(_dt(0), None) == pytest.approx(1.0)

    def test_both_none_returns_fallback(self) -> None:
        assert _usage_hours(None, None) == pytest.approx(1.0)

    def test_zero_duration_returns_zero(self) -> None:
        assert _usage_hours(_dt(0), _dt(0)) == 0.0


class TestClamp:
    """Tests for _clamp() helper."""

    def test_clamp_below_lo(self) -> None:
        assert _clamp(-0.5, 0.0001, 1.0) == pytest.approx(0.0001)

    def test_clamp_above_hi(self) -> None:
        assert _clamp(1.5, 0.0001, 1.0) == pytest.approx(1.0)

    def test_within_range(self) -> None:
        assert _clamp(0.5, 0.0001, 1.0) == pytest.approx(0.5)


class TestCat1EmbodiedSync:
    """Tests for estimate_cat1_embodied_sync()."""

    def test_compute_instance_known(self) -> None:
        """
        Known instance: 4 vCPU / 64 total, 16 GB / 256 GB total.
        resource_share = ((4/64) + (16/256)) / 2 = (0.0625 + 0.0625) / 2 = 0.0625
        embodied_per_hour = 1000 / 35040 ≈ 0.028539
        cat1 = 0.028539 × 720 × 0.0625 ≈ 1.2843 kg CO2e
        """
        result = estimate_cat1_embodied_sync(
            service_category="Compute",
            resource_type="m5.xlarge",
            consumed_quantity=720.0,
            consumed_unit="Hrs",
            charge_period_start=_dt(0),
            charge_period_end=_dt(720),
            mfg_co2e_kg=1000.0,
            lifespan_hours=35040,
            vcpu_count=4.0,
            ram_gb=16.0,
            total_vcpu=64.0,
            total_ram=256.0,
            hardware_family="aws_m5_host",
            instance_found=True,
        )
        assert isinstance(result, Cat1Result)
        assert result.confidence == "high"
        assert result.hardware_family == "aws_m5_host"
        expected_share = ((4 / 64) + (16 / 256)) / 2
        expected_kwh = (1000.0 / 35040) * 720 * expected_share
        assert result.co2e_kg == pytest.approx(expected_kwh, rel=1e-4)
        assert result.resource_share == pytest.approx(expected_share, rel=1e-6)

    def test_networking_returns_zero(self) -> None:
        """Networking should return 0 kg CO2e (embodied carbon negligible)."""
        result = estimate_cat1_embodied_sync(
            service_category="Networking",
            resource_type=None,
            consumed_quantity=1000.0,
            consumed_unit="GB",
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        assert result.co2e_kg == 0.0
        assert result.hardware_family == "N/A"

    def test_serverless_invocations(self) -> None:
        """Serverless: resource_share = invocations × 0.0001, clamped to [0.0001, 1.0]."""
        result = estimate_cat1_embodied_sync(
            service_category="Compute",
            resource_type=None,
            consumed_quantity=1_000_000.0,
            consumed_unit="Invocations",
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        # resource_share = 1e6 × 1e-4 = 100 → clamped to 1.0
        assert result.resource_share == pytest.approx(1.0)

    def test_small_serverless_invocations(self) -> None:
        """Small serverless: 100 invocations × 0.0001 = 0.01 resource_share."""
        result = estimate_cat1_embodied_sync(
            service_category="Compute",
            resource_type=None,
            consumed_quantity=100.0,
            consumed_unit="Invocations",
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        assert result.resource_share == pytest.approx(0.01, rel=1e-6)

    def test_category_match_confidence_medium(self) -> None:
        """Category match without instance lookup → confidence='medium'."""
        result = estimate_cat1_embodied_sync(
            service_category="Database",
            resource_type=None,
            consumed_quantity=168.0,
            consumed_unit="Hrs",
            charge_period_start=_dt(0),
            charge_period_end=_dt(168),
            instance_found=False,
        )
        assert result.confidence == "medium"
        assert result.hardware_family == "generic_x86_server"

    def test_resource_share_clamped_above_zero(self) -> None:
        """resource_share should always be >= 0.0001."""
        result = estimate_cat1_embodied_sync(
            service_category="Compute",
            resource_type=None,
            consumed_quantity=0.0,
            consumed_unit="Hrs",
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
            vcpu_count=0.0,
            ram_gb=0.0,
        )
        assert result.resource_share >= 0.0001

    def test_returns_cat1result_type(self) -> None:
        result = estimate_cat1_embodied_sync(
            service_category="Compute",
            resource_type="m5.large",
            consumed_quantity=1.0,
            consumed_unit="Hrs",
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        assert isinstance(result, Cat1Result)
        assert hasattr(result, "co2e_kg")
        assert hasattr(result, "hardware_family")
        assert hasattr(result, "resource_share")
        assert hasattr(result, "confidence")
        assert hasattr(result, "methodology")


class TestCat3UpstreamSync:
    """Tests for estimate_cat3_upstream_sync()."""

    def test_all_fossil_grid(self) -> None:
        """
        All fossil (renewable_pct=0): blended_upstream = 0.15, total_factor = 0.21.
        10 kWh × 0.21 = 2.1 additional kWh × 500 gCO2/kWh / 1000 = 1.05 kg CO2e.
        """
        result = estimate_cat3_upstream_sync(10.0, 0.0, 500.0)
        assert isinstance(result, Cat3Result)
        assert result.upstream_factor_used == pytest.approx(0.21, rel=1e-6)
        assert result.co2e_kg == pytest.approx(10.0 * 0.21 * 500.0 / 1000.0, rel=1e-6)

    def test_all_renewable_grid(self) -> None:
        """
        All renewable (renewable_pct=1): blended_upstream = 0.03, total_factor = 0.09.
        """
        result = estimate_cat3_upstream_sync(10.0, 1.0, 50.0)
        assert result.upstream_factor_used == pytest.approx(0.09, rel=1e-6)
        assert result.co2e_kg == pytest.approx(10.0 * 0.09 * 50.0 / 1000.0, rel=1e-6)

    def test_mixed_grid(self) -> None:
        """
        50% renewable: blended = (0.5×0.15) + (0.5×0.03) = 0.09, total = 0.15.
        """
        result = estimate_cat3_upstream_sync(10.0, 0.5, 300.0)
        expected_factor = (0.5 * 0.15) + (0.5 * 0.03) + 0.06
        assert result.upstream_factor_used == pytest.approx(expected_factor, rel=1e-6)

    def test_confidence_always_medium(self) -> None:
        result = estimate_cat3_upstream_sync(1.0, 0.3, 400.0)
        assert result.confidence == "medium"

    def test_zero_kwh_gives_zero_carbon(self) -> None:
        result = estimate_cat3_upstream_sync(0.0, 0.3, 400.0)
        assert result.co2e_kg == 0.0


class TestCat12EolSync:
    """Tests for estimate_cat12_eol_sync()."""

    def test_known_values(self) -> None:
        """
        eol=50 kg, lifespan=35040 h, resource_share=0.0625, hours=720.
        cat12 = (50/35040) × 720 × 0.0625 ≈ 0.06415 kg CO2e.
        """
        result = estimate_cat12_eol_sync(
            hardware_family="aws_m5_host",
            resource_share=0.0625,
            charge_period_start=_dt(0),
            charge_period_end=_dt(720),
            eol_co2e_kg=50.0,
            lifespan_hours=35040,
        )
        expected = (50.0 / 35040) * 720 * 0.0625
        assert result.co2e_kg == pytest.approx(expected, rel=1e-4)

    def test_confidence_always_low(self) -> None:
        result = estimate_cat12_eol_sync(
            hardware_family="generic_x86_server",
            resource_share=0.1,
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        assert result.confidence == "low"

    def test_zero_resource_share_gives_zero(self) -> None:
        result = estimate_cat12_eol_sync(
            hardware_family="generic_x86_server",
            resource_share=0.0,
            charge_period_start=_dt(0),
            charge_period_end=_dt(720),
        )
        assert result.co2e_kg == 0.0

    def test_returns_cat12result_type(self) -> None:
        result = estimate_cat12_eol_sync(
            hardware_family="generic_x86_server",
            resource_share=0.05,
            charge_period_start=_dt(0),
            charge_period_end=_dt(1),
        )
        assert isinstance(result, Cat12Result)


class TestScope3SyncAggregator:
    """Tests for estimate_scope3_sync() aggregator."""

    def _make_cat1(self, co2e: float = 1.0, confidence: str = "high") -> Cat1Result:
        return Cat1Result(
            co2e_kg=co2e,
            hardware_family="generic_x86_server",
            resource_share=0.05,
            confidence=confidence,
            methodology="category_estimate_v1",
        )

    def _make_cat3(self, co2e: float = 0.5) -> Cat3Result:
        return Cat3Result(co2e_kg=co2e, upstream_factor_used=0.15, confidence="medium")

    def _make_cat12(self, co2e: float = 0.1) -> Cat12Result:
        return Cat12Result(co2e_kg=co2e, confidence="low")

    def test_total_is_sum_of_categories(self) -> None:
        cat1 = self._make_cat1(1.0)
        cat3 = self._make_cat3(0.5)
        cat12 = self._make_cat12(0.1)
        result = estimate_scope3_sync(cat1, cat3, cat12)
        assert result.scope3_total_co2e_kg == pytest.approx(1.6, rel=1e-6)

    def test_confidence_is_minimum(self) -> None:
        """Confidence should be the minimum across all three categories."""
        cat1 = self._make_cat1(confidence="high")
        cat3 = self._make_cat3()  # medium
        cat12 = self._make_cat12()  # low
        result = estimate_scope3_sync(cat1, cat3, cat12)
        assert result.confidence == "low"

    def test_all_high_confidence(self) -> None:
        cat1 = self._make_cat1(confidence="high")
        cat3 = Cat3Result(co2e_kg=0.5, upstream_factor_used=0.15, confidence="high")
        cat12 = Cat12Result(co2e_kg=0.1, confidence="high")
        result = estimate_scope3_sync(cat1, cat3, cat12)
        assert result.confidence == "high"

    def test_methodology_ref_contains_version(self) -> None:
        result = estimate_scope3_sync(
            self._make_cat1(), self._make_cat3(), self._make_cat12()
        )
        assert "cloudcarbon_scope3_v1" in result.methodology_ref

    def test_returns_scope3estimate_type(self) -> None:
        result = estimate_scope3_sync(
            self._make_cat1(), self._make_cat3(), self._make_cat12()
        )
        assert isinstance(result, Scope3Estimate)
        assert hasattr(result, "cat1")
        assert hasattr(result, "cat3")
        assert hasattr(result, "cat12")
        assert hasattr(result, "scope3_total_co2e_kg")
        assert hasattr(result, "confidence")
        assert hasattr(result, "methodology_ref")

    def test_zero_all_categories(self) -> None:
        cat1 = Cat1Result(co2e_kg=0.0, hardware_family="N/A", resource_share=0.0,
                          confidence="low", methodology="category_estimate_v1")
        cat3 = Cat3Result(co2e_kg=0.0, upstream_factor_used=0.0, confidence="medium")
        cat12 = Cat12Result(co2e_kg=0.0, confidence="low")
        result = estimate_scope3_sync(cat1, cat3, cat12)
        assert result.scope3_total_co2e_kg == 0.0
