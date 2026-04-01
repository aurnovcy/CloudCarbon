"""
CloudCarbon Policy Rule Engine.

Evaluates tenant-configured governance rules against recommendations.
Rules are defined using a simple JSON DSL and never discard recommendations —
they set policy_blocked=True or requires_approval=True so the UI can show
why a recommendation was suppressed.

Supported rule types:
  - exclude_region       : filter out recommendations targeting a specific region
  - min_co2e_threshold   : suppress recommendations below a minimum carbon impact
  - require_approval     : mark recommendations as requiring manual approval
  - water_stress_block   : block region migrations to high water-stress regions
  - exclude_provider     : suppress all recommendations for a specific provider

DSL condition structure:
  {
    "field": "region" | "provider" | "service_name" | "type" |
             "co2e_impact" | "cost_impact" | "water_stress_score",
    "operator": "eq" | "neq" | "gt" | "lt" | "contains" | "in",
    "value": any
  }

Multiple conditions can be combined with "and" / "or" at the top level:
  {"and": [condition1, condition2]}
  {"or":  [condition1, condition2]}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported operators
# ---------------------------------------------------------------------------

_OPERATORS = {"eq", "neq", "gt", "lt", "gte", "lte", "contains", "in"}

# Mapping from condition field names to Recommendation attribute names
_FIELD_MAP: dict[str, str] = {
    "region": "region",
    "provider": "provider",
    "service_name": "service_name",
    "type": "type",
    "co2e_impact": "co2e_impact_monthly_kg",
    "cost_impact": "cost_impact_monthly_usd",
    "water_stress_score": "water_stress_score",  # special: not on Recommendation directly
}


# ---------------------------------------------------------------------------
# PolicyRule dataclass (mirrors the SQLAlchemy model for pure-Python use)
# ---------------------------------------------------------------------------

@dataclass
class PolicyRule:
    """
    Minimal representation of a policy_rules row for use in the carbon-models package.
    The full SQLAlchemy model lives in apps/api.
    """
    id: str
    name: str
    rule_type: str
    condition: dict  # JSON condition object
    is_active: bool = True
    description: str = ""


# ---------------------------------------------------------------------------
# Condition evaluator
# ---------------------------------------------------------------------------

def _get_field_value(recommendation: Any, field: str) -> Any:
    """
    Extract the value of a DSL field from a Recommendation object.
    Returns None if the field is not found.
    """
    attr = _FIELD_MAP.get(field, field)
    return getattr(recommendation, attr, None)


def _evaluate_operator(actual: Any, operator: str, expected: Any) -> bool:
    """
    Evaluate a single operator comparison.

    Args:
        actual: The actual value from the recommendation.
        operator: One of eq, neq, gt, lt, gte, lte, contains, in.
        expected: The expected value from the condition.

    Returns:
        True if the condition is satisfied.
    """
    if actual is None:
        return False

    try:
        if operator == "eq":
            return str(actual).lower() == str(expected).lower()
        elif operator == "neq":
            return str(actual).lower() != str(expected).lower()
        elif operator == "gt":
            return float(actual) > float(expected)
        elif operator == "lt":
            return float(actual) < float(expected)
        elif operator == "gte":
            return float(actual) >= float(expected)
        elif operator == "lte":
            return float(actual) <= float(expected)
        elif operator == "contains":
            return str(expected).lower() in str(actual).lower()
        elif operator == "in":
            if isinstance(expected, list):
                return str(actual).lower() in [str(v).lower() for v in expected]
            return str(actual).lower() in str(expected).lower()
        else:
            logger.warning("Unknown operator: %s", operator)
            return False
    except (ValueError, TypeError) as exc:
        logger.debug("Operator evaluation error (%s %s %s): %s", actual, operator, expected, exc)
        return False


def _evaluate_condition(recommendation: Any, condition: dict) -> bool:
    """
    Recursively evaluate a condition dict against a recommendation.

    Supports:
      - Single condition: {"field": ..., "operator": ..., "value": ...}
      - AND combination: {"and": [condition1, condition2, ...]}
      - OR combination:  {"or":  [condition1, condition2, ...]}
    """
    if not condition:
        return False

    # AND combinator
    if "and" in condition:
        return all(_evaluate_condition(recommendation, c) for c in condition["and"])

    # OR combinator
    if "or" in condition:
        return any(_evaluate_condition(recommendation, c) for c in condition["or"])

    # Single condition
    field = condition.get("field")
    operator = condition.get("operator")
    value = condition.get("value")

    if not field or not operator:
        logger.warning("Invalid condition (missing field or operator): %s", condition)
        return False

    actual = _get_field_value(recommendation, field)
    return _evaluate_operator(actual, operator, value)


# ---------------------------------------------------------------------------
# Policy application
# ---------------------------------------------------------------------------

def apply_policies(recommendation: Any, rules: list[PolicyRule]) -> Any:
    """
    Evaluate all active policy rules against a recommendation.

    Rules never discard recommendations — they set policy_blocked=True or
    requires_approval=True so the UI can show why a recommendation was affected.

    Args:
        recommendation: A Recommendation dataclass instance.
        rules: List of PolicyRule objects.

    Returns:
        The (possibly modified) recommendation.
    """
    for rule in rules:
        if not rule.is_active:
            continue

        try:
            matched = _evaluate_condition(recommendation, rule.condition)
        except Exception as exc:
            logger.warning(
                "Policy rule %s (%s) evaluation error: %s",
                rule.id, rule.name, exc,
            )
            continue

        if not matched:
            continue

        rule_type = rule.rule_type.lower()

        if rule_type in {
            "exclude_region",
            "water_stress_block",
            "exclude_provider",
            "min_co2e_threshold",
        }:
            recommendation.policy_blocked = True
            recommendation.policy_block_reason = rule.name
            logger.debug(
                "Recommendation %s/%s blocked by policy rule '%s' (%s)",
                recommendation.type, recommendation.resource_id, rule.name, rule_type,
            )

        elif rule_type == "require_approval":
            recommendation.requires_approval = True
            logger.debug(
                "Recommendation %s/%s flagged for approval by policy rule '%s'",
                recommendation.type, recommendation.resource_id, rule.name,
            )

        else:
            logger.warning("Unknown rule_type '%s' in rule '%s'", rule_type, rule.name)

    return recommendation


# ---------------------------------------------------------------------------
# Convenience: evaluate a list of recommendations
# ---------------------------------------------------------------------------

def apply_policies_batch(
    recommendations: list[Any],
    rules: list[PolicyRule],
) -> list[Any]:
    """
    Apply policy rules to a list of recommendations.

    Args:
        recommendations: List of Recommendation objects.
        rules: List of PolicyRule objects.

    Returns:
        List of recommendations with policy flags applied.
    """
    return [apply_policies(rec, rules) for rec in recommendations]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_condition(condition: dict) -> list[str]:
    """
    Validate a policy condition dict and return a list of error messages.
    Returns an empty list if the condition is valid.
    """
    errors: list[str] = []

    if not condition:
        errors.append("Condition cannot be empty")
        return errors

    if "and" in condition:
        for i, sub in enumerate(condition["and"]):
            sub_errors = validate_condition(sub)
            errors.extend(f"and[{i}]: {e}" for e in sub_errors)
        return errors

    if "or" in condition:
        for i, sub in enumerate(condition["or"]):
            sub_errors = validate_condition(sub)
            errors.extend(f"or[{i}]: {e}" for e in sub_errors)
        return errors

    field = condition.get("field")
    operator = condition.get("operator")

    if not field:
        errors.append("Missing 'field'")
    elif field not in _FIELD_MAP:
        errors.append(f"Unknown field '{field}'. Valid fields: {sorted(_FIELD_MAP.keys())}")

    if not operator:
        errors.append("Missing 'operator'")
    elif operator not in _OPERATORS:
        errors.append(f"Unknown operator '{operator}'. Valid operators: {sorted(_OPERATORS)}")

    if "value" not in condition:
        errors.append("Missing 'value'")

    return errors
