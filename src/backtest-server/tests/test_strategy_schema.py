"""Tests for strategy definition schema validation and serialization."""

from __future__ import annotations

import copy
import datetime
from typing import Any

import pytest

from src.models.strategy import (
    Condition,
    DataConfig,
    IndicatorConfig,
    Operand,
    PositionSizing,
    RuleSet,
    StrategyDefinition,
)


class TestIndicatorConfig:
    """Test indicator config validation."""

    def test_valid_indicator(self) -> None:
        """Supported indicator type should have no errors."""
        config = IndicatorConfig(id="sma_50", type="SMA", params={"length": 50})
        assert config.validate() == []

    def test_unsupported_indicator(self) -> None:
        """Unsupported indicator type should produce an error."""
        config = IndicatorConfig(id="bad", type="NONEXISTENT")
        errors = config.validate()
        assert len(errors) == 1
        assert "Unsupported indicator" in errors[0]

    def test_empty_id(self) -> None:
        """Empty indicator id should produce an error."""
        config = IndicatorConfig(id="", type="SMA")
        errors = config.validate()
        assert any("id cannot be empty" in e for e in errors)

    def test_case_insensitive_type(self) -> None:
        """Indicator type validation should be case-insensitive."""
        config = IndicatorConfig(id="rsi", type="rsi")
        assert config.validate() == []


class TestOperand:
    """Test operand validation."""

    def test_indicator_operand(self) -> None:
        """Indicator operand should be valid."""
        op = Operand(indicator="sma_50")
        assert op.validate() == []

    def test_constant_operand(self) -> None:
        """Constant operand should be valid."""
        op = Operand(constant=70.0)
        assert op.validate() == []

    def test_empty_operand(self) -> None:
        """Operand with no value should error."""
        op = Operand()
        errors = op.validate()
        assert len(errors) == 1
        assert "must have one of" in errors[0]

    def test_both_operand(self) -> None:
        """Operand with multiple values should error."""
        op = Operand(indicator="sma", constant=50.0)
        errors = op.validate()
        assert len(errors) == 1
        assert "exactly one" in errors[0]


class TestCondition:
    """Test condition validation."""

    def test_valid_condition(self) -> None:
        """Valid condition should have no errors."""
        cond = Condition(
            left=Operand(indicator="sma_fast"),
            operator="crosses_above",
            right=Operand(indicator="sma_slow"),
        )
        assert cond.validate() == []

    def test_unsupported_operator(self) -> None:
        """Unsupported operator should produce an error."""
        cond = Condition(
            left=Operand(indicator="sma"),
            operator="explodes_above",
            right=Operand(constant=50.0),
        )
        errors = cond.validate()
        assert any("Unsupported operator" in e for e in errors)


class TestRuleSet:
    """Test ruleset validation."""

    def test_valid_ruleset(self) -> None:
        """Valid AND ruleset should have no errors."""
        rs = RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="rsi"),
                    operator="less_than",
                    right=Operand(constant=30.0),
                ),
            ],
        )
        assert rs.validate() == []

    def test_invalid_logic(self) -> None:
        """Invalid logic should produce an error."""
        rs = RuleSet(logic="XOR", conditions=[])
        errors = rs.validate()
        assert any("'AND' or 'OR'" in e for e in errors)


class TestPositionSizing:
    """Test position sizing validation."""

    def test_valid_sizing(self) -> None:
        """Valid sizing method should have no errors."""
        ps = PositionSizing(method="equal_weight")
        assert ps.validate() == []

    def test_invalid_sizing(self) -> None:
        """Invalid sizing method should produce an error."""
        ps = PositionSizing(method="yolo_all_in")
        errors = ps.validate()
        assert any("Unsupported sizing method" in e for e in errors)


class TestDataConfig:
    """Test data config date handling."""

    def test_explicit_train_end(self) -> None:
        """Explicit train_end_date should be used."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="2022-06-30",
        )
        assert dc.get_train_end() == datetime.date(2022, 6, 30)

    def test_default_train_end(self) -> None:
        """Default should be 75% of date range."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-01-01",
        )
        train_end = dc.get_train_end()
        start = datetime.date(2020, 1, 1)
        end = datetime.date(2024, 1, 1)
        expected_days = int((end - start).days * 0.75)
        assert train_end == start + datetime.timedelta(days=expected_days)


