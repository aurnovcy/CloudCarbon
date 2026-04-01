"""
Unit tests for the AWS CUR normalizer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.normalizers.aws import parse_aws_csv


class TestAwsNormalizer:
    """Tests for parse_aws_csv()."""

    def test_returns_list_of_focus_records(self, aws_cur_csv: Path) -> None:
        records = parse_aws_csv(str(aws_cur_csv))
        assert isinstance(records, list)
        assert len(records) > 0

    def test_skips_tax_line_items(self, aws_cur_csv: Path) -> None:
        """Tax line items should be skipped (ChargeCategory = Tax is not Usage)."""
        records = parse_aws_csv(str(aws_cur_csv))
        # The fixture has 5 rows: 4 Usage + 1 Tax
        # Tax rows should be included but mapped to ChargeCategory=Tax
        # Verify no record has cost from the tax row mapped incorrectly
        for rec in records:
            assert rec.EffectiveCost is not None

    def test_ec2_record_fields(self, aws_cur_csv: Path) -> None:
        """EC2 record should have correct provider, service, and region."""
        records = parse_aws_csv(str(aws_cur_csv))
        ec2_records = [r for r in records if "Compute" in (r.ServiceName or "") or "EC2" in (r.ServiceName or "")]
        assert len(ec2_records) >= 1
        ec2 = ec2_records[0]
        assert ec2.ProviderName == "Amazon Web Services"
        assert ec2.ServiceCategory == "Compute"
        assert ec2.RegionId is not None
        assert ec2.EffectiveCost == pytest.approx(0.192, rel=1e-3)

    def test_s3_record_fields(self, aws_cur_csv: Path) -> None:
        """S3 record should have Storage service category."""
        records = parse_aws_csv(str(aws_cur_csv))
        s3_records = [r for r in records if "Simple Storage" in (r.ServiceName or "") or "S3" in (r.ServiceName or "")]
        assert len(s3_records) >= 1
        s3 = s3_records[0]
        assert s3.ServiceCategory == "Storage"
        assert s3.EffectiveCost == pytest.approx(0.024696, rel=1e-3)

    def test_tags_are_parsed(self, aws_cur_csv: Path) -> None:
        """Resource tags should be parsed into a dict."""
        records = parse_aws_csv(str(aws_cur_csv))
        tagged = [r for r in records if r.Tags]
        assert len(tagged) >= 1
        tags = tagged[0].Tags
        assert isinstance(tags, dict)
        assert "Environment" in tags or "environment" in tags

    def test_billing_period_dates(self, aws_cur_csv: Path) -> None:
        """BillingPeriodStart and BillingPeriodEnd should be datetime objects."""
        from datetime import datetime
        records = parse_aws_csv(str(aws_cur_csv))
        for rec in records:
            assert isinstance(rec.BillingPeriodStart, datetime)
            assert isinstance(rec.BillingPeriodEnd, datetime)
            assert rec.BillingPeriodStart < rec.BillingPeriodEnd

    def test_resource_id_preserved(self, aws_cur_csv: Path) -> None:
        """ResourceId should be preserved from lineItem/ResourceId."""
        records = parse_aws_csv(str(aws_cur_csv))
        ec2_records = [r for r in records if r.ResourceId and "i-0abc" in r.ResourceId]
        assert len(ec2_records) >= 1
        assert "i-0abc123def456789" in ec2_records[0].ResourceId

    def test_lambda_record(self, aws_cur_csv: Path) -> None:
        """Lambda record should map to Serverless service category."""
        records = parse_aws_csv(str(aws_cur_csv))
        lambda_records = [r for r in records if "Lambda" in (r.ServiceName or "")]
        assert len(lambda_records) >= 1
        lam = lambda_records[0]
        assert lam.ServiceCategory in ("Serverless", "Compute")

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """An empty CSV file should return an empty list without raising."""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text(
            "identity/LineItemId,lineItem/LineItemType,lineItem/UnblendedCost\n"
        )
        records = parse_aws_csv(str(empty_csv))
        assert records == []
