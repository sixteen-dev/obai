"""Signal generation engine — converts rules into entry/exit signals.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 4.1.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ..models.strategy import Condition, Operand, RuleSet


def generate_signals(
    df: pl.DataFrame,
    entry_rules: RuleSet,
    exit_rules: RuleSet,
) -> pl.DataFrame:
    """Generate entry and exit signal columns from rule definitions.

    Args:
        df: DataFrame with indicator columns already computed.
        entry_rules: Rules for entering positions.
        exit_rules: Rules for exiting positions.

    Returns:
        DataFrame with "entry_signal" and "exit_signal" boolean columns added.

    """
    entry_expr = _build_ruleset_expr(entry_rules)
    exit_expr = _build_ruleset_expr(exit_rules)

    # A comparison against an indicator that has not warmed up yet is null,
    # not False. Resolving that to False here keeps "undefined" from reading
    # as a signal, and leaves the boolean columns free of nulls for the
    # engines that convert them straight to numpy.
    return df.with_columns(
        entry_expr.fill_null(value=False).alias("entry_signal"),
        exit_expr.fill_null(value=False).alias("exit_signal"),
    )


def _build_ruleset_expr(ruleset: RuleSet) -> pl.Expr:
    """Build a combined Polars expression from a RuleSet.

    Args:
        ruleset: Set of conditions with AND/OR logic.

    Returns:
        Combined boolean Polars expression.

    """
    if not ruleset.conditions:
        return pl.lit(False)

    exprs = [_build_condition_expr(cond) for cond in ruleset.conditions]

    if ruleset.logic == "AND":
        result = exprs[0]
        for expr in exprs[1:]:
            result = result & expr
        return result

    # OR logic
    result = exprs[0]
    for expr in exprs[1:]:
        result = result | expr
    return result


def _apply_simple_op(left: pl.Expr, right: pl.Expr, operator: str) -> pl.Expr | None:
    """Apply simple comparison operators (no lookback).

    Args:
        left: Left operand expression.
        right: Right operand expression.
        operator: Operator name.

    Returns:
        Boolean expression or None if operator not handled here.

    """
    simple_ops: dict[str, pl.Expr] = {
        "greater_than": left > right,
        "less_than": left < right,
        "equals": left == right,
        "not_equals": left != right,
        "after_time": left >= right,
        "before_time": left < right,
    }
    return simple_ops.get(operator)


def _build_condition_expr(condition: Condition) -> pl.Expr:
    """Build a Polars expression from a single Condition.

    Args:
        condition: Comparison condition with left, operator, right.

    Returns:
        Boolean Polars expression.

    Raises:
        ValueError: If operator is not supported.

    """
    left = _resolve_operand(condition.left)
    right = _resolve_operand(condition.right)

    simple = _apply_simple_op(left, right, condition.operator)
    if simple is not None:
        return simple

    # Crossing semantics: the previous bar was strictly on one side, and the
    # current bar is on or past the other side. Using `>=` / `<=` on the
    # current bar catches equality-edge crossings (e.g. RSI exactly touching
    # 30) which the strict-on-both-sides version would silently drop.
    left_prev = _previous_value(condition.left, left)
    right_prev = _previous_value(condition.right, right)
    if condition.operator == "crosses_above":
        return (left_prev < right_prev) & (left >= right)
    if condition.operator == "crosses_below":
        return (left_prev > right_prev) & (left <= right)

    msg = f"Unsupported operator: {condition.operator}"
    raise ValueError(msg)


def _previous_value(operand: Operand, resolved: pl.Expr) -> pl.Expr:
    """Return the operand's value on the previous bar.

    A constant or an explicit time is a scalar literal of length one, and
    shifting that yields null rather than the same threshold, which resolved
    every crossover against a fixed level to null and then to False. A
    threshold's previous value is the threshold, so it is not shifted.

    Args:
        operand: The operand being compared.
        resolved: That operand's current-bar expression.

    Returns:
        The expression to compare on the previous bar.

    """
    if operand.constant is not None or operand.time is not None:
        return resolved
    return resolved.shift(1)


def _resolve_operand(operand: Operand) -> pl.Expr:
    """Resolve an operand to a Polars expression.

    Phase 4.1: Handles time_of_day and time operands for intraday.
    time_of_day="current" extracts hour + minute/60 from the date column.
    time="HH:MM" converts to a numeric literal for comparison.

    Args:
        operand: Indicator reference, constant, or time operand.

    Returns:
        Polars expression (column reference, literal, or time extraction).

    Raises:
        ValueError: If operand has no value set.

    """
    if operand.indicator is not None:
        return pl.col(operand.indicator)
    if operand.constant is not None:
        return pl.lit(operand.constant)
    if operand.time_of_day is not None:
        # Extract hour + fractional minute from the date/datetime column
        # e.g., 10:30 → 10.5, 15:45 → 15.75
        return pl.col("date").dt.hour() + pl.col("date").dt.minute() / 60.0
    if operand.time is not None:
        # Parse "HH:MM" to numeric for comparison with time_of_day
        return pl.lit(_time_str_to_numeric(operand.time))
    msg = "Operand must have one of: indicator, constant, time_of_day, time"
    raise ValueError(msg)


def _time_str_to_numeric(time_str: str) -> float:
    """Convert "HH:MM" string to numeric hours (e.g., "10:30" → 10.5).

    Args:
        time_str: Time string in HH:MM format.

    Returns:
        Numeric hour value.

    Raises:
        ValueError: If format is invalid.

    """
    parts = time_str.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = f"Invalid time format '{time_str}', expected HH:MM"
        raise ValueError(msg)
    return int(parts[0]) + int(parts[1]) / 60.0


def count_condition_hits(df: pl.DataFrame, ruleset: RuleSet) -> list[dict[str, Any]]:
    """Count the bars each condition is true on, in declaration order.

    Counts every predicate on its own, so a rule set whose combination never
    fires can be read apart: a predicate with zero hits never fires at all,
    while predicates that all fire mean the combination is what is rare.
    Crossover predicates count transition bars, matching the signal engine.

    Args:
        df: Frame with the indicator columns the conditions reference.
        ruleset: Conditions to count.

    Returns:
        One ``{"rule": label, "true_bars": count}`` per condition, in
        declaration order; an empty list for a rule set with no conditions.

    """
    hits: list[dict[str, Any]] = []
    for condition in ruleset.conditions:
        expr = _build_condition_expr(condition).fill_null(value=False)
        hits.append(
            {
                "rule": _condition_label(condition),
                "true_bars": int(df.select(expr.sum()).item()),
            }
        )
    return hits


def _condition_label(condition: Condition) -> str:
    """Render a condition as the rule text the agent wrote.

    Args:
        condition: Comparison condition with left, operator, right.

    Returns:
        "<left> <operator> <right>" using each operand's own label.

    """
    return (
        f"{_operand_label(condition.left)} {condition.operator} {_operand_label(condition.right)}"
    )


def _operand_label(operand: Operand) -> str:
    """Render an operand as the name or value it stands for.

    Args:
        operand: Indicator reference, constant, or time operand.

    Returns:
        The column name, the constant's value, "time_of_day", or the HH:MM
        string.

    Raises:
        ValueError: If operand has no value set.

    """
    if operand.indicator is not None:
        return operand.indicator
    if operand.constant is not None:
        return str(operand.constant)
    if operand.time_of_day is not None:
        return "time_of_day"
    if operand.time is not None:
        return operand.time
    msg = "Operand must have one of: indicator, constant, time_of_day, time"
    raise ValueError(msg)
