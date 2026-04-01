"""
carbon_models — CloudCarbon estimation library.

Exports all public estimation functions and result dataclasses for
Scope 1, Scope 2, Scope 3, and water consumption.
"""
from carbon_models.scope1_2 import (
    GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH,
    Scope1Result,
    Scope2Result,
    calculate_scope2_async,
    calculate_scope2_sync,
    compute_kwh_per_unit,
    estimate_kwh,
    get_scope1,
)
from carbon_models.scope3 import (
    Cat1Result,
    Cat3Result,
    Cat12Result,
    Scope3Estimate,
    estimate_cat1_embodied_async,
    estimate_cat1_embodied_sync,
    estimate_cat3_upstream_async,
    estimate_cat3_upstream_sync,
    estimate_cat12_eol_async,
    estimate_cat12_eol_sync,
    estimate_scope3_async,
    estimate_scope3_sync,
)
from carbon_models.water import (
    WaterEstimate,
    estimate_water_async,
    estimate_water_sync,
)

__all__ = [
    # Scope 1 / 2
    "GLOBAL_AVERAGE_CARBON_INTENSITY_GCO2_KWH",
    "Scope1Result",
    "Scope2Result",
    "calculate_scope2_async",
    "calculate_scope2_sync",
    "compute_kwh_per_unit",
    "estimate_kwh",
    "get_scope1",
    # Scope 3
    "Cat1Result",
    "Cat3Result",
    "Cat12Result",
    "Scope3Estimate",
    "estimate_cat1_embodied_async",
    "estimate_cat1_embodied_sync",
    "estimate_cat3_upstream_async",
    "estimate_cat3_upstream_sync",
    "estimate_cat12_eol_async",
    "estimate_cat12_eol_sync",
    "estimate_scope3_async",
    "estimate_scope3_sync",
    # Water
    "WaterEstimate",
    "estimate_water_async",
    "estimate_water_sync",
]
