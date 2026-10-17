"""Evaluate daily rule predicates from verified indicator snapshots.

This does not compute indicators, size positions, implement stops, or place
orders. Deployment still requires a validated adapter for those mechanics.
"""

import math
import operator
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

LIVE_INDICATORS = frozenset({"RSI", "SMA", "EMA", "WMA", "DEMA", "TEMA", "ADX"})
OPERATORS = frozenset(
    {"greater_than", "less_than", "equals", "not_equals", "crosses_above", "crosses_below"}
)

# The backtest engine's indicator schema names the lookback `length`
# (src/backtest-server/src/models/indicator_catalog.py). The live market-data
# MCP tool calls the same argument `period`. Gate on the schema's spelling so a
# frozen strategy JSON is accepted as tested.
_LIVE_PARAMS = frozenset({"length"})
_RAW_OPERANDS = frozenset({"open", "high", "low", "close", "volume"})
_COMPARISONS: Mapping[str, Callable[[float, float], bool]] = {
    "greater_than": operator.gt,
    "less_than": operator.lt,
    "equals": operator.eq,
    "not_equals": operator.ne,
}


def _number(value: object) -> float:
    """Return a finite float, rejecting bools and warm-up nulls."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Rule inputs must be finite numbers, with completed warm-up")
    return float(value)


def _operand(operand: Any, values: dict[str, float]) -> float:
    """Resolve one rule operand against a single bar's values.

    Args:
        operand: A rule operand naming exactly one `constant` or `indicator`.
        values: Indicator and raw-OHLCV values for the bar being evaluated.

    Returns:
        The operand's finite numeric value.

    Raises:
        ValueError: If the operand is malformed or its value is unusable.
    """
    if not isinstance(operand, dict):
        raise ValueError("Unsupported or ambiguous rule operand")
    fields = {key: value for key, value in operand.items() if value is not None}
    if len(fields) != 1 or not set(fields) <= {"constant", "indicator"}:
        raise ValueError("Unsupported or ambiguous rule operand")
    if "constant" in fields:
        return _number(fields["constant"])
    name = fields["indicator"]
    if name not in values:
        raise ValueError(f"Missing indicator value: {name}")
    return _number(values[name])


def _check_ruleset(rules: Any) -> list[dict[str, Any]]:
    """Validate a ruleset's shape and return its conditions.

    Args:
        rules: A strategy `entry_rules` or `exit_rules` object.

    Returns:
        The ruleset's conditions, each already shape-checked.

    Raises:
        ValueError: If the ruleset or any condition is malformed.
    """
    if not isinstance(rules, dict) or set(rules) != {"logic", "conditions"}:
        raise ValueError("Unsupported ruleset")
    if rules["logic"] not in {"AND", "OR"} or not isinstance(rules["conditions"], list):
        raise ValueError("Unsupported ruleset")
    for condition in rules["conditions"]:
        if not isinstance(condition, dict) or set(condition) != {"left", "operator", "right"}:
            raise ValueError("Unsupported condition fields")
        if condition["operator"] not in OPERATORS:
            raise ValueError(f"Unsupported operator: {condition['operator']}")
    return rules["conditions"]


def _condition_holds(
    condition: dict[str, Any],
    previous: dict[str, float],
    current: dict[str, float],
) -> bool:
    """Evaluate one shape-checked condition on the completed bar pair."""
    op = condition["operator"]
    left = _operand(condition["left"], current)
    right = _operand(condition["right"], current)
    compare = _COMPARISONS.get(op)
    if compare is not None:
        return compare(left, right)
    left_prev = _operand(condition["left"], previous)
    right_prev = _operand(condition["right"], previous)
    if op == "crosses_above":
        return left_prev < right_prev and left >= right
    return left_prev > right_prev and left <= right


def evaluate_rules(
    rules: dict[str, Any],
    previous: dict[str, float],
    current: dict[str, float],
) -> bool:
    """Evaluate a ruleset with the equity engine's crossover semantics.

    A crossover needs a strictly opposite previous value and a current value
    touching or passing the threshold. Every condition is evaluated so missing
    data raises even when the logic would have short-circuited.

    Args:
        rules: A strategy `entry_rules` or `exit_rules` object.
        previous: Values for the bar before the signal bar.
        current: Values for the completed signal bar.

    Returns:
        True when the ruleset fires; False for an empty ruleset.

    Raises:
        ValueError: If the ruleset, an operand, or a value is unusable.
    """
    conditions = _check_ruleset(rules)
    results = [_condition_holds(condition, previous, current) for condition in conditions]
    if not results:
        return False
    return all(results) if rules["logic"] == "AND" else any(results)


def _check_bars(
    strategy: dict[str, Any],
    snapshot: dict[str, Any],
    expected_bar_date: str,
    expected_price_basis: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the snapshot's provenance and return its bar pair.

    Args:
        strategy: The frozen strategy JSON.
        snapshot: The normalized completed-bar snapshot.
        expected_bar_date: Last completed session, from the exchange calendar.
        expected_price_basis: Adjustment basis from the validated plan.

    Returns:
        The previous and current bar records.

    Raises:
        ValueError: If any provenance or completeness gate fails.
    """
    if (
        strategy.get("data_config", {}).get("timeframe", "daily") != "daily"
        or snapshot.get("timeframe") != "daily"
    ):
        raise ValueError("Only completed daily-bar snapshots are supported")
    if snapshot.get("symbol") not in strategy["universe"]["symbols"]:
        raise ValueError("Snapshot symbol is outside the strategy universe")
    if not expected_price_basis or snapshot.get("price_basis") != expected_price_basis:
        raise ValueError("Price basis is missing or differs from the validated execution plan")
    if snapshot.get("warmup_complete") is not True:
        raise ValueError("Indicator warm-up is not verified")
    previous, current = snapshot["previous"], snapshot["current"]
    if previous.get("complete") is not True or current.get("complete") is not True:
        raise ValueError("Both bars must be completed")
    if current["date"] != expected_bar_date:
        raise ValueError("Current bar is not the expected completed session")
    if date.fromisoformat(previous["date"]) >= date.fromisoformat(current["date"]):
        raise ValueError("Stale or out-of-order completed bars")
    return previous, current


