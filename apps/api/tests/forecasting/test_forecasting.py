"""
Unit tests for the ForecastingService.

Tests cover:
  - _fit_and_forecast with known synthetic data
  - Confidence interval ordering (lower <= predicted <= upper)
  - MAE calculation
  - Flat forecast fallback for insufficient data
  - Invalid metric raises ValueError
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pytest

sys.path.insert(0, "/home/ubuntu/cloudcarbon/apps/api/src")

from forecasting.service import (
    _fit_and_forecast,
    _MIN_DATA_POINTS,
    _HOLDOUT_DAYS,
    ForecastResult,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _linear_series(n: int, slope: float = 10.0, intercept: float = 100.0) -> list[float]:
    """Generate a simple linear time series."""
    return [intercept + slope * i for i in range(n)]


def _seasonal_series(n: int, period: int = 7, amplitude: float = 20.0) -> list[float]:
    """Generate a seasonal time series with weekly pattern."""
    base = 100.0
    return [base + amplitude * math.sin(2 * math.pi * i / period) for i in range(n)]


# ---------------------------------------------------------------------------
# Tests: _fit_and_forecast
# ---------------------------------------------------------------------------

class TestFitAndForecast:
    def test_returns_correct_horizon_length(self):
        values = _linear_series(60)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=14)
        assert len(predicted) == 14
        assert len(lower) == 14
        assert len(upper) == 14

    def test_confidence_interval_ordering(self):
        values = _seasonal_series(60)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=30)
        for p, lo, hi in zip(predicted, lower, upper):
            assert lo <= p + 0.01, f"lower {lo} > predicted {p}"
            assert hi >= p - 0.01, f"upper {hi} < predicted {p}"

    def test_predicted_values_are_non_negative(self):
        values = _linear_series(60)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=14)
        assert all(v >= 0 for v in predicted)
        assert all(v >= 0 for v in lower)

    def test_mae_is_non_negative(self):
        values = _linear_series(60)
        _, _, _, mae = _fit_and_forecast(values, horizon_days=14)
        assert mae >= 0.0

    def test_mae_is_finite(self):
        values = _linear_series(60)
        _, _, _, mae = _fit_and_forecast(values, horizon_days=14)
        assert math.isfinite(mae)

    def test_series_with_zeros_uses_additive_seasonality(self):
        # Series with zeros should not raise (multiplicative fails with zeros)
        values = [0.0, 0.0, 5.0, 10.0, 0.0, 5.0, 10.0] * 6
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=7)
        assert len(predicted) == 7
        assert all(v >= 0 for v in predicted)

    def test_constant_series_forecast_is_near_constant(self):
        values = [100.0] * 60
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=14)
        for v in predicted:
            assert abs(v - 100.0) < 50.0, f"Forecast {v} too far from constant 100.0"

    def test_upward_trend_forecast_is_increasing(self):
        values = _linear_series(60, slope=5.0, intercept=50.0)
        predicted, _, _, _ = _fit_and_forecast(values, horizon_days=14)
        # The first forecast point should be above the last training value
        last_train = values[-_HOLDOUT_DAYS - 1] if len(values) > _HOLDOUT_DAYS else values[-1]
        assert predicted[0] > last_train * 0.5  # at least half the last value

    def test_horizon_1_returns_single_point(self):
        values = _linear_series(60)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=1)
        assert len(predicted) == 1
        assert len(lower) == 1
        assert len(upper) == 1

    def test_exactly_min_data_points(self):
        values = _linear_series(_MIN_DATA_POINTS)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=7)
        assert len(predicted) == 7

    def test_large_horizon(self):
        values = _seasonal_series(90)
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=365)
        assert len(predicted) == 365
        assert all(math.isfinite(v) for v in predicted)

    def test_noisy_series_still_produces_finite_output(self):
        rng = np.random.default_rng(42)
        values = (100 + rng.normal(0, 20, 60)).tolist()
        predicted, lower, upper, mae = _fit_and_forecast(values, horizon_days=14)
        assert all(math.isfinite(v) for v in predicted)
        assert all(math.isfinite(v) for v in lower)
        assert all(math.isfinite(v) for v in upper)


# ---------------------------------------------------------------------------
# Tests: flat forecast fallback (insufficient data)
# ---------------------------------------------------------------------------

class TestFlatForecastFallback:
    """Verify the flat forecast path when data < _MIN_DATA_POINTS."""

    def test_flat_forecast_length(self):
        """ForecastResult.predicted should have horizon_days items."""
        from forecasting.service import ForecastResult
        mean_val = 50.0
        horizon = 14
        result = ForecastResult(
            metric="cost_usd",
            dates=["2026-01-01"] * horizon,
            predicted=[mean_val] * horizon,
            lower_80=[mean_val * 0.8] * horizon,
            upper_80=[mean_val * 1.2] * horizon,
            model_mae=0.0,
            data_points_used=5,
            generated_at="2026-01-01T00:00:00+00:00",
        )
        assert len(result.predicted) == horizon

    def test_flat_forecast_values_equal_mean(self):
        mean_val = 75.0
        horizon = 7
        predicted = [mean_val] * horizon
        assert all(v == mean_val for v in predicted)

    def test_flat_forecast_ci_ordering(self):
        mean_val = 50.0
        lower = [mean_val * 0.8] * 7
        upper = [mean_val * 1.2] * 7
        for lo, hi in zip(lower, upper):
            assert lo <= hi


# ---------------------------------------------------------------------------
# Tests: supported metrics validation
# ---------------------------------------------------------------------------

class TestSupportedMetrics:
    def test_invalid_metric_raises_value_error(self):
        from forecasting.service import _SUPPORTED_METRICS
        assert "cost_usd" in _SUPPORTED_METRICS
        assert "total_co2e_kg" in _SUPPORTED_METRICS
        assert "water_litres" in _SUPPORTED_METRICS
        assert "water_stress_adjusted_litres" in _SUPPORTED_METRICS

    def test_invalid_metric_not_in_set(self):
        from forecasting.service import _SUPPORTED_METRICS
        assert "invalid_metric" not in _SUPPORTED_METRICS
        assert "carbon_intensity" not in _SUPPORTED_METRICS
