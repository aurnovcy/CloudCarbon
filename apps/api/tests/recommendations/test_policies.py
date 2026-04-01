"""
Unit tests for the policy rule engine.

Tests cover:
  - All five rule types
  - All six operators (eq, neq, gt, lt, contains, in)
  - AND / OR combinators
  - validate_condition helper
  - apply_policies_batch
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/home/ubuntu/cloudcarbon/packages/carbon-models/src")

from carbon_models.policies import (
    PolicyRule,
    apply_policies,
    apply_policies_batch,
    validate_condition,
    _evaluate_condition,
    _evaluate_operator,
)
from carbon_models.recommendations import Recommendation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_rec(
    type: str = "rightsize",
    resource_id: str = "res-001",
    provider: str = "AWS",
    region: str = "us-east-1",
    service_name: str = "Amazon EC2",
    co2e: float = 50.0,
    cost: float = 200.0,
    water_stress_score: float = 2.0,
) -> Recommendation:
    return Recommendation(
        type=type,
        resource_id=resource_id,
        provider=provider,
        region=region,
        service_name=service_name,
        cost_impact_monthly_usd=cost,
        co2e_impact_monthly_kg=co2e,
        water_impact_monthly_litres=10.0,
        impact_score=0.8,
        cost_weight_used=0.6,
        carbon_weight_used=0.3,
        water_weight_used=0.1,
        complexity="low",
        implementation_steps=[],
        methodology_notes="",
        policy_blocked=False,
        policy_block_reason=None,
        requires_approval=False,
    )


def _make_rule(
    rule_type: str,
    condition: dict,
    name: str = "Test Rule",
    is_active: bool = True,
) -> PolicyRule:
    return PolicyRule(
        id="rule-001",
        name=name,
        rule_type=rule_type,
        condition=condition,
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# Test: _evaluate_operator
# ---------------------------------------------------------------------------

class TestEvaluateOperator:
    def test_eq_match(self):
        assert _evaluate_operator("us-east-1", "eq", "us-east-1") is True

    def test_eq_case_insensitive(self):
        assert _evaluate_operator("AWS", "eq", "aws") is True

    def test_neq_match(self):
        assert _evaluate_operator("AWS", "neq", "Azure") is True

    def test_neq_no_match(self):
        assert _evaluate_operator("AWS", "neq", "AWS") is False

    def test_gt_match(self):
        assert _evaluate_operator(50.0, "gt", 10.0) is True

    def test_gt_no_match(self):
        assert _evaluate_operator(5.0, "gt", 10.0) is False

    def test_lt_match(self):
        assert _evaluate_operator(5.0, "lt", 10.0) is True

    def test_lt_no_match(self):
        assert _evaluate_operator(50.0, "lt", 10.0) is False

    def test_contains_match(self):
        assert _evaluate_operator("us-east-1", "contains", "east") is True

    def test_contains_no_match(self):
        assert _evaluate_operator("us-east-1", "contains", "west") is False

    def test_in_match(self):
        assert _evaluate_operator("AWS", "in", ["AWS", "Azure", "GCP"]) is True

    def test_in_no_match(self):
        assert _evaluate_operator("Alibaba", "in", ["AWS", "Azure", "GCP"]) is False

    def test_none_value_returns_false(self):
        assert _evaluate_operator(None, "eq", "anything") is False


# ---------------------------------------------------------------------------
# Test: _evaluate_condition
# ---------------------------------------------------------------------------

class TestEvaluateCondition:
    def test_simple_condition_match(self):
        rec = _make_rec(region="us-east-1")
        cond = {"field": "region", "operator": "eq", "value": "us-east-1"}
        assert _evaluate_condition(rec, cond) is True

    def test_simple_condition_no_match(self):
        rec = _make_rec(region="eu-west-1")
        cond = {"field": "region", "operator": "eq", "value": "us-east-1"}
        assert _evaluate_condition(rec, cond) is False

    def test_and_combinator_both_match(self):
        rec = _make_rec(provider="AWS", region="us-east-1")
        cond = {
            "and": [
                {"field": "provider", "operator": "eq", "value": "AWS"},
                {"field": "region", "operator": "contains", "value": "us-east"},
            ]
        }
        assert _evaluate_condition(rec, cond) is True

    def test_and_combinator_one_fails(self):
        rec = _make_rec(provider="AWS", region="eu-west-1")
        cond = {
            "and": [
                {"field": "provider", "operator": "eq", "value": "AWS"},
                {"field": "region", "operator": "contains", "value": "us-east"},
            ]
        }
        assert _evaluate_condition(rec, cond) is False

    def test_or_combinator_one_matches(self):
        rec = _make_rec(provider="Azure", region="eu-west-1")
        cond = {
            "or": [
                {"field": "provider", "operator": "eq", "value": "AWS"},
                {"field": "provider", "operator": "eq", "value": "Azure"},
            ]
        }
        assert _evaluate_condition(rec, cond) is True

    def test_or_combinator_none_match(self):
        rec = _make_rec(provider="GCP")
        cond = {
            "or": [
                {"field": "provider", "operator": "eq", "value": "AWS"},
                {"field": "provider", "operator": "eq", "value": "Azure"},
            ]
        }
        assert _evaluate_condition(rec, cond) is False

    def test_co2e_impact_field(self):
        rec = _make_rec(co2e=50.0)
        cond = {"field": "co2e_impact", "operator": "gt", "value": 10.0}
        assert _evaluate_condition(rec, cond) is True

    def test_cost_impact_field(self):
        rec = _make_rec(cost=200.0)
        cond = {"field": "cost_impact", "operator": "lt", "value": 100.0}
        assert _evaluate_condition(rec, cond) is False


# ---------------------------------------------------------------------------
# Test: apply_policies — rule types
# ---------------------------------------------------------------------------

class TestApplyPolicies:
    def test_exclude_region_blocks_matching(self):
        rec = _make_rec(region="us-east-1")
        rule = _make_rule(
            "exclude_region",
            {"field": "region", "operator": "contains", "value": "us-east-1"},
            name="Exclude US East 1",
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is True
        assert result.policy_block_reason == "Exclude US East 1"

    def test_exclude_region_does_not_block_non_matching(self):
        rec = _make_rec(region="eu-west-1")
        rule = _make_rule(
            "exclude_region",
            {"field": "region", "operator": "contains", "value": "us-east-1"},
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is False

    def test_min_co2e_threshold_blocks_below_threshold(self):
        rec = _make_rec(co2e=5.0)
        rule = _make_rule(
            "min_co2e_threshold",
            {"field": "co2e_impact", "operator": "lt", "value": 10.0},
            name="Min CO2e 10kg",
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is True

    def test_min_co2e_threshold_allows_above_threshold(self):
        rec = _make_rec(co2e=50.0)
        rule = _make_rule(
            "min_co2e_threshold",
            {"field": "co2e_impact", "operator": "lt", "value": 10.0},
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is False

    def test_require_approval_sets_flag(self):
        rec = _make_rec(type="terminate_idle")
        rule = _make_rule(
            "require_approval",
            {"field": "type", "operator": "eq", "value": "terminate_idle"},
            name="Approve Terminations",
        )
        result = apply_policies(rec, [rule])
        assert result.requires_approval is True
        assert result.policy_blocked is False  # require_approval does NOT block

    def test_water_stress_block_blocks_high_stress(self):
        rec = _make_rec(type="region_migrate_carbon")
        # Manually set water_stress_score on rec
        rec.water_stress_score = 4.0  # type: ignore[attr-defined]
        rule = _make_rule(
            "water_stress_block",
            {"field": "water_stress_score", "operator": "gt", "value": 3.0},
            name="Block High Water Stress",
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is True

    def test_exclude_provider_blocks_matching(self):
        rec = _make_rec(provider="Alibaba")
        rule = _make_rule(
            "exclude_provider",
            {"field": "provider", "operator": "eq", "value": "alibaba"},
            name="Exclude Alibaba",
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is True

    def test_inactive_rule_ignored(self):
        rec = _make_rec(region="us-east-1")
        rule = _make_rule(
            "exclude_region",
            {"field": "region", "operator": "eq", "value": "us-east-1"},
            is_active=False,
        )
        result = apply_policies(rec, [rule])
        assert result.policy_blocked is False

    def test_multiple_rules_first_block_wins(self):
        rec = _make_rec(region="us-east-1", type="terminate_idle")
        rules = [
            _make_rule(
                "exclude_region",
                {"field": "region", "operator": "eq", "value": "us-east-1"},
                name="Block Region",
            ),
            _make_rule(
                "require_approval",
                {"field": "type", "operator": "eq", "value": "terminate_idle"},
                name="Require Approval",
            ),
        ]
        result = apply_policies(rec, rules)
        assert result.policy_blocked is True
        assert result.requires_approval is True  # both rules apply

    def test_no_rules_returns_unchanged(self):
        rec = _make_rec()
        result = apply_policies(rec, [])
        assert result.policy_blocked is False
        assert result.requires_approval is False


# ---------------------------------------------------------------------------
# Test: validate_condition
# ---------------------------------------------------------------------------

class TestValidateCondition:
    def test_valid_simple_condition(self):
        cond = {"field": "region", "operator": "eq", "value": "us-east-1"}
        errors = validate_condition(cond)
        assert errors == []

    def test_missing_field(self):
        cond = {"operator": "eq", "value": "us-east-1"}
        errors = validate_condition(cond)
        assert any("field" in e for e in errors)

    def test_missing_operator(self):
        cond = {"field": "region", "value": "us-east-1"}
        errors = validate_condition(cond)
        assert any("operator" in e for e in errors)

    def test_missing_value(self):
        cond = {"field": "region", "operator": "eq"}
        errors = validate_condition(cond)
        assert any("value" in e for e in errors)

    def test_unknown_field(self):
        cond = {"field": "unknown_field", "operator": "eq", "value": "x"}
        errors = validate_condition(cond)
        assert any("Unknown field" in e for e in errors)

    def test_unknown_operator(self):
        cond = {"field": "region", "operator": "starts_with", "value": "us"}
        errors = validate_condition(cond)
        assert any("Unknown operator" in e for e in errors)

    def test_valid_and_combinator(self):
        cond = {
            "and": [
                {"field": "region", "operator": "eq", "value": "us-east-1"},
                {"field": "provider", "operator": "eq", "value": "AWS"},
            ]
        }
        errors = validate_condition(cond)
        assert errors == []

    def test_invalid_nested_condition(self):
        cond = {
            "and": [
                {"field": "region", "operator": "eq", "value": "us-east-1"},
                {"field": "unknown", "operator": "eq", "value": "x"},
            ]
        }
        errors = validate_condition(cond)
        assert len(errors) > 0

    def test_empty_condition(self):
        errors = validate_condition({})
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Test: apply_policies_batch
# ---------------------------------------------------------------------------

class TestApplyPoliciesBatch:
    def test_batch_applies_to_all(self):
        recs = [_make_rec(region="us-east-1") for _ in range(5)]
        rule = _make_rule(
            "exclude_region",
            {"field": "region", "operator": "eq", "value": "us-east-1"},
        )
        results = apply_policies_batch(recs, [rule])
        assert all(r.policy_blocked for r in results)

    def test_batch_empty_rules(self):
        recs = [_make_rec() for _ in range(3)]
        results = apply_policies_batch(recs, [])
        assert all(not r.policy_blocked for r in results)

    def test_batch_empty_recs(self):
        results = apply_policies_batch([], [_make_rule("exclude_region", {})])
        assert results == []