class TestDataConfigValidation:
    """Test data config validation rules."""

    def test_valid_dates(self) -> None:
        """Valid date range should have no errors."""
        dc = DataConfig(start_date="2020-01-01", end_date="2024-12-31")
        assert dc.validate() == []

    def test_invalid_start_date_format(self) -> None:
        """Malformed start_date should produce an error."""
        dc = DataConfig(start_date="not-a-date", end_date="2024-12-31")
        errors = dc.validate()
        assert any("Invalid start_date" in e for e in errors)

    def test_invalid_end_date_format(self) -> None:
        """Malformed end_date should produce an error."""
        dc = DataConfig(start_date="2020-01-01", end_date="garbage")
        errors = dc.validate()
        assert any("Invalid end_date" in e for e in errors)

    def test_start_after_end(self) -> None:
        """Start >= end should produce an error."""
        dc = DataConfig(start_date="2024-01-01", end_date="2020-01-01")
        errors = dc.validate()
        assert any("must be before" in e for e in errors)

    def test_range_too_short(self) -> None:
        """Date range under 30 days should produce an error."""
        dc = DataConfig(start_date="2024-01-01", end_date="2024-01-15")
        errors = dc.validate()
        assert any("at least" in e for e in errors)

    def test_valid_train_end(self) -> None:
        """Train end between start and end should pass."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="2022-06-15",
        )
        assert dc.validate() == []

    def test_train_end_before_start(self) -> None:
        """Train end before start should error."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="2019-06-15",
        )
        errors = dc.validate()
        assert any("between start_date and end_date" in e for e in errors)

    def test_train_end_after_end(self) -> None:
        """Train end after end_date should error."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="2025-06-15",
        )
        errors = dc.validate()
        assert any("between start_date and end_date" in e for e in errors)

    def test_invalid_train_end_format(self) -> None:
        """Malformed train_end_date should produce an error."""
        dc = DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="bad-date",
        )
        errors = dc.validate()
        assert any("Invalid train_end_date" in e for e in errors)


class TestStrategyDefinition:
    """Test complete strategy validation and serialization."""

    def test_from_dict_valid(self, sample_strategy_dict: dict[str, Any]) -> None:
        """Valid dict should parse without errors."""
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        assert strategy.name == "Test SMA Crossover"
        assert strategy.universe.symbols == ["AAPL", "MSFT"]
        assert len(strategy.indicators) == 2
        assert strategy.risk_management.stop_loss_pct == 5.0

    def test_from_dict_invalid_indicator(self, sample_strategy_dict: dict[str, Any]) -> None:
        """Invalid indicator should raise ValueError."""
        sample_strategy_dict["indicators"][0]["type"] = "NONEXISTENT"
        with pytest.raises(ValueError, match="Invalid strategy"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_from_dict_empty_name(self, sample_strategy_dict: dict[str, Any]) -> None:
        """Empty name should raise ValueError."""
        sample_strategy_dict["name"] = ""
        with pytest.raises(ValueError, match="name cannot be empty"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_from_dict_empty_symbols(self, sample_strategy_dict: dict[str, Any]) -> None:
        """Empty symbols should raise ValueError."""
        sample_strategy_dict["universe"]["symbols"] = []
        with pytest.raises(ValueError, match="at least one symbol"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_roundtrip(self, sample_strategy_dict: dict[str, Any]) -> None:
        """from_dict -> to_dict should produce equivalent result."""
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        serialized = strategy.to_dict()

        # to_dict sorts symbols, so normalize input
        assert serialized["universe"]["symbols"] == sorted(["AAPL", "MSFT"])
        assert serialized["data_config"]["start_date"] == "2020-01-01"
        assert len(serialized["indicators"]) == 2

    def test_execution_config_new_fields_roundtrip(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """New execution config fields should survive round-trip."""
        sample_strategy_dict["execution_config"] = {
            "slippage_pct": 0.15,
            "commission_pct": 0.2,
            "initial_capital": 50_000.0,
            "volume_scaled_slippage": True,
            "estimate_spread": True,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        assert strategy.execution_config.volume_scaled_slippage is True
        assert strategy.execution_config.estimate_spread is True

        serialized = strategy.to_dict()
        ec = serialized["execution_config"]
        assert ec["volume_scaled_slippage"] is True
        assert ec["estimate_spread"] is True
        assert ec["slippage_pct"] == 0.15

    def test_execution_config_defaults_for_missing_fields(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Missing new fields should default to False."""
        sample_strategy_dict["execution_config"] = {
            "slippage_pct": 0.1,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        assert strategy.execution_config.volume_scaled_slippage is False
        assert strategy.execution_config.estimate_spread is False

    def test_cache_key_deterministic(self, sample_strategy_dict: dict[str, Any]) -> None:
        """cache_key should be deterministic for same input."""
        s1 = StrategyDefinition.from_dict(sample_strategy_dict)
        s2 = StrategyDefinition.from_dict(sample_strategy_dict)
        assert s1.cache_key() == s2.cache_key()

    def test_cache_key_changes_with_params(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Different params should produce different cache keys."""
        dict1 = copy.deepcopy(sample_strategy_dict)
        dict2 = copy.deepcopy(sample_strategy_dict)
        dict2["indicators"][0]["params"]["length"] = 100

        s1 = StrategyDefinition.from_dict(dict1)
        s2 = StrategyDefinition.from_dict(dict2)

        assert s1.cache_key() != s2.cache_key()

    def test_undefined_indicator_reference(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Entry rule referencing undefined indicator should raise."""
        data = copy.deepcopy(sample_strategy_dict)
        data["entry_rules"]["conditions"][0]["left"]["indicator"] = "nonexistent"
        with pytest.raises(ValueError, match="undefined indicator"):
            StrategyDefinition.from_dict(data)

    def test_undefined_exit_indicator_reference(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Exit rule referencing undefined indicator should raise."""
        data = copy.deepcopy(sample_strategy_dict)
        data["exit_rules"]["conditions"][0]["left"]["indicator"] = "ghost"
        with pytest.raises(ValueError, match="undefined indicator"):
            StrategyDefinition.from_dict(data)

    def test_constant_operand_skips_ref_check(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Constant operands should not trigger ref validation."""
        # The sample dict already has a constant operand in entry rules (rsi < 70)
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        assert strategy.name == "Test SMA Crossover"

    def test_multi_output_suffixed_refs_valid(self) -> None:
        """Rules referencing {id}_{suffix} for multi-output indicators should pass."""
        data: dict[str, Any] = {
            "name": "MACD Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {"start_date": "2023-01-01", "end_date": "2024-01-01"},
            "indicators": [
                {
                    "id": "macd",
                    "type": "MACD",
                    "params": {
                        "fast_length": 12,
                        "slow_length": 26,
                        "signal_length": 9,
                    },
                },
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "macd_macd"},
                        "operator": "crosses_above",
                        "right": {"indicator": "macd_signal"},
                    },
                ],
            },
            "exit_rules": {
                "logic": "OR",
                "conditions": [
                    {
                        "left": {"indicator": "macd_macd"},
                        "operator": "crosses_below",
                        "right": {"indicator": "macd_signal"},
                    },
                ],
            },
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.name == "MACD Test"

    def test_multi_output_bare_id_still_valid(self) -> None:
        """Bare indicator id should still be valid for multi-output indicators."""
        data: dict[str, Any] = {
            "name": "BB Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {"start_date": "2023-01-01", "end_date": "2024-01-01"},
            "indicators": [
                {"id": "bb", "type": "BBANDS", "params": {"length": 20, "std_dev": 2}},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "bb"},
                        "operator": "greater_than",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {
                "logic": "OR",
                "conditions": [
                    {
                        "left": {"indicator": "bb_upper"},
                        "operator": "less_than",
                        "right": {"constant": 200.0},
                    },
                ],
            },
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.name == "BB Test"

    def test_invalid_dates_rejected(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Invalid date range should raise ValueError."""
        data = copy.deepcopy(sample_strategy_dict)
        data["data_config"]["start_date"] = "2024-01-01"
        data["data_config"]["end_date"] = "2024-01-10"
        data["data_config"]["train_end_date"] = None
        with pytest.raises(ValueError, match="at least"):
            StrategyDefinition.from_dict(data)

    def test_vwap_daily_timeframe_rejected(self) -> None:
        """VWAP with daily timeframe should fail validation."""
        data: dict[str, Any] = {
            "name": "VWAP Daily Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "timeframe": "daily",
            },
            "indicators": [
                {"id": "vwap", "type": "VWAP"},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "vwap"},
                        "operator": "greater_than",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
        }
        with pytest.raises(ValueError, match="intraday data"):
            StrategyDefinition.from_dict(data)

    def test_vwap_intraday_timeframe_accepted(self) -> None:
        """VWAP with intraday timeframe should pass validation."""
        data: dict[str, Any] = {
            "name": "VWAP Intraday Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "timeframe": "5min",
            },
            "indicators": [
                {"id": "vwap", "type": "VWAP"},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "vwap"},
                        "operator": "greater_than",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.name == "VWAP Intraday Test"

    def test_equals_operator_accepted(self) -> None:
        """Equals operator should pass validation."""
        data: dict[str, Any] = {
            "name": "CDL Equals Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {"start_date": "2023-01-01", "end_date": "2024-01-01"},
            "indicators": [
                {"id": "engulf", "type": "CDL_ENGULFING"},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "engulf"},
                        "operator": "equals",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.name == "CDL Equals Test"

    def test_not_equals_operator_accepted(self) -> None:
        """not_equals operator should pass validation."""
        data: dict[str, Any] = {
            "name": "CDL Not Equals Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {"start_date": "2023-01-01", "end_date": "2024-01-01"},
            "indicators": [
                {"id": "doji", "type": "CDL_DOJI"},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "doji"},
                        "operator": "not_equals",
                        "right": {"constant": 0.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.name == "CDL Not Equals Test"

    def test_candlestick_indicator_accepted(self) -> None:
        """CDL_* indicators should pass validation."""
        config = IndicatorConfig(id="cdl_test", type="CDL_HAMMER")
        assert config.validate() == []

    def test_statistical_indicator_accepted(self) -> None:
        """Statistical indicators should pass validation."""
        for ind_type in ["LINEARREG", "LINEARREG_SLOPE", "LINEARREG_ANGLE", "STDDEV"]:
            config = IndicatorConfig(id="stat_test", type=ind_type, params={"length": 14})
            assert config.validate() == [], f"{ind_type} failed validation"

    def test_beta_correl_indicator_accepted(self) -> None:
        """BETA and CORREL should pass validation."""
        for ind_type in ["BETA", "CORREL"]:
            config = IndicatorConfig(id="dual_test", type=ind_type, params={"length": 20})
            assert config.validate() == [], f"{ind_type} failed validation"

    def test_portfolio_mode_rejects_intraday_timeframe(self) -> None:
        """Portfolio allocation_mode with intraday timeframe should raise ValueError."""
        data: dict[str, Any] = {
            "name": "Portfolio Intraday Test",
            "universe": {"symbols": ["AAPL", "MSFT"]},
            "data_config": {
                "start_date": "2024-01-01",
                "end_date": "2025-01-01",
                "timeframe": "5min",
            },
            "indicators": [
                {"id": "sma", "type": "SMA", "params": {"length": 20}},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "sma"},
                        "operator": "greater_than",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
            "position_sizing": {
                "method": "equal_weight",
                "max_position_pct": 20.0,
                "max_positions": 5,
                "allocation_mode": "portfolio",
            },
        }
        with pytest.raises(ValueError, match="Portfolio allocation mode requires daily"):
            StrategyDefinition.from_dict(data)

    def test_portfolio_mode_accepts_daily_timeframe(self) -> None:
        """Portfolio allocation_mode with daily timeframe should pass validation."""
        data: dict[str, Any] = {
            "name": "Portfolio Daily Test",
            "universe": {"symbols": ["AAPL", "MSFT"]},
            "data_config": {
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "timeframe": "daily",
            },
            "indicators": [
                {"id": "sma", "type": "SMA", "params": {"length": 20}},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "sma"},
                        "operator": "greater_than",
                        "right": {"constant": 100.0},
                    },
                ],
            },
            "exit_rules": {"logic": "OR", "conditions": []},
            "position_sizing": {
                "method": "equal_weight",
                "max_position_pct": 20.0,
                "max_positions": 5,
                "allocation_mode": "portfolio",
            },
        }
        strategy = StrategyDefinition.from_dict(data)
        assert strategy.position_sizing.allocation_mode == "portfolio"
