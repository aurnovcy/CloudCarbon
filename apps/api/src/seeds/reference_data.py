"""
Reference data seed script.

Populates:
  region_carbon_intensity  — grid carbon intensity per cloud region
  region_water             — water usage effectiveness per cloud region
  hardware_carbon_coefficients — embodied carbon per hardware family
  instance_hardware_map    — instance type → hardware family mapping

Data sources:
  - AWS Sustainability reports (2023): https://sustainability.aboutamazon.com
  - Azure Sustainability reports (2023): https://azure.microsoft.com/en-us/global-infrastructure/sustainability/
  - GCP Sustainability reports (2023): https://cloud.google.com/sustainability
  - Boavizta hardware database: https://boavizta.org
  - Electricity Maps: https://app.electricitymaps.com
  - WRI Aqueduct: https://www.wri.org/aqueduct

Note: Values marked with [estimate] are approximations where provider data is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# Allow running directly: python -m src.seeds.reference_data
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

REGION_CARBON_INTENSITY = [
    # AWS regions — source: AWS Customer Carbon Footprint Tool methodology
    # carbon_intensity_gco2_kwh: location-based (grid average)
    # carbon_intensity_market_gco2_kwh: market-based (with renewable energy certificates)
    # renewable_pct: % renewable energy in AWS's energy mix for that region (2023)
    {
        "region_id": "us-east-1",
        "provider": "aws",
        "region_name": "US East (N. Virginia)",
        "grid_zone": "US-MIDA-PJM",
        "carbon_intensity_gco2_kwh": 315.4,
        "carbon_intensity_market_gco2_kwh": 0.0,   # AWS 100% renewable match
        "renewable_pct": 100.0,
    },
    {
        "region_id": "us-east-2",
        "provider": "aws",
        "region_name": "US East (Ohio)",
        "grid_zone": "US-MIDW-MISO",
        "carbon_intensity_gco2_kwh": 388.2,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "us-west-1",
        "provider": "aws",
        "region_name": "US West (N. California)",
        "grid_zone": "US-CAL-CISO",
        "carbon_intensity_gco2_kwh": 210.5,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "us-west-2",
        "provider": "aws",
        "region_name": "US West (Oregon)",
        "grid_zone": "US-NW-PACW",
        "carbon_intensity_gco2_kwh": 98.3,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "eu-west-1",
        "provider": "aws",
        "region_name": "Europe (Ireland)",
        "grid_zone": "IE",
        "carbon_intensity_gco2_kwh": 295.7,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "eu-central-1",
        "provider": "aws",
        "region_name": "Europe (Frankfurt)",
        "grid_zone": "DE",
        "carbon_intensity_gco2_kwh": 366.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "ap-southeast-1",
        "provider": "aws",
        "region_name": "Asia Pacific (Singapore)",
        "grid_zone": "SG",
        "carbon_intensity_gco2_kwh": 408.0,
        "carbon_intensity_market_gco2_kwh": 408.0,  # Limited renewable options in SG
        "renewable_pct": 2.0,
    },
    {
        "region_id": "ap-northeast-1",
        "provider": "aws",
        "region_name": "Asia Pacific (Tokyo)",
        "grid_zone": "JP-TK",
        "carbon_intensity_gco2_kwh": 463.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    # Azure regions — source: Microsoft Sustainability Calculator methodology
    {
        "region_id": "eastus",
        "provider": "azure",
        "region_name": "East US",
        "grid_zone": "US-MIDA-PJM",
        "carbon_intensity_gco2_kwh": 315.4,
        "carbon_intensity_market_gco2_kwh": 0.0,   # Microsoft 100% renewable match
        "renewable_pct": 100.0,
    },
    {
        "region_id": "westus2",
        "provider": "azure",
        "region_name": "West US 2",
        "grid_zone": "US-NW-PACW",
        "carbon_intensity_gco2_kwh": 98.3,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "northeurope",
        "provider": "azure",
        "region_name": "North Europe",
        "grid_zone": "IE",
        "carbon_intensity_gco2_kwh": 295.7,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "westeurope",
        "provider": "azure",
        "region_name": "West Europe",
        "grid_zone": "NL",
        "carbon_intensity_gco2_kwh": 283.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "eastasia",
        "provider": "azure",
        "region_name": "East Asia",
        "grid_zone": "HK",
        "carbon_intensity_gco2_kwh": 598.0,
        "carbon_intensity_market_gco2_kwh": 598.0,  # [estimate]
        "renewable_pct": 3.0,
    },
    {
        "region_id": "southeastasia",
        "provider": "azure",
        "region_name": "Southeast Asia",
        "grid_zone": "SG",
        "carbon_intensity_gco2_kwh": 408.0,
        "carbon_intensity_market_gco2_kwh": 408.0,
        "renewable_pct": 2.0,
    },
    # GCP regions — source: Google Environmental Report 2023
    {
        "region_id": "us-central1",
        "provider": "gcp",
        "region_name": "US Central (Iowa)",
        "grid_zone": "US-MIDW-MISO",
        "carbon_intensity_gco2_kwh": 388.2,
        "carbon_intensity_market_gco2_kwh": 0.0,   # Google 100% renewable match
        "renewable_pct": 100.0,
    },
    {
        "region_id": "us-east1",
        "provider": "gcp",
        "region_name": "US East (South Carolina)",
        "grid_zone": "US-SE-SERC",
        "carbon_intensity_gco2_kwh": 350.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "europe-west1",
        "provider": "gcp",
        "region_name": "Europe (Belgium)",
        "grid_zone": "BE",
        "carbon_intensity_gco2_kwh": 167.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "europe-west4",
        "provider": "gcp",
        "region_name": "Europe (Netherlands)",
        "grid_zone": "NL",
        "carbon_intensity_gco2_kwh": 283.0,
        "carbon_intensity_market_gco2_kwh": 0.0,
        "renewable_pct": 100.0,
    },
    {
        "region_id": "asia-east1",
        "provider": "gcp",
        "region_name": "Asia Pacific (Taiwan)",
        "grid_zone": "TW",
        "carbon_intensity_gco2_kwh": 509.0,
        "carbon_intensity_market_gco2_kwh": 509.0,  # [estimate]
        "renewable_pct": 6.0,
    },
    {
        "region_id": "asia-southeast1",
        "provider": "gcp",
        "region_name": "Asia Pacific (Singapore)",
        "grid_zone": "SG",
        "carbon_intensity_gco2_kwh": 408.0,
        "carbon_intensity_market_gco2_kwh": 408.0,
        "renewable_pct": 2.0,
    },
    # Alibaba Cloud regions — source: Alibaba Cloud Sustainability Report 2023 [estimates]
    {
        "region_id": "cn-hangzhou",
        "provider": "alibaba",
        "region_name": "China (Hangzhou)",
        "grid_zone": "CN-EC",
        "carbon_intensity_gco2_kwh": 581.0,
        "carbon_intensity_market_gco2_kwh": 581.0,  # [estimate]
        "renewable_pct": 25.0,
    },
    {
        "region_id": "cn-shanghai",
        "provider": "alibaba",
        "region_name": "China (Shanghai)",
        "grid_zone": "CN-EC",
        "carbon_intensity_gco2_kwh": 581.0,
        "carbon_intensity_market_gco2_kwh": 581.0,  # [estimate]
        "renewable_pct": 25.0,
    },
    {
        "region_id": "cn-beijing",
        "provider": "alibaba",
        "region_name": "China (Beijing)",
        "grid_zone": "CN-NC",
        "carbon_intensity_gco2_kwh": 620.0,
        "carbon_intensity_market_gco2_kwh": 620.0,  # [estimate]
        "renewable_pct": 20.0,
    },
    {
        "region_id": "ap-southeast-1",
        "provider": "alibaba",
        "region_name": "Asia Pacific (Singapore)",
        "grid_zone": "SG",
        "carbon_intensity_gco2_kwh": 408.0,
        "carbon_intensity_market_gco2_kwh": 408.0,
        "renewable_pct": 2.0,
    },
]

REGION_WATER = [
    # AWS — source: AWS Water Stewardship Report 2023
    # WUE values: provider_disclosed where available, cooling_type_estimate otherwise
    {"region_id": "us-east-1", "provider": "aws", "wue_litres_per_kwh": 0.18,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 1.8, "latitude": 38.9072, "longitude": -77.0369},
    {"region_id": "us-east-2", "provider": "aws", "wue_litres_per_kwh": 0.22,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 1.5, "latitude": 39.9612, "longitude": -82.9988},
    {"region_id": "us-west-1", "provider": "aws", "wue_litres_per_kwh": 0.35,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 3.8, "latitude": 37.7749, "longitude": -122.4194},
    {"region_id": "us-west-2", "provider": "aws", "wue_litres_per_kwh": 0.14,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 2.1, "latitude": 45.5051, "longitude": -122.6750},
    {"region_id": "eu-west-1", "provider": "aws", "wue_litres_per_kwh": 0.12,
     "cooling_type": "air", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 0.8, "latitude": 53.3498, "longitude": -6.2603},
    {"region_id": "eu-central-1", "provider": "aws", "wue_litres_per_kwh": 0.20,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 1.2, "latitude": 50.1109, "longitude": 8.6821},
    {"region_id": "ap-southeast-1", "provider": "aws", "wue_litres_per_kwh": 0.40,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.5, "latitude": 1.3521, "longitude": 103.8198},
    {"region_id": "ap-northeast-1", "provider": "aws", "wue_litres_per_kwh": 0.25,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 1.0, "latitude": 35.6762, "longitude": 139.6503},
    # Azure — source: Microsoft Water Stewardship Report 2023
    {"region_id": "eastus", "provider": "azure", "wue_litres_per_kwh": 0.19,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 1.8, "latitude": 37.3719, "longitude": -79.8164},
    {"region_id": "westus2", "provider": "azure", "wue_litres_per_kwh": 0.13,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 2.3, "latitude": 47.6062, "longitude": -122.3321},
    {"region_id": "northeurope", "provider": "azure", "wue_litres_per_kwh": 0.10,
     "cooling_type": "air", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 0.6, "latitude": 53.3498, "longitude": -6.2603},
    {"region_id": "westeurope", "provider": "azure", "wue_litres_per_kwh": 0.15,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 0.9, "latitude": 52.3676, "longitude": 4.9041},
    {"region_id": "eastasia", "provider": "azure", "wue_litres_per_kwh": 0.45,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 3.2, "latitude": 22.3193, "longitude": 114.1694},
    {"region_id": "southeastasia", "provider": "azure", "wue_litres_per_kwh": 0.42,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.5, "latitude": 1.3521, "longitude": 103.8198},
    # GCP — source: Google Environmental Report 2023
    {"region_id": "us-central1", "provider": "gcp", "wue_litres_per_kwh": 0.91,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 1.3, "latitude": 41.8780, "longitude": -93.0977},
    {"region_id": "us-east1", "provider": "gcp", "wue_litres_per_kwh": 0.55,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 1.1, "latitude": 33.1960, "longitude": -80.0131},
    {"region_id": "europe-west1", "provider": "gcp", "wue_litres_per_kwh": 0.14,
     "cooling_type": "air", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 0.7, "latitude": 50.4501, "longitude": 3.8136},
    {"region_id": "europe-west4", "provider": "gcp", "wue_litres_per_kwh": 0.18,
     "cooling_type": "evaporative", "water_data_source": "provider_disclosed",
     "wri_aqueduct_score": 0.9, "latitude": 53.4386, "longitude": 6.8355},
    {"region_id": "asia-east1", "provider": "gcp", "wue_litres_per_kwh": 0.38,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.8, "latitude": 25.0330, "longitude": 121.5654},
    {"region_id": "asia-southeast1", "provider": "gcp", "wue_litres_per_kwh": 0.40,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.5, "latitude": 1.3521, "longitude": 103.8198},
    # Alibaba Cloud [estimates]
    {"region_id": "cn-hangzhou", "provider": "alibaba", "wue_litres_per_kwh": 0.30,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.2, "latitude": 30.2741, "longitude": 120.1551},
    {"region_id": "cn-shanghai", "provider": "alibaba", "wue_litres_per_kwh": 0.32,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.4, "latitude": 31.2304, "longitude": 121.4737},
    {"region_id": "cn-beijing", "provider": "alibaba", "wue_litres_per_kwh": 0.35,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 3.5, "latitude": 39.9042, "longitude": 116.4074},
    {"region_id": "ap-southeast-1", "provider": "alibaba", "wue_litres_per_kwh": 0.40,
     "cooling_type": "evaporative", "water_data_source": "cooling_type_estimate",
     "wri_aqueduct_score": 2.5, "latitude": 1.3521, "longitude": 103.8198},
]

HARDWARE_CARBON_COEFFICIENTS = [
    # Source: Boavizta database (https://boavizta.org) + Dell/HPE sustainability reports
    # mfg_co2e_kg: manufacturing embodied carbon per server unit
    # eol_co2e_kg: end-of-life treatment carbon per server unit
    {
        "hardware_family": "Intel Xeon Cascade Lake",
        "manufacturer": "Intel",
        "mfg_co2e_kg": 1200.0,   # Boavizta: ~1200 kgCO2eq for dual-socket 2U server
        "lifespan_hours": 35040,
        "eol_co2e_kg": 45.0,
        "source": "boavizta",
    },
    {
        "hardware_family": "Intel Xeon Ice Lake",
        "manufacturer": "Intel",
        "mfg_co2e_kg": 1350.0,   # Higher transistor density → higher mfg carbon
        "lifespan_hours": 35040,
        "eol_co2e_kg": 48.0,
        "source": "boavizta",
    },
    {
        "hardware_family": "Intel Xeon Sapphire Rapids",
        "manufacturer": "Intel",
        "mfg_co2e_kg": 1500.0,   # Intel 7 process, larger die [estimate]
        "lifespan_hours": 35040,
        "eol_co2e_kg": 52.0,
        "source": "estimate",
    },
    {
        "hardware_family": "AMD EPYC Rome",
        "manufacturer": "AMD",
        "mfg_co2e_kg": 1100.0,   # TSMC 7nm process, Boavizta
        "lifespan_hours": 35040,
        "eol_co2e_kg": 42.0,
        "source": "boavizta",
    },
    {
        "hardware_family": "AMD EPYC Milan",
        "manufacturer": "AMD",
        "mfg_co2e_kg": 1250.0,   # TSMC 7nm+, higher core count [estimate]
        "lifespan_hours": 35040,
        "eol_co2e_kg": 45.0,
        "source": "estimate",
    },
    {
        "hardware_family": "AMD EPYC Genoa",
        "manufacturer": "AMD",
        "mfg_co2e_kg": 1400.0,   # TSMC 5nm, 96-core [estimate]
        "lifespan_hours": 35040,
        "eol_co2e_kg": 50.0,
        "source": "estimate",
    },
    {
        "hardware_family": "AWS Graviton2",
        "manufacturer": "AWS (TSMC)",
        "mfg_co2e_kg": 900.0,    # ARM architecture, lower power, Boavizta estimate
        "lifespan_hours": 35040,
        "eol_co2e_kg": 35.0,
        "source": "boavizta",
    },
    {
        "hardware_family": "AWS Graviton3",
        "manufacturer": "AWS (TSMC)",
        "mfg_co2e_kg": 1050.0,   # TSMC 5nm, higher core count [estimate]
        "lifespan_hours": 35040,
        "eol_co2e_kg": 38.0,
        "source": "estimate",
    },
    {
        "hardware_family": "Google Axion",
        "manufacturer": "Google (TSMC)",
        "mfg_co2e_kg": 1000.0,   # ARM Neoverse V2, TSMC 5nm [estimate]
        "lifespan_hours": 35040,
        "eol_co2e_kg": 38.0,
        "source": "estimate",
    },
]

INSTANCE_HARDWARE_MAP = [
    # AWS EC2 instance families
    # t3 — Intel Xeon Cascade Lake (Skylake/Cascade Lake depending on generation)
    {"instance_type": "t3.nano",     "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 0.5,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.micro",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 1.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.small",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 2.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.medium",   "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 4.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.large",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.xlarge",   "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "t3.2xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    # m5 — Intel Xeon Cascade Lake
    {"instance_type": "m5.large",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "m5.xlarge",   "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "m5.2xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "m5.4xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "m5.8xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 32, "ram_gb": 128.0,"total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    # m6i — Intel Xeon Ice Lake
    {"instance_type": "m6i.large",   "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "m6i.xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "m6i.2xlarge", "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "m6i.4xlarge", "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    # m6g — AWS Graviton2
    {"instance_type": "m6g.large",   "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    {"instance_type": "m6g.xlarge",  "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    {"instance_type": "m6g.2xlarge", "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    {"instance_type": "m6g.4xlarge", "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    # c5 — Intel Xeon Cascade Lake
    {"instance_type": "c5.large",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 4.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "c5.xlarge",   "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 8.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "c5.2xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    {"instance_type": "c5.4xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 16, "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 192.0},
    # c6i — Intel Xeon Ice Lake
    {"instance_type": "c6i.large",   "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 2,  "ram_gb": 4.0,  "total_vcpu_on_host": 128, "total_ram_gb_on_host": 256.0},
    {"instance_type": "c6i.xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 4,  "ram_gb": 8.0,  "total_vcpu_on_host": 128, "total_ram_gb_on_host": 256.0},
    {"instance_type": "c6i.2xlarge", "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 8,  "ram_gb": 16.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 256.0},
    # c6g — AWS Graviton2
    {"instance_type": "c6g.large",   "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 2,  "ram_gb": 4.0,  "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 128.0},
    {"instance_type": "c6g.xlarge",  "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 4,  "ram_gb": 8.0,  "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 128.0},
    {"instance_type": "c6g.2xlarge", "provider": "aws", "hardware_family": "AWS Graviton2", "vcpu_count": 8,  "ram_gb": 16.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 128.0},
    # r5 — Intel Xeon Cascade Lake (memory-optimized)
    {"instance_type": "r5.large",    "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "r5.xlarge",   "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "r5.2xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 64.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "r5.4xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 16, "ram_gb": 128.0,"total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    # r6i — Intel Xeon Ice Lake
    {"instance_type": "r6i.large",   "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 2,  "ram_gb": 16.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 1024.0},
    {"instance_type": "r6i.xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 4,  "ram_gb": 32.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 1024.0},
    {"instance_type": "r6i.2xlarge", "provider": "aws", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 8,  "ram_gb": 64.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 1024.0},
    # p3 — NVIDIA V100 (Intel Xeon Broadwell host)
    {"instance_type": "p3.2xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 61.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 488.0},
    {"instance_type": "p3.8xlarge",  "provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 32, "ram_gb": 244.0,"total_vcpu_on_host": 64,  "total_ram_gb_on_host": 488.0},
    # p4 — NVIDIA A100 (Intel Xeon Cascade Lake host)
    {"instance_type": "p4d.24xlarge","provider": "aws", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 96, "ram_gb": 1152.0,"total_vcpu_on_host": 96, "total_ram_gb_on_host": 1152.0},
    # Azure VM families
    # Standard_D — Intel Xeon Cascade Lake / Ice Lake
    {"instance_type": "Standard_D2s_v3",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 288.0},
    {"instance_type": "Standard_D4s_v3",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 288.0},
    {"instance_type": "Standard_D8s_v3",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 288.0},
    {"instance_type": "Standard_D16s_v3", "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 288.0},
    {"instance_type": "Standard_D2s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "Standard_D4s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    {"instance_type": "Standard_D8s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 384.0},
    # Standard_E — memory-optimized
    {"instance_type": "Standard_E2s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 2,  "ram_gb": 16.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "Standard_E4s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 4,  "ram_gb": 32.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "Standard_E8s_v5",  "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 8,  "ram_gb": 64.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    {"instance_type": "Standard_E16s_v5", "provider": "azure", "hardware_family": "Intel Xeon Ice Lake",     "vcpu_count": 16, "ram_gb": 128.0,"total_vcpu_on_host": 96,  "total_ram_gb_on_host": 768.0},
    # Standard_F — compute-optimized
    {"instance_type": "Standard_F2s_v2",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 4.0,  "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 144.0},
    {"instance_type": "Standard_F4s_v2",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 8.0,  "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 144.0},
    {"instance_type": "Standard_F8s_v2",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 16.0, "total_vcpu_on_host": 72,  "total_ram_gb_on_host": 144.0},
    # Standard_NC — GPU (NVIDIA V100 / A100)
    {"instance_type": "Standard_NC6s_v3",  "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 6,  "ram_gb": 112.0,"total_vcpu_on_host": 24,  "total_ram_gb_on_host": 448.0},
    {"instance_type": "Standard_NC12s_v3", "provider": "azure", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 12, "ram_gb": 224.0,"total_vcpu_on_host": 24,  "total_ram_gb_on_host": 448.0},
    # GCP machine types
    # n1 — Intel Xeon Cascade Lake / Skylake
    {"instance_type": "n1-standard-1",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 1,  "ram_gb": 3.75, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 360.0},
    {"instance_type": "n1-standard-2",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 7.5,  "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 360.0},
    {"instance_type": "n1-standard-4",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 15.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 360.0},
    {"instance_type": "n1-standard-8",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 30.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 360.0},
    # n2 — Intel Xeon Ice Lake
    {"instance_type": "n2-standard-2",  "provider": "gcp", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "n2-standard-4",  "provider": "gcp", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "n2-standard-8",  "provider": "gcp", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    {"instance_type": "n2-standard-16", "provider": "gcp", "hardware_family": "Intel Xeon Ice Lake", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 128, "total_ram_gb_on_host": 512.0},
    # n2d — AMD EPYC Milan
    {"instance_type": "n2d-standard-2",  "provider": "gcp", "hardware_family": "AMD EPYC Milan", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 224, "total_ram_gb_on_host": 896.0},
    {"instance_type": "n2d-standard-4",  "provider": "gcp", "hardware_family": "AMD EPYC Milan", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 224, "total_ram_gb_on_host": 896.0},
    {"instance_type": "n2d-standard-8",  "provider": "gcp", "hardware_family": "AMD EPYC Milan", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 224, "total_ram_gb_on_host": 896.0},
    {"instance_type": "n2d-standard-16", "provider": "gcp", "hardware_family": "AMD EPYC Milan", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 224, "total_ram_gb_on_host": 896.0},
    # c2 — Intel Xeon Cascade Lake (compute-optimized)
    {"instance_type": "c2-standard-4",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 60,  "total_ram_gb_on_host": 240.0},
    {"instance_type": "c2-standard-8",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 60,  "total_ram_gb_on_host": 240.0},
    {"instance_type": "c2-standard-16", "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 16, "ram_gb": 64.0, "total_vcpu_on_host": 60,  "total_ram_gb_on_host": 240.0},
    # c3 — Intel Xeon Sapphire Rapids
    {"instance_type": "c3-standard-4",  "provider": "gcp", "hardware_family": "Intel Xeon Sapphire Rapids", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 176, "total_ram_gb_on_host": 704.0},
    {"instance_type": "c3-standard-8",  "provider": "gcp", "hardware_family": "Intel Xeon Sapphire Rapids", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 176, "total_ram_gb_on_host": 704.0},
    {"instance_type": "c3-standard-22", "provider": "gcp", "hardware_family": "Intel Xeon Sapphire Rapids", "vcpu_count": 22, "ram_gb": 88.0, "total_vcpu_on_host": 176, "total_ram_gb_on_host": 704.0},
    # e2 — Intel Xeon Cascade Lake / AMD EPYC Rome (shared-core)
    {"instance_type": "e2-standard-2",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 2,  "ram_gb": 8.0,  "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    {"instance_type": "e2-standard-4",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 4,  "ram_gb": 16.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    {"instance_type": "e2-standard-8",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 8,  "ram_gb": 32.0, "total_vcpu_on_host": 64,  "total_ram_gb_on_host": 256.0},
    # a2 — NVIDIA A100 (Intel Xeon Cascade Lake host)
    {"instance_type": "a2-highgpu-1g",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 12, "ram_gb": 85.0, "total_vcpu_on_host": 96,  "total_ram_gb_on_host": 680.0},
    {"instance_type": "a2-highgpu-2g",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 24, "ram_gb": 170.0,"total_vcpu_on_host": 96,  "total_ram_gb_on_host": 680.0},
    {"instance_type": "a2-highgpu-4g",  "provider": "gcp", "hardware_family": "Intel Xeon Cascade Lake", "vcpu_count": 48, "ram_gb": 340.0,"total_vcpu_on_host": 96,  "total_ram_gb_on_host": 680.0},
]


# ---------------------------------------------------------------------------
# Seed runner
# ---------------------------------------------------------------------------

async def seed(db: AsyncSession) -> None:
    """Seed all reference data tables. Idempotent (uses INSERT ... ON CONFLICT DO UPDATE)."""
    now = datetime.now(tz=timezone.utc)

    logger.info("Seeding region_carbon_intensity (%d rows)...", len(REGION_CARBON_INTENSITY))
    for row in REGION_CARBON_INTENSITY:
        await db.execute(
            text("""
                INSERT INTO region_carbon_intensity
                    (region_id, provider, region_name, grid_zone,
                     carbon_intensity_gco2_kwh, carbon_intensity_market_gco2_kwh,
                     renewable_pct, last_updated)
                VALUES
                    (:region_id, :provider, :region_name, :grid_zone,
                     :carbon_intensity_gco2_kwh, :carbon_intensity_market_gco2_kwh,
                     :renewable_pct, :last_updated)
                ON CONFLICT (region_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    region_name = EXCLUDED.region_name,
                    grid_zone = EXCLUDED.grid_zone,
                    carbon_intensity_gco2_kwh = EXCLUDED.carbon_intensity_gco2_kwh,
                    carbon_intensity_market_gco2_kwh = EXCLUDED.carbon_intensity_market_gco2_kwh,
                    renewable_pct = EXCLUDED.renewable_pct,
                    last_updated = EXCLUDED.last_updated
            """),
            {**row, "last_updated": now},
        )

    logger.info("Seeding region_water (%d rows)...", len(REGION_WATER))
    for row in REGION_WATER:
        await db.execute(
            text("""
                INSERT INTO region_water
                    (region_id, provider, wue_litres_per_kwh, cooling_type,
                     water_data_source, wri_aqueduct_score, latitude, longitude, last_updated)
                VALUES
                    (:region_id, :provider, :wue_litres_per_kwh, :cooling_type,
                     :water_data_source, :wri_aqueduct_score, :latitude, :longitude, :last_updated)
                ON CONFLICT (region_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    wue_litres_per_kwh = EXCLUDED.wue_litres_per_kwh,
                    cooling_type = EXCLUDED.cooling_type,
                    water_data_source = EXCLUDED.water_data_source,
                    wri_aqueduct_score = EXCLUDED.wri_aqueduct_score,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    last_updated = EXCLUDED.last_updated
            """),
            {**row, "last_updated": now},
        )

    logger.info("Seeding hardware_carbon_coefficients (%d rows)...", len(HARDWARE_CARBON_COEFFICIENTS))
    for row in HARDWARE_CARBON_COEFFICIENTS:
        await db.execute(
            text("""
                INSERT INTO hardware_carbon_coefficients
                    (hardware_family, manufacturer, mfg_co2e_kg, lifespan_hours,
                     eol_co2e_kg, source, last_updated)
                VALUES
                    (:hardware_family, :manufacturer, :mfg_co2e_kg, :lifespan_hours,
                     :eol_co2e_kg, :source, :last_updated)
                ON CONFLICT (hardware_family) DO UPDATE SET
                    manufacturer = EXCLUDED.manufacturer,
                    mfg_co2e_kg = EXCLUDED.mfg_co2e_kg,
                    lifespan_hours = EXCLUDED.lifespan_hours,
                    eol_co2e_kg = EXCLUDED.eol_co2e_kg,
                    source = EXCLUDED.source,
                    last_updated = EXCLUDED.last_updated
            """),
            {**row, "last_updated": now},
        )

    logger.info("Seeding instance_hardware_map (%d rows)...", len(INSTANCE_HARDWARE_MAP))
    for row in INSTANCE_HARDWARE_MAP:
        await db.execute(
            text("""
                INSERT INTO instance_hardware_map
                    (instance_type, provider, hardware_family, vcpu_count,
                     ram_gb, total_vcpu_on_host, total_ram_gb_on_host)
                VALUES
                    (:instance_type, :provider, :hardware_family, :vcpu_count,
                     :ram_gb, :total_vcpu_on_host, :total_ram_gb_on_host)
                ON CONFLICT (instance_type, provider) DO UPDATE SET
                    hardware_family = EXCLUDED.hardware_family,
                    vcpu_count = EXCLUDED.vcpu_count,
                    ram_gb = EXCLUDED.ram_gb,
                    total_vcpu_on_host = EXCLUDED.total_vcpu_on_host,
                    total_ram_gb_on_host = EXCLUDED.total_ram_gb_on_host
            """),
            row,
        )

    await db.commit()
    logger.info("Reference data seeding complete.")


async def main() -> None:
    """Entry point for running seed directly: python -m src.seeds.reference_data"""
    import os
    logging.basicConfig(level=logging.INFO)

    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://cloudcarbon:cloudcarbon@localhost:5432/cloudcarbon",
    )
    # Ensure asyncpg driver
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
