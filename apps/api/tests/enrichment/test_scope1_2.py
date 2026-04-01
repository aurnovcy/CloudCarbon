"""
Unit tests for carbon_models.scope1_2 — Scope 1 and Scope 2 estimation.

All tests use known inputs and verify exact or approximate outputs.
No database or network calls are made.
"""
from __future__ import annotations

import sys
import pytest

# Ensure carbon_models is importable
sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.scope1_2 import (
    GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH,
    Scope1Result,
    Scope2Result,
    calculate_scope2_sync,
    compute_kwh_per_unit,
    estimate_kwh,
    get_scope1,
)


class TestEstimateKwh:
    """Tests for estimate_kwh() energy estimation function."""

    def test_compute_vcpu_hours(self) -> None:
        """720 vCPU-hours × 0.005 kWh/vCPU-hr = 3.6 kWh."""
        result = estimate_kwh("Compute", 720.0, "Hrs", "m5.xlarge", 138.24)
        assert result == pytest.approx(3.6, rel=1e-6)

    def test_compute_gpu_hours(self) -> None:
        """10 GPU-hours × 0.3 kWh/GPU-hr = 3.0 kWh."""
        result = estimate_kwh("Compute", 10.0, "GPU-Hours", "p3.2xlarge", 30.0)
        assert result == pytest.approx(3.0, rel=1e-6)

    def test_compute_serverless_invocations(self) -> None:
        """1,000,000 invocations × 0.0000003 kWh = 0.3 kWh."""
        result = estimate_kwh("Compute", 1_000_000.0, "Invocations", None, 0.20)
        assert result == pytest.approx(0.3, rel=1e-6)

    def test_storage_gb_month(self) -> None:
        """512 GB-months × 0.000002 kWh/GB-month = 0.001024 kWh."""
        result = estimate_kwh("Storage", 512.0, "GB-Mo", None, 9.22)
        assert result == pytest.approx(0.001024, rel=1e-6)

    def test_networking_gb_transferred(self) -> None:
        """100 GB × 0.001 kWh/GB = 0.1 kWh."""
        result = estimate_kwh("Networking", 100.0, "GB", None, 9.0)
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_database_vcpu_hours(self) -> None:
        """168 vCPU-hours × 0.006 kWh/vCPU-hr = 1.008 kWh."""
        result = estimate_kwh("Database", 168.0, "vCPU-Hours", "db.r5.large", 84.0)
        assert result == pytest.approx(1.008, rel=1e-6)

    def test_ai_ml_gpu_hours(self) -> None:
        """8 GPU-hours × 0.4 kWh/GPU-hr = 3.2 kWh."""
        result = estimate_kwh("AI and Machine Learning", 8.0, "GPU-Hours", None, 40.0)
        assert result == pytest.approx(3.2, rel=1e-6)

    def test_unrecognized_category_spend_fallback(self) -> None:
        """Unknown category: $50 × 0.002 kWh/$ = 0.1 kWh."""
        result = estimate_kwh("Other", None, None, None, 50.0)
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_zero_quantity_falls_back_to_spend(self) -> None:
        """Zero quantity should use spend-based fallback."""
        result = estimate_kwh("Compute", 0.0, "Hrs", "m5.large", 10.0)
        assert result == pytest.approx(10.0 * 0.002, rel=1e-6)

    def test_none_quantity_falls_back_to_spend(self) -> None:
        """None quantity should use spend-based fallback."""
        result = estimate_kwh("Storage", None, None, None, 5.0)
        assert result == pytest.approx(5.0 * 0.002, rel=1e-6)

    def test_returns_float(self) -> None:
        """Result should always be a float."""
        result = estimate_kwh("Compute", 100.0, "Hrs", None, 20.0)
        assert isinstance(result, float)

    def test_zero_cost_zero_quantity_returns_zero(self) -> None:
        """Zero cost and zero quantity should return 0.0."""
        result = estimate_kwh("Other", 0.0, None, None, 0.0)
        assert result == 0.0


