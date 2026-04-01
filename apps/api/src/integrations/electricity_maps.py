"""
Electricity Maps API integration.

Provides live carbon intensity data (gCO2eq/kWh) for cloud regions via the
Electricity Maps API (https://api.electricitymap.org/v3/).

Features:
  - Live carbon intensity lookup with 1-hour Redis cache
  - Graceful fallback to static region_carbon_intensity table values
  - Background refresh function for the agents scheduler (every 6 hours)
  - No-op when ELECTRICITY_MAPS_API_KEY is not configured

API reference: https://static.electricitymaps.com/api/docs/index.html
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ELECTRICITY_MAPS_API_BASE = "https://api.electricitymap.org/v3"
CACHE_TTL_SECONDS = 3600  # 1 hour
CACHE_KEY_PREFIX = "em:ci:"  # Redis key prefix: em:ci:{zone}


def _get_api_key() -> str | None:
    """Return the Electricity Maps API key from environment, or None if not set."""
    return os.getenv("ELECTRICITY_MAPS_API_KEY") or None


def _cache_key(zone: str) -> str:
    return f"{CACHE_KEY_PREFIX}{zone}"


# ---------------------------------------------------------------------------
# Live carbon intensity lookup
# ---------------------------------------------------------------------------

async def get_live_carbon_intensity(
    zone: str,
    api_key: str | None = None,
    redis_client: Any = None,
) -> float | None:
    """
    Fetch the latest carbon intensity (gCO2eq/kWh) for an Electricity Maps zone.

    Results are cached in Redis with a 1-hour TTL. Returns None if:
      - The API key is not configured
      - The API call fails
      - The zone is not found

    Args:
        zone: Electricity Maps zone code (e.g. 'US-MIDA-PJM', 'DE', 'FR').
        api_key: Electricity Maps API key. Reads ELECTRICITY_MAPS_API_KEY env var if None.
        redis_client: Optional redis.asyncio.Redis client. If None, caching is skipped.

    Returns:
        Carbon intensity in gCO2eq/kWh, or None if unavailable.
    """
    key = _get_api_key() if api_key is None else api_key
    if not key:
        logger.debug("ELECTRICITY_MAPS_API_KEY not set; skipping live lookup for zone %s", zone)
        return None

    # Check Redis cache first
    if redis_client is not None:
        try:
            cached = await redis_client.get(_cache_key(zone))
            if cached is not None:
                value = float(cached)
                logger.debug("Cache hit for zone %s: %.1f gCO2/kWh", zone, value)
                return value
        except Exception as exc:
            logger.warning("Redis cache read failed for zone %s: %s", zone, exc)

    # Fetch from Electricity Maps API
    url = f"{ELECTRICITY_MAPS_API_BASE}/carbon-intensity/latest"
    params = {"zone": zone}
    headers = {"auth-token": key}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            intensity = float(data["carbonIntensity"])

            # Store in Redis cache
            if redis_client is not None:
                try:
                    await redis_client.setex(
                        _cache_key(zone),
                        CACHE_TTL_SECONDS,
                        str(intensity),
                    )
                except Exception as exc:
                    logger.warning("Redis cache write failed for zone %s: %s", zone, exc)

            logger.info("Live carbon intensity for zone %s: %.1f gCO2/kWh", zone, intensity)
            return intensity

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.warning("Zone %s not found in Electricity Maps", zone)
        elif exc.response.status_code == 429:
            logger.warning("Electricity Maps rate limit hit for zone %s", zone)
        else:
            logger.warning(
                "Electricity Maps API error for zone %s: %s %s",
                zone, exc.response.status_code, exc.response.text[:200],
            )
        return None
    except httpx.TimeoutException:
        logger.warning("Electricity Maps API timeout for zone %s", zone)
        return None
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Unexpected Electricity Maps response for zone %s: %s", zone, exc)
        return None
    except Exception as exc:
        logger.warning("Electricity Maps lookup failed for zone %s: %s", zone, exc)
        return None


# ---------------------------------------------------------------------------
# Background refresh (called by agents scheduler every 6 hours)
# ---------------------------------------------------------------------------

async def refresh_region_carbon_intensity(
    db_session: Any,
    redis_client: Any = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Refresh carbon intensity values for all regions that have a grid_zone.

    Iterates over region_carbon_intensity rows with a non-null grid_zone,
    calls get_live_carbon_intensity() for each, and updates the table if
    a live value is available.

    Args:
        db_session: SQLAlchemy AsyncSession.
        redis_client: Optional redis.asyncio.Redis client for caching.
        api_key: Electricity Maps API key (reads env var if None).

    Returns:
        Dict with updated_count, skipped_count, error_count.
    """
    from sqlalchemy import text

    key = _get_api_key() if api_key is None else api_key
    if not key:
        logger.info("ELECTRICITY_MAPS_API_KEY not set; skipping region carbon intensity refresh")
        return {"updated_count": 0, "skipped_count": 0, "error_count": 0, "reason": "no_api_key"}

    # Fetch all regions with a grid_zone
    result = await db_session.execute(
        text("SELECT region_id, grid_zone FROM region_carbon_intensity WHERE grid_zone IS NOT NULL")
    )
    rows = result.fetchall()

    updated = 0
    skipped = 0
    errors = 0

    for region_id, grid_zone in rows:
        try:
            live_intensity = await get_live_carbon_intensity(
                zone=grid_zone,
                api_key=key,
                redis_client=redis_client,
            )

            if live_intensity is not None:
                await db_session.execute(
                    text("""
                        UPDATE region_carbon_intensity
                        SET carbon_intensity_gco2_kwh = :intensity,
                            last_updated = :now
                        WHERE region_id = :rid
                    """),
                    {
                        "intensity": live_intensity,
                        "now": datetime.now(tz=timezone.utc),
                        "rid": region_id,
                    },
                )
                updated += 1
                logger.debug("Updated %s (%s): %.1f gCO2/kWh", region_id, grid_zone, live_intensity)
            else:
                skipped += 1

        except Exception as exc:
            errors += 1
            logger.warning("Failed to refresh %s (%s): %s", region_id, grid_zone, exc)

    if updated > 0:
        await db_session.commit()

    logger.info(
        "Carbon intensity refresh complete: %d updated, %d skipped, %d errors",
        updated, skipped, errors,
    )
    return {"updated_count": updated, "skipped_count": skipped, "error_count": errors}


