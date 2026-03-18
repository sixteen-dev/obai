"""Signal generation engine — converts rules into entry/exit signals."""

from __future__ import annotations

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

    return df.with_columns(
        entry_expr.alias("entry_signal"),
        exit_expr.alias("exit_signal"),
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

    if condition.operator == "greater_than":
        return left > right
    if condition.operator == "less_than":
        return left < right
    if condition.operator == "crosses_above":
        return (left.shift(1) < right.shift(1)) & (left > right)
    if condition.operator == "crosses_below":
        return (left.shift(1) > right.shift(1)) & (left < right)
    msg = f"Unsupported operator: {condition.operator}"
    raise ValueError(msg)


def _resolve_operand(operand: Operand) -> pl.Expr:
    """Resolve an operand to a Polars expression.

    Args:
        operand: Either an indicator reference or a constant value.

    Returns:
        Polars expression (column reference or literal).

    Raises:
        ValueError: If operand has no value set.

    """
    if operand.indicator is not None:
        return pl.col(operand.indicator)
    if operand.constant is not None:
        return pl.lit(operand.constant)
    msg = "Operand must have either 'indicator' or 'constant'"
    raise ValueError(msg)
