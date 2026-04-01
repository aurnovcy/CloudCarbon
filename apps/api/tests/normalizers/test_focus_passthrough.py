"""
Unit tests for the FOCUS 1.0 passthrough normalizer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.normalizers.focus_passthrough import parse_focus_file


class TestFocusPassthrough:
    """Tests for parse_focus_file()."""

    def test_returns_records_and_no_errors_for_valid_file(self, focus_csv: Path) -> None:
        records, errors = parse_focus_file(str(focus_csv))
        assert isinstance(records, list)
        assert isinstance(errors, list)
        assert len(records) > 0
        assert len(errors) == 0

    def test_usage_record_fields(self, focus_csv: Path) -> None:
        """Usage records should have all required FOCUS fields populated."""
        records, _ = parse_focus_file(str(focus_csv))
        usage_records = [r for r in records if r.ChargeCategory == "Usage"]
        assert len(usage_records) >= 1
        rec = usage_records[0]
        assert rec.ProviderName == "Amazon Web Services"
        assert rec.ServiceCategory == "Compute"
        assert rec.EffectiveCost == pytest.approx(0.192, rel=1e-3)

    def test_purchase_record_charge_category(self, focus_csv: Path) -> None:
        """Purchase records should have ChargeCategory = 'Purchase'."""
        records, _ = parse_focus_file(str(focus_csv))
        purchase_records = [r for r in records if r.ChargeCategory == "Purchase"]
        assert len(purchase_records) >= 1

    def test_tags_parsed(self, focus_csv: Path) -> None:
        """Tags column (JSON string) should be parsed into a dict."""
        records, _ = parse_focus_file(str(focus_csv))
        tagged = [r for r in records if r.Tags]
        assert len(tagged) >= 1
        tags = tagged[0].Tags
        assert isinstance(tags, dict)

    def test_billing_period_dates(self, focus_csv: Path) -> None:
        """BillingPeriodStart and BillingPeriodEnd should be datetime objects."""
        from datetime import datetime
        records, _ = parse_focus_file(str(focus_csv))
        for rec in records:
            assert isinstance(rec.BillingPeriodStart, datetime)
            assert isinstance(rec.BillingPeriodEnd, datetime)

    def test_invalid_cost_produces_validation_error(self, tmp_path: Path) -> None:
        """A row with an invalid EffectiveCost should produce a validation error."""
        bad_csv = tmp_path / "bad_focus.csv"
        bad_csv.write_text(
            "BillingPeriodStart,BillingPeriodEnd,ChargeCategory,EffectiveCost,"
            "InvoiceIssuerName,ProviderName,ServiceCategory,ServiceName\n"
            "2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Usage,NOT_A_NUMBER,"
            "AWS,Amazon Web Services,Compute,Amazon EC2\n"
        )
        records, errors = parse_focus_file(str(bad_csv))
        assert len(errors) >= 1
        assert len(records) == 0

    def test_missing_required_field_produces_validation_error(self, tmp_path: Path) -> None:
        """A row missing a required field (ProviderName) should produce a validation error."""
        bad_csv = tmp_path / "missing_field.csv"
        bad_csv.write_text(
            "BillingPeriodStart,BillingPeriodEnd,ChargeCategory,EffectiveCost,"
            "InvoiceIssuerName,ServiceCategory,ServiceName\n"
            "2026-03-01T00:00:00Z,2026-04-01T00:00:00Z,Usage,0.192,"
            "AWS,Compute,Amazon EC2\n"
        )
        records, errors = parse_focus_file(str(bad_csv))
        # ProviderName is required; should fail validation
        assert len(errors) >= 1

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """An empty CSV file (headers only) should return empty lists without raising."""
        empty_csv = tmp_path / "empty_focus.csv"
        empty_csv.write_text(
            "BillingPeriodStart,BillingPeriodEnd,ChargeCategory,EffectiveCost,"
            "InvoiceIssuerName,ProviderName,ServiceCategory,ServiceName\n"
        )
        records, errors = parse_focus_file(str(empty_csv))
        assert records == []
        assert errors == []