def _check_indicators(
    strategy: dict[str, Any],
    previous: dict[str, Any],
    current: dict[str, Any],
) -> set[str]:
    """Check every declared indicator against the live adapter's capabilities.

    Args:
        strategy: The frozen strategy JSON.
        previous: The bar before the signal bar.
        current: The completed signal bar.

    Returns:
        The declared indicator IDs.

    Raises:
        ValueError: If an indicator is unsupported, duplicated, or unvalued.
    """
    identifiers: set[str] = set()
    for indicator in strategy["indicators"]:
        unsupported = (
            str(indicator["type"]).upper() not in LIVE_INDICATORS
            or indicator.get("source", "close") != "close"
            or bool(set(indicator.get("params", {})) - _LIVE_PARAMS)
        )
        if unsupported:
            raise ValueError(f"Live indicator adapter does not support {indicator['id']}")
        if indicator["id"] in identifiers:
            raise ValueError("Duplicate indicator ID")
        identifiers.add(indicator["id"])
        for bar in (previous, current):
            _operand({"indicator": indicator["id"]}, bar["values"])
    return identifiers


def _check_rule_operands(strategy: dict[str, Any], allowed: set[str]) -> None:
    """Reject any rule operand naming an indicator the strategy never declared."""
    for rules in (strategy["entry_rules"], strategy["exit_rules"]):
        for condition in _check_ruleset(rules):
            for side in ("left", "right"):
                operand = condition[side]
                name = operand.get("indicator") if isinstance(operand, dict) else None
                if name is not None and name not in allowed:
                    raise ValueError(f"Undeclared indicator: {name}")


def evaluate_signals(
    strategy: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_bar_date: str,
    expected_price_basis: str,
) -> dict[str, str | bool]:
    """Check the narrow live-data capability contract before evaluating a signal.

    Args:
        strategy: The frozen, tested strategy JSON.
        snapshot: The normalized completed-bar snapshot (see signal-input.md).
        expected_bar_date: Last completed session, from the exchange calendar.
        expected_price_basis: Adjustment basis from the validated plan.

    Returns:
        The symbol, signal bar date, and the entry and exit predicates.

    Raises:
        ValueError: If any capability or data-quality gate fails.

    Note:
        `expected_bar_date` must come from the exchange calendar. Snapshot
        quality flags attest to upstream validation; they cannot prove
        indicator fidelity.
    """
    previous, current = _check_bars(strategy, snapshot, expected_bar_date, expected_price_basis)
    declared = _check_indicators(strategy, previous, current)
    _check_rule_operands(strategy, declared | set(_RAW_OPERANDS))
    return {
        "symbol": snapshot["symbol"],
        "signal_bar_date": current["date"],
        "entry_signal": evaluate_rules(
            strategy["entry_rules"], previous["values"], current["values"]
        ),
        "exit_signal": evaluate_rules(
            strategy["exit_rules"], previous["values"], current["values"]
        ),
    }