class TestComputeKwhPerUnit:
    """Tests for compute_kwh_per_unit() coefficient resolver."""

    def test_default_vcpu(self) -> None:
        assert compute_kwh_per_unit("m5.xlarge", "Hrs") == pytest.approx(0.005)

    def test_gpu_resource_type(self) -> None:
        assert compute_kwh_per_unit("p3.2xlarge-gpu", "Hrs") == pytest.approx(0.3)

    def test_gpu_unit(self) -> None:
        assert compute_kwh_per_unit(None, "GPU-Hours") == pytest.approx(0.3)

    def test_invocation_unit(self) -> None:
        assert compute_kwh_per_unit(None, "Invocations") == pytest.approx(0.0000003)

    def test_request_unit(self) -> None:
        assert compute_kwh_per_unit(None, "Requests") == pytest.approx(0.0000003)

    def test_none_inputs_default_vcpu(self) -> None:
        assert compute_kwh_per_unit(None, None) == pytest.approx(0.005)


class TestScope1:
    """Tests for get_scope1() — always zero for cloud customers."""

    def test_scope1_is_zero(self) -> None:
        result = get_scope1()
        assert isinstance(result, Scope1Result)
        assert result.co2e_kg == 0.0

    def test_scope1_has_methodology_note(self) -> None:
        result = get_scope1()
        assert len(result.methodology_note) > 0
        assert "Scope 1" in result.methodology_note


class TestScope2Sync:
    """Tests for calculate_scope2_sync() using pre-fetched intensity values."""

    def test_known_intensity_location(self) -> None:
        """
        10 kWh × 415 gCO2/kWh / 1000 = 4.15 kg CO2e (location-based).
        """
        result = calculate_scope2_sync(10.0, "us-east-1", 415.0, 350.0)
        assert isinstance(result, Scope2Result)
        assert result.location_kg == pytest.approx(4.15, rel=1e-6)

    def test_known_intensity_market(self) -> None:
        """
        10 kWh × 350 gCO2/kWh / 1000 = 3.5 kg CO2e (market-based).
        """
        result = calculate_scope2_sync(10.0, "us-east-1", 415.0, 350.0)
        assert result.market_kg == pytest.approx(3.5, rel=1e-6)

    def test_region_found_flag(self) -> None:
        """region_found=True when intensity is provided."""
        result = calculate_scope2_sync(10.0, "eu-west-1", 300.0)
        assert result.region_found is True

    def test_global_fallback_when_no_intensity(self) -> None:
        """Uses global average (475 gCO2/kWh) when intensity is None."""
        result = calculate_scope2_sync(10.0, "unknown-region", None)
        assert result.intensity_gco2_kwh == pytest.approx(GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH)
        assert result.region_found is False
        assert result.location_kg == pytest.approx(10.0 * 475.0 / 1000.0, rel=1e-6)

    def test_zero_kwh_gives_zero_carbon(self) -> None:
        result = calculate_scope2_sync(0.0, "us-east-1", 415.0)
        assert result.location_kg == 0.0
        assert result.market_kg == 0.0

    def test_market_defaults_to_location_when_not_provided(self) -> None:
        """market_intensity defaults to location intensity if not provided."""
        result = calculate_scope2_sync(5.0, "eu-central-1", 400.0)
        assert result.market_kg == pytest.approx(result.location_kg, rel=1e-6)

    def test_high_intensity_region(self) -> None:
        """Coal-heavy region: 900 gCO2/kWh."""
        result = calculate_scope2_sync(1.0, "cn-north-1", 900.0)
        assert result.location_kg == pytest.approx(0.9, rel=1e-6)

    def test_low_intensity_region(self) -> None:
        """Hydro-heavy region: 20 gCO2/kWh."""
        result = calculate_scope2_sync(100.0, "eu-north-1", 20.0)
        assert result.location_kg == pytest.approx(2.0, rel=1e-6)

    def test_returns_scope2result_type(self) -> None:
        result = calculate_scope2_sync(1.0, "us-east-1", 415.0)
        assert isinstance(result, Scope2Result)
        assert hasattr(result, "location_kg")
        assert hasattr(result, "market_kg")
        assert hasattr(result, "intensity_gco2_kwh")
        assert hasattr(result, "region_found")
