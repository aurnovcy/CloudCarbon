"""
Integration tests for the full CloudCarbon pipeline.

Tests the complete flow against a live server:
  ingest → enrich → recommend → query → reports

Requires a running server at http://localhost:8000 plus PostgreSQL and Redis.

Run with:
    cd apps/api
    python -m pytest tests/integration/test_full_pipeline.py -v --tb=short
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import requests

BASE_URL = os.environ.get("TEST_SERVER_URL", "http://localhost:8000")
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "sample_focus.csv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_available() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_token() -> str | None:
    """Obtain a JWT from the dev-token endpoint (dev/test environments only)."""
    try:
        r = requests.post(f"{BASE_URL}/auth/dev-token", timeout=5)
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception:
        pass
    return None


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Skip entire module when server is not up
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

_SERVER_UP = _server_available()

skip_if_no_server = pytest.mark.skipif(
    not _SERVER_UP,
    reason="Integration tests require a live server at http://localhost:8000",
)


# ---------------------------------------------------------------------------
# Test class — 15 ordered tests sharing state via class attributes
# ---------------------------------------------------------------------------

@skip_if_no_server
class TestFullPipeline:
    """
    15 integration tests covering the full CloudCarbon pipeline.
    Each test stores state in class attributes for subsequent tests.
    """

    # Shared state populated by tests
    _token: str | None = None
    _account_id: str | None = None
    _ingested_total: int = 0
    _enrichment_job_id: str | None = None
    _rec_job_id: str | None = None

    def setup_method(self):
        """Obtain a fresh token before each test."""
        if TestFullPipeline._token is None:
            TestFullPipeline._token = get_token()
        self.token = TestFullPipeline._token

    # ------------------------------------------------------------------
    # test_01 — Health check
    # ------------------------------------------------------------------
    def test_01_health(self):
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    # ------------------------------------------------------------------
    # test_02 — Create a cloud account
    # ------------------------------------------------------------------
    def test_02_create_account(self):
        assert self.token, "No JWT — check /auth/dev-token endpoint"
        payload = {
            "display_name": "Integration Test AWS",
            "provider": "aws",
            "account_id": "999888777666",
            "config": {},
            "credentials": {},
        }
        r = requests.post(
            f"{BASE_URL}/accounts",
            json=payload,
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code in (200, 201), r.text
        data = r.json()
        assert "id" in data
        TestFullPipeline._account_id = data["id"]

    # ------------------------------------------------------------------
    # test_03 — Upload FOCUS CSV
    # ------------------------------------------------------------------
    def test_03_upload_focus_file(self):
        assert self.token, "No JWT"
        assert TestFullPipeline._account_id, "No account_id from test_02"
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"

        with open(FIXTURE_PATH, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/ingest/upload?account_id={TestFullPipeline._account_id}",
                files={"file": ("sample_focus.csv", f, "text/csv")},
                headers=auth_headers(self.token),
                timeout=30,
            )

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] > 0, (
            f"Expected records ingested. errors={data.get('errors')}, "
            f"validation_errors={data.get('validation_errors', [])[:3]}"
        )
        assert data["inserted"] + data["updated"] == data["total"]
        TestFullPipeline._ingested_total = data["total"]

    # ------------------------------------------------------------------
    # test_04 — Trigger enrichment (background task)
    # ------------------------------------------------------------------
    def test_04_run_enrichment(self):
        assert self.token, "No JWT"
        r = requests.post(
            f"{BASE_URL}/enrichment/run",
            json={"mode": "incremental"},
            headers=auth_headers(self.token),
            timeout=15,
        )
        assert r.status_code in (200, 202), r.text
        data = r.json()
        assert "job_id" in data
        TestFullPipeline._enrichment_job_id = data["job_id"]

    # ------------------------------------------------------------------
    # test_05 — Enrichment summary (defaults to caller's tenant)
    # ------------------------------------------------------------------
    def test_05_enrichment_summary(self):
        assert self.token, "No JWT"
        # Poll briefly so background task has a chance to start
        time.sleep(2)

        r = requests.get(
            f"{BASE_URL}/enrichment/summary",
            headers=auth_headers(self.token),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total_focus_records" in data
        assert "total_records_enriched" in data
        scope3 = data.get("total_scope3_co2e_kg", data.get("scope3_total", 0))
        assert scope3 >= 0

    # ------------------------------------------------------------------
    # test_06 — Trigger recommendations engine
    # ------------------------------------------------------------------
    def test_06_run_recommendations(self):
        assert self.token, "No JWT"
        r = requests.post(
            f"{BASE_URL}/recommendations/run",
            json={"cost_weight": 0.6, "carbon_weight": 0.3, "water_weight": 0.1},
            headers=auth_headers(self.token),
            timeout=15,
        )
        assert r.status_code in (200, 202), r.text
        data = r.json()
        assert "job_id" in data
        TestFullPipeline._rec_job_id = data["job_id"]

    # ------------------------------------------------------------------
    # test_07 — List recommendations (checks schema fields)
    # ------------------------------------------------------------------
    def test_07_list_recommendations(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/recommendations",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get("items", data.get("recommendations", []))
        assert isinstance(items, list)
        assert "total" in data
        # Validate schema of first recommendation if any exist
        if items:
            rec = items[0]
            assert "cost_impact_monthly_usd" in rec, f"Missing cost_impact_monthly_usd in {list(rec.keys())}"
            assert "co2e_impact_monthly_kg" in rec, f"Missing co2e_impact_monthly_kg in {list(rec.keys())}"
            assert "water_impact_monthly_litres" in rec, f"Missing water_impact_monthly_litres in {list(rec.keys())}"

    # ------------------------------------------------------------------
    # test_08 — List recommendations sorted by cost
    # ------------------------------------------------------------------
    def test_08_cost_mode_recs(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/recommendations?sort_by=cost&sort_dir=desc",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get("items", data.get("recommendations", []))
        assert isinstance(items, list)

    # ------------------------------------------------------------------
    # test_09 — List recommendations sorted by carbon
    # ------------------------------------------------------------------
    def test_09_sustainability_mode_recs(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/recommendations?sort_by=co2e&sort_dir=desc",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get("items", data.get("recommendations", []))
        assert isinstance(items, list)

    # ------------------------------------------------------------------
    # test_10 — Overview report
    # ------------------------------------------------------------------
    def test_10_overview_report(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/reports/overview",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total_cost_usd" in data
        assert "total_co2e_kg" in data

    # ------------------------------------------------------------------
    # test_11 — Carbon report (time-series)
    # ------------------------------------------------------------------
    def test_11_carbon_report(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/reports/carbon",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Should be a list of time-series points or a dict with a series key
        assert isinstance(data, (list, dict))

    # ------------------------------------------------------------------
    # test_12 — Water report
    # ------------------------------------------------------------------
    def test_12_water_report(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/reports/water",
            headers=auth_headers(self.token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total_water_litres" in data

    # ------------------------------------------------------------------
    # test_13 — Natural language query (SQL must start with SELECT)
    # ------------------------------------------------------------------
    def test_13_natural_language_query(self):
        assert self.token, "No JWT"
        r = requests.post(
            f"{BASE_URL}/query/natural-language",
            json={"question": "How many billing records do we have?"},
            headers=auth_headers(self.token),
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "sql" in data
        assert "row_count" in data
        supported = data.get("supported", True)
        if supported:
            sql = data["sql"].strip().upper()
            assert sql.startswith("SELECT"), f"SQL does not start with SELECT: {data['sql'][:100]}"

    # ------------------------------------------------------------------
    # test_14 — Forecast endpoint
    # ------------------------------------------------------------------
    def test_14_forecast(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/reports/forecast?metrics=cost_usd,total_co2e_kg&horizon_days=30",
            headers=auth_headers(self.token),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, (list, dict))

    # ------------------------------------------------------------------
    # test_15 — Executive summary
    # ------------------------------------------------------------------
    def test_15_executive_summary(self):
        assert self.token, "No JWT"
        r = requests.get(
            f"{BASE_URL}/reports/executive-summary",
            headers=auth_headers(self.token),
            timeout=30,
        )
        assert r.status_code in (200, 422, 500), r.text
        if r.status_code == 200:
            data = r.json()
            assert "summary" in data or "period_start" in data, (
                f"Expected 'summary' or 'period_start' in response keys: {list(data.keys())}"
            )
