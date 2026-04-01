"""
Unit tests for the GCP BigQuery billing export normalizer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion.normalizers.gcp import parse_gcp_json, normalize_gcp_row


class TestGcpNormalizer:
    """Tests for parse_gcp_json() and normalize_gcp_row()."""

    def test_returns_list_of_focus_records(self, gcp_billing_json: Path) -> None:
        records = parse_gcp_json(str(gcp_billing_json))
        assert isinstance(records, list)
        assert len(records) > 0

    def test_compute_engine_record(self, gcp_billing_json: Path) -> None:
        """Compute Engine record should map to Compute service category."""
        records = parse_gcp_json(str(gcp_billing_json))
        compute_records = [r for r in records if "Compute" in (r.ServiceName or "")]
        assert len(compute_records) >= 1
        ce = compute_records[0]
        # GCP normalizer uses 'Google Cloud Platform' as ProviderName
        assert ce.ProviderName in ("Google Cloud", "Google Cloud Platform")
        assert ce.ServiceCategory == "Compute"
        assert ce.EffectiveCost == pytest.approx(0.048, rel=1e-3)

    def test_cloud_storage_record(self, gcp_billing_json: Path) -> None:
        """Cloud Storage record should map to Storage service category."""
        records = parse_gcp_json(str(gcp_billing_json))
        storage_records = [r for r in records if "Storage" in (r.ServiceName or "")]
        assert len(storage_records) >= 1
        storage = storage_records[0]
        assert storage.ServiceCategory == "Storage"
        assert storage.EffectiveCost == pytest.approx(5.12, rel=1e-3)

    def test_credits_applied_to_cost(self, gcp_billing_json: Path) -> None:
        """Cloud SQL record has a sustained use discount credit; effective cost should be net."""
        records = parse_gcp_json(str(gcp_billing_json))
        sql_records = [r for r in records if "SQL" in (r.ServiceName or "")]
        assert len(sql_records) >= 1
        sql = sql_records[0]
        # Gross cost = 0.0413, credit = -0.0124, net = 0.0289
        assert sql.EffectiveCost == pytest.approx(0.0413 - 0.0124, rel=1e-2)

    def test_labels_parsed_as_tags(self, gcp_billing_json: Path) -> None:
        """GCP labels array should be converted to a Tags dict."""
        records = parse_gcp_json(str(gcp_billing_json))
        tagged = [r for r in records if r.Tags]
        assert len(tagged) >= 1
        tags = tagged[0].Tags
        assert isinstance(tags, dict)
        assert "environment" in tags or "team" in tags

    def test_region_extracted(self, gcp_billing_json: Path) -> None:
        """RegionId should be extracted from location.region."""
        records = parse_gcp_json(str(gcp_billing_json))
        for rec in records:
            if rec.RegionId:
                assert isinstance(rec.RegionId, str)

    def test_resource_id_from_resource_name(self, gcp_billing_json: Path) -> None:
        """ResourceId should come from resource.name."""
        records = parse_gcp_json(str(gcp_billing_json))
        compute_records = [r for r in records if r.ResourceId and "instances" in r.ResourceId]
        assert len(compute_records) >= 1

    def test_billing_period_dates(self, gcp_billing_json: Path) -> None:
        """BillingPeriodStart and BillingPeriodEnd should be datetime objects."""
        from datetime import datetime
        records = parse_gcp_json(str(gcp_billing_json))
        for rec in records:
            assert isinstance(rec.BillingPeriodStart, datetime)
            assert isinstance(rec.BillingPeriodEnd, datetime)

    def test_normalize_single_row(self) -> None:
        """normalize_gcp_row() should handle a minimal row dict."""
        row = {
            "billing_account_id": "TEST-ACCOUNT",
            "service": {"id": "6F81-5844-456A", "description": "Compute Engine"},
            "sku": {"id": "0048-21CE-74C0", "description": "N2 Instance Core"},
            "usage_start_time": "2026-03-01T00:00:00Z",
            "usage_end_time": "2026-03-01T01:00:00Z",
            "project": {"id": "test-project", "number": "999", "name": "Test", "labels": {}},
            "labels": [],
            "location": {"location": "us-central1", "country": "US", "region": "us-central1", "zone": None},
            "resource": {"name": "test-instance", "global_name": "//compute.googleapis.com/test-instance"},
            "cost": 0.1,
            "currency": "USD",
            "currency_conversion_rate": 1.0,
            "usage": {"amount": 1.0, "unit": "h", "amount_in_pricing_units": 1.0, "pricing_unit": "hour"},
            "credits": [],
            "invoice": {"month": "202603"},
            "cost_type": "regular",
            "adjustment_info": None,
        }
        rec = normalize_gcp_row(row)
        assert rec is not None
        assert rec.EffectiveCost == pytest.approx(0.1)
        # GCP normalizer uses 'Google Cloud Platform' as ProviderName
        assert rec.ProviderName in ("Google Cloud", "Google Cloud Platform")

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """An empty JSON array file should return an empty list without raising."""
        empty_json = tmp_path / "empty_gcp.json"
        empty_json.write_text("[]")
        records = parse_gcp_json(str(empty_json))
        assert records == []
