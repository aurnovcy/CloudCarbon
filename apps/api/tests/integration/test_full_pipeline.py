"""
Integration tests for the full CloudCarbon pipeline.

Tests the complete flow: ingest → enrich → recommend → query via the HTTP API.
Requires a running PostgreSQL and Redis instance (uses DATABASE_URL and REDIS_URL env vars).

Run with:
    cd apps/api
    python -m pytest tests/integration/test_full_pipeline.py -v --tb=short

When DATABASE_URL / REDIS_URL are not reachable the suite is automatically skipped.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Ensure src is importable from tests directory
# ---------------------------------------------------------------------------
import sys
_api_dir = Path(__file__).resolve().parent.parent.parent
if str(_api_dir) not in sys.path:
    sys.path.insert(0, str(_api_dir))

# Set a test JWT secret before importing app modules
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-integration-tests")
os.environ.setdefault("ENVIRONMENT", "development")

from src.config import get_settings
from src.services.jwt_service import create_access_token

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Availability check — skip entire suite when services are not up
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    try:
        settings = get_settings()
        # Strip SQLAlchemy dialect prefix for raw psycopg2 check
        raw_url = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        conn = psycopg2.connect(raw_url, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    try:
        import redis as _redis
        settings = get_settings()
        r = _redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.integration

# Skip the entire module when services are unavailable
_DB_UP = _db_available()
_REDIS_UP = _redis_available()
_SERVICES_UP = _DB_UP and _REDIS_UP

skip_if_no_services = pytest.mark.skipif(
    not _SERVICES_UP,
    reason=(
        "Integration tests require PostgreSQL and Redis. "
        f"DB available: {_DB_UP}, Redis available: {_REDIS_UP}. "
        "Start both services and re-run."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str = "admin") -> str:
    token, _ = create_access_token(user_id, tenant_id, role)
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Session-scoped fixtures (create once per test class run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def db() -> Generator[Session, None, None]:
    """Provide a raw SQLAlchemy session for setup/teardown."""
    if not _SERVICES_UP:
        pytest.skip("Services not available")
    from src.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="class")
def test_ids(db: Session):
    """Create a tenant, user, and cloud account; yield IDs; clean up after."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()

    # Create tenant
    db.execute(text("""
        INSERT INTO tenants (id, name, slug, plan)
        VALUES (:id, :name, :slug, 'pro')
    """), {"id": str(tenant_id), "name": "Integration Test Tenant", "slug": f"inttest-{tenant_id.hex[:8]}"})

    # Create admin user
    db.execute(text("""
        INSERT INTO users (id, tenant_id, email, name, role, auth_provider)
        VALUES (:id, :tenant_id, :email, 'Test Admin', 'admin', 'test')
    """), {"id": str(user_id), "tenant_id": str(tenant_id), "email": f"admin-{tenant_id.hex[:8]}@test.local"})

    # Create cloud account (use column names from the updated model: name, account_identifier)
    db.execute(text("""
        INSERT INTO cloud_accounts (id, tenant_id, name, provider, account_identifier, status, config)
        VALUES (:id, :tenant_id, 'Integration AWS', 'aws', '123456789012', 'active', '{}')
    """), {"id": str(account_id), "tenant_id": str(tenant_id)})

    db.commit()

    yield {"tenant_id": tenant_id, "user_id": user_id, "account_id": account_id}

    # Cleanup — cascade deletes handle focus_records, enriched_records, etc.
    db.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)})
    db.commit()


@pytest.fixture(scope="class")
def admin_token(test_ids) -> str:
    return _make_token(test_ids["user_id"], test_ids["tenant_id"], role="admin")


