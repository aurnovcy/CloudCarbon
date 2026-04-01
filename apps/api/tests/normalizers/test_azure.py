"""
Unit tests for the Azure Cost Management normalizer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.normalizers.azure import parse_azure_csv


class TestAzureNormalizer:
    """Tests for parse_azure_csv()."""

    def test_returns_list_of_focus_records(self, azure_cost_csv: Path) -> None:
        records = parse_azure_csv(str(azure_cost_csv))
        assert isinstance(records, list)
        assert len(records) > 0

    def test_vm_record_fields(self, azure_cost_csv: Path) -> None:
        """Virtual Machine record should have Compute service category."""
        records = parse_azure_csv(str(azure_cost_csv))
        vm_records = [r for r in records if "Virtual Machine" in (r.ServiceName or "")]
        assert len(vm_records) >= 1
        vm = vm_records[0]
        assert vm.ProviderName == "Microsoft Azure"
        assert vm.ServiceCategory == "Compute"
        assert vm.EffectiveCost == pytest.approx(138.24, rel=1e-2)

    def test_storage_record_fields(self, azure_cost_csv: Path) -> None:
        """Blob Storage record should have Storage service category."""
        records = parse_azure_csv(str(azure_cost_csv))
        storage_records = [r for r in records if "Storage" in (r.ServiceName or "")]
        assert len(storage_records) >= 1
        storage = storage_records[0]
        assert storage.ServiceCategory == "Storage"

    def test_region_mapping(self, azure_cost_csv: Path) -> None:
        """Azure region 'westeurope' should be mapped to a RegionId."""
        records = parse_azure_csv(str(azure_cost_csv))
        for rec in records:
            if rec.RegionId:
                assert isinstance(rec.RegionId, str)
                assert len(rec.RegionId) > 0

    def test_tags_parsed_from_json_string(self, azure_cost_csv: Path) -> None:
        """Tags column (JSON string) should be parsed into a dict."""
        records = parse_azure_csv(str(azure_cost_csv))
        tagged = [r for r in records if r.Tags]
        assert len(tagged) >= 1
        tags = tagged[0].Tags
        assert isinstance(tags, dict)

    def test_billing_period_dates(self, azure_cost_csv: Path) -> None:
        """BillingPeriodStart and BillingPeriodEnd should be datetime objects."""
        from datetime import datetime
        records = parse_azure_csv(str(azure_cost_csv))
        for rec in records:
            assert isinstance(rec.BillingPeriodStart, datetime)
            assert isinstance(rec.BillingPeriodEnd, datetime)

    def test_resource_id_preserved(self, azure_cost_csv: Path) -> None:
        """ResourceId should be the full Azure resource path."""
        records = parse_azure_csv(str(azure_cost_csv))
        vm_records = [r for r in records if r.ResourceId and "virtualMachines" in r.ResourceId]
        assert len(vm_records) >= 1

    def test_zero_cost_aks_record(self, azure_cost_csv: Path) -> None:
        """AKS management fee (zero cost) should still be ingested."""
        records = parse_azure_csv(str(azure_cost_csv))
        aks_records = [r for r in records if "Kubernetes" in (r.ServiceName or "")]
        assert len(aks_records) >= 1
        assert aks_records[0].EffectiveCost == pytest.approx(0.0, abs=1e-6)

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """An empty CSV file should return an empty list without raising."""
        empty_csv = tmp_path / "empty_azure.csv"
        empty_csv.write_text("BillingAccountId,Cost,ServiceFamily\n")
        records = parse_azure_csv(str(empty_csv))
        assert records == []
