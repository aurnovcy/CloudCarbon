"""
Water consumption estimation for cloud workloads.

Uses Water Usage Effectiveness (WUE) data from the region_water reference table,
supplemented by WRI Aqueduct water stress scores to produce a stress-adjusted
water consumption figure.

Units:
  estimated_kwh    — kWh (IT load)
  water_litres     — litres (absolute consumption)
  wue              — litres per kWh IT load
  stress_score     — WRI Aqueduct score 0–5 (higher = more stressed)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

_DEFAULT_WUE: float = 1.8                  # Air-cooled data centre default
_DEFAULT_COOLING_TYPE: str = "air"
_DEFAULT_DATA_SOURCE: str = "cooling_type_estimate"

# WRI Aqueduct stress multipliers (score band → multiplier)
# Score 0–1: low stress, 1–2: low-medium, 2–3: medium-high, 3–4: high, 4–5: extremely high
_STRESS_MULTIPLIERS: list[tuple[float, float, float]] = [
    (0.0, 1.0, 1.0),   # 0–1 → ×1.0
    (1.0, 2.0, 1.5),   # 1–2 → ×1.5
    (2.0, 3.0, 2.0),   # 2–3 → ×2.0
    (3.0, 4.0, 3.0),   # 3–4 → ×3.0
    (4.0, 5.0, 4.0),   # 4–5 → ×4.0
]


def _stress_multiplier(score: float) -> float:
    """Return the stress multiplier for a given WRI Aqueduct score."""
    for lo, hi, mult in _STRESS_MULTIPLIERS:
        if lo <= score < hi:
            return mult
    # Clamp: score exactly 5.0 uses the highest band
    if score >= 5.0:
        return 4.0
    return 1.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class WaterEstimate:
    """Water consumption estimate for a single billing record."""
    water_litres: float
    wue_litres_per_kwh: float
    water_stress_score: float | None
    water_stress_adjusted_litres: float
    water_data_source: str
    cooling_type: str
    region_found: bool = False


# ---------------------------------------------------------------------------
# Estimation functions
# ---------------------------------------------------------------------------

async def estimate_water_async(
    estimated_kwh: float,
    region_id: str | None,
    db_session: Any,
) -> WaterEstimate:
    """
    Async version: looks up region_water table for WUE and stress data.

    Args:
        estimated_kwh: Energy estimate in kWh.
        region_id: Cloud region identifier.
        db_session: SQLAlchemy AsyncSession.

    Returns:
        WaterEstimate with absolute and stress-adjusted consumption.
    """
    from sqlalchemy import text

    wue: float = _DEFAULT_WUE
    cooling_type: str = _DEFAULT_COOLING_TYPE
    data_source: str = _DEFAULT_DATA_SOURCE
    stress_score: float | None = None
    region_found: bool = False

    if region_id:
        try:
            result = await db_session.execute(
                text(
                    "SELECT wue_litres_per_kwh, cooling_type, water_data_source, wri_aqueduct_score "
                    "FROM region_water WHERE region_id = :rid LIMIT 1"
                ),
                {"rid": region_id},
            )
            row = result.fetchone()
            if row and row[0] is not None:
                wue = float(row[0])
                cooling_type = row[1] or _DEFAULT_COOLING_TYPE
                data_source = row[2] or _DEFAULT_DATA_SOURCE
                stress_score = float(row[3]) if row[3] is not None else None
                region_found = True
        except Exception as exc:
            logger.warning("Water DB lookup failed for region %s: %s", region_id, exc)

    return _calculate_water(estimated_kwh, wue, cooling_type, data_source, stress_score, region_found)


def estimate_water_sync(
    estimated_kwh: float,
    wue_litres_per_kwh: float | None = None,
    cooling_type: str | None = None,
    water_data_source: str | None = None,
    wri_aqueduct_score: float | None = None,
    region_found: bool = False,
) -> WaterEstimate:
    """
    Synchronous water estimation using pre-fetched values.
    Used in unit tests and batch pre-fetch patterns.

    Args:
        estimated_kwh: Energy estimate in kWh.
        wue_litres_per_kwh: WUE from region_water table (or None for default).
        cooling_type: Cooling technology description.
        water_data_source: Source of WUE data.
        wri_aqueduct_score: WRI Aqueduct water stress score (0–5).
        region_found: Whether region data was found in the table.

    Returns:
        WaterEstimate.
    """
    wue = wue_litres_per_kwh if wue_litres_per_kwh is not None else _DEFAULT_WUE
    ct = cooling_type or _DEFAULT_COOLING_TYPE
    ds = water_data_source or _DEFAULT_DATA_SOURCE

    return _calculate_water(estimated_kwh, wue, ct, ds, wri_aqueduct_score, region_found)


def _calculate_water(
    estimated_kwh: float,
    wue: float,
    cooling_type: str,
    data_source: str,
    stress_score: float | None,
    region_found: bool,
) -> WaterEstimate:
    """Core water calculation logic."""
    water_litres = estimated_kwh * wue

    if stress_score is not None:
        multiplier = _stress_multiplier(stress_score)
        stress_adjusted = water_litres * multiplier
    else:
        stress_adjusted = water_litres  # No adjustment; unknown stress

    return WaterEstimate(
        water_litres=water_litres,
        wue_litres_per_kwh=wue,
        water_stress_score=stress_score,
        water_stress_adjusted_litres=stress_adjusted,
        water_data_source=data_source,
        cooling_type=cooling_type,
        region_found=region_found,
    )
