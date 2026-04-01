"""
pytest configuration and shared fixtures for CloudCarbon API tests.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture file paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def aws_cur_csv() -> Path:
    """Path to the AWS CUR sample CSV fixture."""
    return FIXTURES_DIR / "aws_cur_sample.csv"


@pytest.fixture
def azure_cost_csv() -> Path:
    """Path to the Azure Cost Management sample CSV fixture."""
    return FIXTURES_DIR / "azure_cost_sample.csv"


@pytest.fixture
def gcp_billing_json() -> Path:
    """Path to the GCP BigQuery billing export sample JSON fixture."""
    return FIXTURES_DIR / "gcp_billing_sample.json"


@pytest.fixture
def alibaba_billing_json() -> Path:
    """Path to the Alibaba Cloud billing sample JSON fixture."""
    return FIXTURES_DIR / "alibaba_billing_sample.json"


@pytest.fixture
def focus_csv() -> Path:
    """Path to the FOCUS 1.0 passthrough sample CSV fixture."""
    return FIXTURES_DIR / "focus_sample.csv"
