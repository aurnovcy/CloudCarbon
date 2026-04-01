"""
Unit tests for carbon_models.water — water consumption estimation.

All tests use known inputs and verify exact or approximate outputs.
No database or network calls are made.
"""
from __future__ import annotations

import sys
import pytest

sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.water import (
    WaterEstimate,
    _stress_multiplier,
    _DEFAULT_WUE,
    _DEFAULT_COOLING_TYPE,
    _DEFAULT_DATA_SOURCE,
    estimate_water_sync,
)


class TestStressMultiplier:
    """Tests for _stress_multiplier() band lookup."""

    def test_band_0_to_1(self) -> None:
        assert _stress_multiplier(0.0) == pytest.approx(1.0)
        assert _stress_multiplier(0.5) == pytest.approx(1.0)
        assert _stress_multiplier(0.99) == pytest.approx(1.0)

    def test_band_1_to_2(self) -> None:
        assert _stress_multiplier(1.0) == pytest.approx(1.5)
        assert _stress_multiplier(1.5) == pytest.approx(1.5)
        assert _stress_multiplier(1.99) == pytest.approx(1.5)

    def test_band_2_to_3(self) -> None:
        assert _stress_multiplier(2.0) == pytest.approx(2.0)
        assert _stress_multiplier(2.5) == pytest.approx(2.0)

    def test_band_3_to_4(self) -> None:
        assert _stress_multiplier(3.0) == pytest.approx(3.0)
        assert _stress_multiplier(3.9) == pytest.approx(3.0)

    def test_band_4_to_5(self) -> None:
        assert _stress_multiplier(4.0) == pytest.approx(4.0)
        assert _stress_multiplier(4.9) == pytest.approx(4.0)

    def test_exactly_5(self) -> None:
        assert _stress_multiplier(5.0) == pytest.approx(4.0)

    def test_above_5_clamped(self) -> None:
        assert _stress_multiplier(6.0) == pytest.approx(4.0)


class TestEstimateWaterSync:
    """Tests for estimate_water_sync() using pre-fetched values."""

    def test_known_wue_no_stress(self) -> None:
        """
        10 kWh × 1.2 L/kWh = 12.0 L.
        No stress score → stress_adjusted = 12.0 L.
        """
        result = estimate_water_sync(10.0, wue_litres_per_kwh=1.2)
        assert isinstance(result, WaterEstimate)
        assert result.water_litres == pytest.approx(12.0, rel=1e-6)
        assert result.water_stress_adjusted_litres == pytest.approx(12.0, rel=1e-6)
        assert result.water_stress_score is None

    def test_stress_score_band_2_to_3(self) -> None:
        """
        10 kWh × 1.8 L/kWh = 18.0 L.
        Stress score 2.5 → multiplier 2.0 → adjusted = 36.0 L.
        """
        result = estimate_water_sync(10.0, wue_litres_per_kwh=1.8, wri_aqueduct_score=2.5)
        assert result.water_litres == pytest.approx(18.0, rel=1e-6)
        assert result.water_stress_adjusted_litres == pytest.approx(36.0, rel=1e-6)
        assert result.water_stress_score == pytest.approx(2.5)

    def test_stress_score_band_4_to_5(self) -> None:
        """
        5 kWh × 2.0 L/kWh = 10.0 L.
        Stress score 4.5 → multiplier 4.0 → adjusted = 40.0 L.
        """
        result = estimate_water_sync(5.0, wue_litres_per_kwh=2.0, wri_aqueduct_score=4.5)
        assert result.water_stress_adjusted_litres == pytest.approx(40.0, rel=1e-6)

    def test_default_wue_when_not_provided(self) -> None:
        """Uses _DEFAULT_WUE (1.8) when wue_litres_per_kwh is None."""
        result = estimate_water_sync(10.0)
        assert result.wue_litres_per_kwh == pytest.approx(_DEFAULT_WUE)
        assert result.water_litres == pytest.approx(10.0 * _DEFAULT_WUE, rel=1e-6)

    def test_default_cooling_type(self) -> None:
        result = estimate_water_sync(1.0)
        assert result.cooling_type == _DEFAULT_COOLING_TYPE

    def test_default_data_source(self) -> None:
        result = estimate_water_sync(1.0)
        assert result.water_data_source == _DEFAULT_DATA_SOURCE

    def test_custom_cooling_type(self) -> None:
        result = estimate_water_sync(1.0, cooling_type="evaporative")
        assert result.cooling_type == "evaporative"

    def test_region_found_flag(self) -> None:
        result = estimate_water_sync(1.0, region_found=True)
        assert result.region_found is True

    def test_region_not_found_flag(self) -> None:
        result = estimate_water_sync(1.0, region_found=False)
        assert result.region_found is False

    def test_zero_kwh_gives_zero_water(self) -> None:
        result = estimate_water_sync(0.0, wue_litres_per_kwh=1.5, wri_aqueduct_score=3.0)
        assert result.water_litres == 0.0
        assert result.water_stress_adjusted_litres == 0.0

    def test_low_stress_no_adjustment(self) -> None:
        """Stress score 0.5 → multiplier 1.0 → no adjustment."""
        result = estimate_water_sync(10.0, wue_litres_per_kwh=1.0, wri_aqueduct_score=0.5)
        assert result.water_stress_adjusted_litres == pytest.approx(result.water_litres, rel=1e-6)

    def test_provider_disclosed_data_source(self) -> None:
        result = estimate_water_sync(
            1.0,
            wue_litres_per_kwh=0.9,
            water_data_source="provider_disclosed",
        )
        assert result.water_data_source == "provider_disclosed"

    def test_returns_water_estimate_type(self) -> None:
        result = estimate_water_sync(5.0)
        assert isinstance(result, WaterEstimate)
        assert hasattr(result, "water_litres")
        assert hasattr(result, "wue_litres_per_kwh")
        assert hasattr(result, "water_stress_score")
        assert hasattr(result, "water_stress_adjusted_litres")
        assert hasattr(result, "water_data_source")
        assert hasattr(result, "cooling_type")

    def test_high_wue_data_centre(self) -> None:
        """
        High WUE data centre (3.5 L/kWh) in high-stress region (score 3.5).
        100 kWh × 3.5 = 350 L × 3.0 = 1050 L adjusted.
        """
        result = estimate_water_sync(100.0, wue_litres_per_kwh=3.5, wri_aqueduct_score=3.5)
        assert result.water_litres == pytest.approx(350.0, rel=1e-6)
        assert result.water_stress_adjusted_litres == pytest.approx(1050.0, rel=1e-6)
