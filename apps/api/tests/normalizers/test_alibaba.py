"""
Unit tests for the Alibaba Cloud billing normalizer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion.normalizers.alibaba import parse_alibaba_json


CNY_TO_USD = 0.138  # Fixed rate for deterministic tests


class TestAlibabaaNormalizer:
    """Tests for parse_alibaba_json()."""

    def _load_fixture(self, alibaba_billing_json: Path) -> list[dict]:
        with open(alibaba_billing_json) as f:
            return json.load(f)

    def test_returns_list_of_focus_records(self, alibaba_billing_json: Path) -> None:
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        assert isinstance(records, list)
        assert len(records) > 0

    def test_ecs_record_fields(self, alibaba_billing_json: Path) -> None:
        """ECS record should map to Compute service category."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        # Normalizer appends ProductType: 'Elastic Compute Service - ecs'
        ecs_records = [r for r in records if "Elastic Compute" in (r.ServiceName or "")]
        assert len(ecs_records) >= 1
        ecs = ecs_records[0]
        assert ecs.ProviderName == "Alibaba Cloud"
        assert ecs.ServiceCategory == "Compute"
        # 145.92 CNY * 0.138 = ~20.14 USD
        assert ecs.EffectiveCost == pytest.approx(145.92 * CNY_TO_USD, rel=1e-2)

    def test_oss_record_fields(self, alibaba_billing_json: Path) -> None:
        """OSS record should map to Storage service category."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        oss_records = [r for r in records if "Object Storage" in (r.ServiceName or "")]
        assert len(oss_records) >= 1
        oss = oss_records[0]
        assert oss.ServiceCategory == "Storage"

    def test_rds_record_fields(self, alibaba_billing_json: Path) -> None:
        """RDS record should map to Database service category."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        rds_records = [r for r in records if "RDS" in (r.ServiceName or "") or "ApsaraDB" in (r.ServiceName or "")]
        assert len(rds_records) >= 1
        rds = rds_records[0]
        assert rds.ServiceCategory in ("Database", "Databases")

    def test_currency_conversion(self, alibaba_billing_json: Path) -> None:
        """All costs should be converted from CNY to USD."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        for rec in records:
            # All costs should be positive USD values
            assert rec.EffectiveCost >= 0
            # The original CNY amounts are > 1, so USD should also be > 0
            assert rec.EffectiveCost > 0

    def test_tags_parsed_from_tag_string(self, alibaba_billing_json: Path) -> None:
        """Tags should be parsed from the 'key:value,key:value' format."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        tagged = [r for r in records if r.Tags]
        assert len(tagged) >= 1
        tags = tagged[0].Tags
        assert isinstance(tags, dict)
        # Alibaba fixture uses lowercase keys: environment, team
        assert "environment" in tags or "team" in tags or "Environment" in tags or "Team" in tags

    def test_billing_period_dates(self, alibaba_billing_json: Path) -> None:
        """BillingPeriodStart and BillingPeriodEnd should be datetime objects."""
        from datetime import datetime
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        for rec in records:
            assert isinstance(rec.BillingPeriodStart, datetime)
            assert isinstance(rec.BillingPeriodEnd, datetime)

    def test_resource_id_from_instance_id(self, alibaba_billing_json: Path) -> None:
        """ResourceId should be populated from InstanceID."""
        items = self._load_fixture(alibaba_billing_json)
        records = parse_alibaba_json(items, CNY_TO_USD)
        for rec in records:
            assert rec.ResourceId is not None
            assert len(rec.ResourceId) > 0

    def test_empty_list_returns_empty_list(self) -> None:
        """An empty items list should return an empty list without raising."""
        records = parse_alibaba_json([], CNY_TO_USD)
        assert records == []