# ---------------------------------------------------------------------------
# Zone mapping helper
# ---------------------------------------------------------------------------

# Mapping from cloud region IDs to Electricity Maps zone codes
# This covers the most common regions; extend as needed
REGION_TO_ZONE: dict[str, str] = {
    # AWS
    "us-east-1": "US-MIDA-PJM",
    "us-east-2": "US-MIDW-MISO",
    "us-west-1": "US-CAL-CISO",
    "us-west-2": "US-NW-PACW",
    "eu-west-1": "IE",
    "eu-west-2": "GB",
    "eu-west-3": "FR",
    "eu-central-1": "DE",
    "eu-north-1": "SE",
    "ap-southeast-1": "SG",
    "ap-southeast-2": "AU-NSW",
    "ap-northeast-1": "JP-TK",
    "ap-northeast-2": "KR",
    "ap-south-1": "IN-WE",
    "ca-central-1": "CA-ON",
    "sa-east-1": "BR-CS",
    # Azure
    "eastus": "US-MIDA-PJM",
    "eastus2": "US-MIDA-PJM",
    "westus": "US-CAL-CISO",
    "westus2": "US-NW-PACW",
    "westus3": "US-SW-AZPS",
    "northeurope": "IE",
    "westeurope": "NL",
    "uksouth": "GB",
    "ukwest": "GB",
    "francecentral": "FR",
    "germanywestcentral": "DE",
    "swedencentral": "SE",
    "norwayeast": "NO",
    "eastasia": "HK",
    "southeastasia": "SG",
    "japaneast": "JP-TK",
    "koreacentral": "KR",
    "australiaeast": "AU-NSW",
    "centralindia": "IN-WE",
    "canadacentral": "CA-ON",
    "brazilsouth": "BR-CS",
    # GCP
    "us-central1": "US-MIDW-MISO",
    "us-east1": "US-SE-SERC",
    "us-east4": "US-MIDA-PJM",
    "us-west1": "US-NW-PACW",
    "us-west2": "US-CAL-CISO",
    "us-west3": "US-SW-AZPS",
    "us-west4": "US-SW-NEVP",
    "europe-west1": "BE",
    "europe-west2": "GB",
    "europe-west3": "DE",
    "europe-west4": "NL",
    "europe-west6": "CH",
    "europe-north1": "FI",
    "asia-east1": "TW",
    "asia-east2": "HK",
    "asia-northeast1": "JP-TK",
    "asia-northeast2": "JP-KY",
    "asia-northeast3": "KR",
    "asia-southeast1": "SG",
    "asia-southeast2": "ID-JW",
    "asia-south1": "IN-WE",
    "australia-southeast1": "AU-NSW",
    "southamerica-east1": "BR-CS",
    "northamerica-northeast1": "CA-QC",
}


def get_zone_for_region(region_id: str) -> str | None:
    """Return the Electricity Maps zone code for a cloud region ID, or None."""
    return REGION_TO_ZONE.get(region_id)
