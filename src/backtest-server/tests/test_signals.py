"""Tests for signal generation engine."""

from __future__ import annotations

import polars as pl
import pytest

from src.engine.signals import generate_signals
from src.models.strategy import Condition, Operand, RuleSet


class TestGenerateSignals:
    """Test signal generation from rules."""

    def test_greater_than_signal(self) -> None:
        """greater_than should generate correct boolean column."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4, 5],
                "rsi": [25.0, 35.0, 65.0, 75.0, 45.0],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="greater_than",
                    right=Operand(constant=50.0),
                ),
            ],
        )
        exit_rules = RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="less_than",
                    right=Operand(constant=30.0),
                ),
            ],
        )
        result = generate_signals(df, entry, exit_rules)

        assert "entry_signal" in result.columns
        assert "exit_signal" in result.columns

        entries = result["entry_signal"].to_list()
        # rsi > 50: [False, False, True, True, False]
        assert entries == [False, False, True, True, False]

        exits = result["exit_signal"].to_list()
        # rsi < 30: [True, False, False, False, False]
        assert exits == [True, False, False, False, False]

    def test_crosses_above_signal(self) -> None:
        """crosses_above should detect crossover points."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4, 5],
                "fast": [10.0, 15.0, 25.0, 30.0, 20.0],
                "slow": [20.0, 20.0, 20.0, 20.0, 20.0],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="fast"),
                    operator="crosses_above",
                    right=Operand(indicator="slow"),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        entries = result["entry_signal"].to_list()

        # fast crosses above slow: prev fast < prev slow AND current fast > slow
        # At idx 2: prev fast(15) < prev slow(20) AND fast(25) > slow(20) → True
        assert entries[2] is True
        # At idx 0: no prev → shift produces null, AND with bool → False in Polars
        assert not entries[0]
        # At idx 3: prev fast(25) > prev slow(20), so NOT crosses_above
        assert entries[3] is not True

    def test_crosses_below_signal(self) -> None:
        """crosses_below should detect downward crossover."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4, 5],
                "fast": [25.0, 22.0, 15.0, 10.0, 25.0],
                "slow": [20.0, 20.0, 20.0, 20.0, 20.0],
            }
        )
        entry = RuleSet(logic="AND", conditions=[])
        exit_rules = RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator="fast"),
                    operator="crosses_below",
                    right=Operand(indicator="slow"),
                ),
            ],
        )

        result = generate_signals(df, entry, exit_rules)
        exits = result["exit_signal"].to_list()

        # crosses_below = (prev_left > prev_right) & (left < right)
        # idx 0: shift produces null, AND with bool → False in Polars
        assert not exits[0]
        # idx 2: prev fast(22) > prev slow(20) AND fast(15) < slow(20) → True
        assert exits[2] is True
        # idx 4: prev fast(10) > prev slow(20) → False → no crossover
        assert exits[4] is not True

    def test_and_logic_combines_conditions(self) -> None:
        """AND logic should require all conditions to be true."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4],
                "rsi": [25.0, 55.0, 75.0, 55.0],
                "sma": [100.0, 105.0, 110.0, 95.0],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="greater_than",
                    right=Operand(constant=50.0),
                ),
                Condition(
                    left=Operand(indicator="sma"),
                    operator="greater_than",
                    right=Operand(constant=100.0),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        entries = result["entry_signal"].to_list()

        # Both rsi > 50 AND sma > 100
        # idx 0: rsi 25 > 50? No → False
        # idx 1: rsi 55 > 50 AND sma 105 > 100 → True
        # idx 2: rsi 75 > 50 AND sma 110 > 100 → True
        # idx 3: rsi 55 > 50 AND sma 95 > 100? No → False
        assert entries == [False, True, True, False]

    def test_or_logic_combines_conditions(self) -> None:
        """OR logic should require any condition to be true."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4],
                "rsi": [25.0, 55.0, 75.0, 45.0],
                "sma": [100.0, 95.0, 110.0, 95.0],
            }
        )
        exit_rules = RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="greater_than",
                    right=Operand(constant=70.0),
                ),
                Condition(
                    left=Operand(indicator="sma"),
                    operator="less_than",
                    right=Operand(constant=96.0),
                ),
            ],
        )
        entry = RuleSet(logic="AND", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        exits = result["exit_signal"].to_list()

        # rsi > 70 OR sma < 96
        # idx 0: rsi 25 > 70? No. sma 100 < 96? No → False
        # idx 1: rsi 55 > 70? No. sma 95 < 96? Yes → True
        # idx 2: rsi 75 > 70? Yes → True
        # idx 3: rsi 45 > 70? No. sma 95 < 96? Yes → True
        assert exits == [False, True, True, True]

    def test_empty_conditions_all_false(self) -> None:
        """Empty conditions should produce all-false signals."""
        df = pl.DataFrame({"date": [1, 2, 3]})
        entry = RuleSet(logic="AND", conditions=[])
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        assert all(not v for v in result["entry_signal"].to_list())
        assert all(not v for v in result["exit_signal"].to_list())

    def test_unsupported_operator_raises(self) -> None:
        """Unsupported operator should raise ValueError."""
        df = pl.DataFrame({"date": [1, 2], "rsi": [50.0, 60.0]})
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="explodes_above",
                    right=Operand(constant=50.0),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        with pytest.raises(ValueError, match="Unsupported operator"):
            generate_signals(df, entry, exit_rules)

    def test_operand_without_value_raises(self) -> None:
        """Operand with no indicator or constant should raise ValueError."""
        df = pl.DataFrame({"date": [1, 2], "rsi": [50.0, 60.0]})
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(),  # No value
                    operator="greater_than",
                    right=Operand(constant=50.0),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        with pytest.raises(ValueError, match="must have one of"):
            generate_signals(df, entry, exit_rules)

    def test_equals_operator(self) -> None:
        """Equals should produce True when values match exactly."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4, 5],
                "cdl_signal": [0, 100, -100, 0, 100],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="cdl_signal"),
                    operator="equals",
                    right=Operand(constant=100.0),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        entries = result["entry_signal"].to_list()
        # cdl_signal == 100: [False, True, False, False, True]
        assert entries == [False, True, False, False, True]

    def test_not_equals_operator(self) -> None:
        """not_equals should produce True when values differ."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3, 4, 5],
                "cdl_signal": [0, 100, -100, 0, 100],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="cdl_signal"),
                    operator="not_equals",
                    right=Operand(constant=0.0),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        entries = result["entry_signal"].to_list()
        # cdl_signal != 0: [False, True, True, False, True]
        assert entries == [False, True, True, False, True]

    def test_equals_with_indicator_comparison(self) -> None:
        """Equals should work between two indicator columns."""
        df = pl.DataFrame(
            {
                "date": [1, 2, 3],
                "col_a": [10.0, 20.0, 30.0],
                "col_b": [10.0, 25.0, 30.0],
            }
        )
        entry = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="col_a"),
                    operator="equals",
                    right=Operand(indicator="col_b"),
                ),
            ],
        )
        exit_rules = RuleSet(logic="OR", conditions=[])

        result = generate_signals(df, entry, exit_rules)
        entries = result["entry_signal"].to_list()
        assert entries == [True, False, True]