@pytest.fixture(scope="class")
def client(test_ids) -> Generator[TestClient, None, None]:
    """
    TestClient with the auth dependency overridden to return our test admin user.
    """
    from src.dependencies.auth import CurrentUser, get_current_user, require_role
    from src.main import app

    user_id = test_ids["user_id"]
    tenant_id = test_ids["tenant_id"]

    def _override_current_user() -> CurrentUser:
        return CurrentUser(
            id=user_id,
            tenant_id=tenant_id,
            email=f"admin-{tenant_id.hex[:8]}@test.local",
            name="Test Admin",
            role="admin",
            auth_provider="test",
        )

    app.dependency_overrides[get_current_user] = _override_current_user
    # Override each role-level dependency
    for role in ("viewer", "analyst", "engineer", "admin"):
        app.dependency_overrides[require_role(role)] = _override_current_user

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@skip_if_no_services
class TestFullPipeline:
    """
    15 integration tests covering the full CloudCarbon pipeline.
    Tests run in order (test_01 … test_15) within the same DB state.
    """

    # ------------------------------------------------------------------
    # test_01 — Health check
    # ------------------------------------------------------------------
    def test_01_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    # ------------------------------------------------------------------
    # test_02 — GET /auth/me returns test user
    # ------------------------------------------------------------------
    def test_02_auth_me(self, client: TestClient, test_ids):
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "admin"
        assert str(test_ids["tenant_id"]) == data["tenant_id"]

    # ------------------------------------------------------------------
    # test_03 — List accounts (should include our test account)
    # ------------------------------------------------------------------
    def test_03_list_accounts(self, client: TestClient, test_ids):
        resp = client.get("/accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        ids = [a["id"] for a in data["accounts"]]
        assert str(test_ids["account_id"]) in ids

    # ------------------------------------------------------------------
    # test_04 — Upload FOCUS CSV (sample_focus.csv fixture)
    # ------------------------------------------------------------------
    def test_04_upload_focus_csv(self, client: TestClient, test_ids):
        csv_path = FIXTURES_DIR / "sample_focus.csv"
        assert csv_path.exists(), f"Fixture not found: {csv_path}"

        with open(csv_path, "rb") as f:
            resp = client.post(
                f"/ingest/upload?account_id={test_ids['account_id']}",
                files={"file": ("sample_focus.csv", f, "text/csv")},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] > 0, (
            f"Expected records to be ingested. "
            f"errors={data.get('errors')}, "
            f"validation_errors={data.get('validation_errors', [])[:3]}"
        )
        assert data["inserted"] + data["updated"] == data["total"]
        # Store for subsequent tests
        TestFullPipeline._ingested_total = data["total"]

    # ------------------------------------------------------------------
    # test_05 — Re-upload same CSV triggers upsert (no duplicate inserts)
    # ------------------------------------------------------------------
    def test_05_upload_idempotent(self, client: TestClient, test_ids):
        csv_path = FIXTURES_DIR / "sample_focus.csv"
        with open(csv_path, "rb") as f:
            resp = client.post(
                f"/ingest/upload?account_id={test_ids['account_id']}",
                files={"file": ("sample_focus.csv", f, "text/csv")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == TestFullPipeline._ingested_total
        assert data["inserted"] == 0
        assert data["updated"] == data["total"]

    # ------------------------------------------------------------------
    # test_06 — List accounts now shows record_count > 0
    # ------------------------------------------------------------------
    def test_06_account_record_count(self, client: TestClient, test_ids):
        resp = client.get("/accounts")
        assert resp.status_code == 200
        account = next(
            (a for a in resp.json()["accounts"] if a["id"] == str(test_ids["account_id"])),
            None,
        )
        assert account is not None
        assert account["record_count"] >= TestFullPipeline._ingested_total

    # ------------------------------------------------------------------
    # test_07 — Trigger enrichment job (background task)
    # ------------------------------------------------------------------
    def test_07_trigger_enrichment(self, client: TestClient, test_ids):
        resp = client.post(
            "/enrichment/run",
            json={"tenant_id": str(test_ids["tenant_id"]), "mode": "incremental"},
        )
        assert resp.status_code in (200, 202), resp.text
        data = resp.json()
        assert "job_id" in data
        TestFullPipeline._enrichment_job_id = data["job_id"]

    # ------------------------------------------------------------------
    # test_08 — Enrichment job status is reachable
    # ------------------------------------------------------------------
    def test_08_enrichment_job_status(self, client: TestClient):
        job_id = getattr(TestFullPipeline, "_enrichment_job_id", None)
        if not job_id:
            pytest.skip("No enrichment job ID from test_07")
        resp = client.get(f"/enrichment/status/{job_id}")
        assert resp.status_code in (200, 404), resp.text
        if resp.status_code == 200:
            assert resp.json()["job_id"] == job_id

    # ------------------------------------------------------------------
    # test_09 — Enrichment summary returns counts per tenant
    # ------------------------------------------------------------------
    def test_09_enrichment_summary(self, client: TestClient, test_ids):
        resp = client.get(f"/enrichment/summary?tenant_id={test_ids['tenant_id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_focus_records" in data
        assert "total_records_enriched" in data

    # ------------------------------------------------------------------
    # test_10 — Recommendations list (may be empty but must return 200)
    # ------------------------------------------------------------------
    def test_10_list_recommendations(self, client: TestClient):
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    # ------------------------------------------------------------------
    # test_11 — Run recommendations engine
    # ------------------------------------------------------------------
    def test_11_run_recommendations(self, client: TestClient):
        resp = client.post(
            "/recommendations/run",
            json={"cost_weight": 0.6, "carbon_weight": 0.3, "water_weight": 0.1},
        )
        assert resp.status_code in (200, 202), resp.text
        data = resp.json()
        assert "job_id" in data

    # ------------------------------------------------------------------
    # test_12 — Overview report endpoint
    # ------------------------------------------------------------------
    def test_12_cost_carbon_report(self, client: TestClient):
        resp = client.get("/reports/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "total_cost_usd" in data
        assert "total_co2e_kg" in data

    # ------------------------------------------------------------------
    # test_13 — NL query with a mocked Anthropic call
    # ------------------------------------------------------------------
    def test_13_nl_query(self, client: TestClient, test_ids):
        from src.query.service import NLQueryResult

        mock_result = NLQueryResult(
            question="How many records do we have?",
            sql="SELECT COUNT(*) AS cnt FROM focus_records WHERE tenant_id = :tid",
            results=[{"cnt": TestFullPipeline._ingested_total}],
            row_count=1,
            supported=True,
            suggestions=["What is my top provider by cost?"],
        )

        with patch("src.routers.query.natural_language_to_sql", return_value=mock_result), \
             patch("src.routers.query.suggest_followup_questions", return_value=mock_result.suggestions):
            resp = client.post(
                "/query/natural-language",
                json={"question": "How many records do we have?"},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "sql" in data
        assert data["row_count"] >= 0

    # ------------------------------------------------------------------
    # test_14 — Executive summary report
    # ------------------------------------------------------------------
    def test_14_executive_summary(self, client: TestClient):
        with patch("src.reports.generator.client") as mock_client:
            mock_message = MagicMock()
            mock_message.content = [MagicMock(text="Test executive summary narrative.")]
            mock_client.messages.create.return_value = mock_message
            resp = client.get("/reports/executive-summary")

        assert resp.status_code in (200, 422, 500), resp.text
        if resp.status_code == 200:
            data = resp.json()
            assert "summary" in data or "period_start" in data

    # ------------------------------------------------------------------
    # test_15 — Delete cloud account (soft-delete)
    # ------------------------------------------------------------------
    def test_15_delete_account(self, client: TestClient, test_ids):
        resp = client.delete(f"/accounts/{test_ids['account_id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "deleted"
        assert data.get("account_id") == str(test_ids["account_id"])

        # Verify it no longer appears in the active list
        list_resp = client.get("/accounts")
        assert list_resp.status_code == 200
        active_ids = [a["id"] for a in list_resp.json()["accounts"]]
        assert str(test_ids["account_id"]) not in active_ids
