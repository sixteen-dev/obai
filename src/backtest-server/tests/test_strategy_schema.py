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
    RiskManagement,
    RuleSet,
    StrategyDefinition,
    Universe,
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

    def test_unknown_param_rejected_with_accepted_names(self) -> None:
        """A misspelled param must be named as unsupported, with the accepted set."""
        config = IndicatorConfig(id="sma200", type="SMA", params={"lenght": 200})
        errors = config.validate()
        assert len(errors) == 1
        assert "lenght" in errors[0]
        assert "length" in errors[0]

    def test_param_free_indicator_rejects_any_param(self) -> None:
        """OBV takes no params, so anything supplied is a silent-drop risk."""
        errors = IndicatorConfig(id="obv", type="OBV", params={"length": 20}).validate()
        assert len(errors) == 1
        assert "length" in errors[0]

    def test_second_source_accepted_for_dual_input(self) -> None:
        """BETA/CORREL read `second_source` from params, so it must validate."""
        for ind_type in ["BETA", "CORREL"]:
            config = IndicatorConfig(
                id="dual",
                type=ind_type,
                params={"length": 20, "second_source": "sma_20"},
            )
            assert config.validate() == [], f"{ind_type} rejected second_source"

    def test_ratio_requires_second_source(self) -> None:
        """RATIO has no natural counterpart series, so the caller must name one."""
        errors = IndicatorConfig(id="rel", type="RATIO").validate()

        assert errors == ["Indicator 'rel' (RATIO) requires param 'second_source'"]
        assert (
            IndicatorConfig(id="rel", type="RATIO", params={"second_source": "open"}).validate()
            == []
        )

    def test_natr_accepts_length_and_validates(self) -> None:
        """NATR takes `length`, rejects a zero window, and names its own param."""
        assert IndicatorConfig(id="natr", type="NATR", params={"length": 14}).validate() == []

        too_short = IndicatorConfig(id="natr", type="NATR", params={"length": 0}).validate()
        assert len(too_short) == 1
        assert "must be an integer in [1, 100000]" in too_short[0]

        native_name = IndicatorConfig(id="natr", type="NATR", params={"timeperiod": 14}).validate()
        assert len(native_name) == 1
        assert "timeperiod" in native_name[0]
        assert "length" in native_name[0]

    def test_wilder_natives_accept_a_one_bar_period_and_reject_zero(self) -> None:
        """NATR/PLUS_DI/MINUS_DI read the prior close, so their floor is one bar."""
        for indicator_type in ["NATR", "PLUS_DI", "MINUS_DI"]:
            accepted = IndicatorConfig(id="d", type=indicator_type, params={"length": 1})
            rejected = IndicatorConfig(id="d", type=indicator_type, params={"length": 0})

            assert accepted.validate() == [], indicator_type
            assert len(rejected.validate()) == 1, indicator_type

    def test_window_natives_reject_a_single_bar_window(self) -> None:
        """MAX/MIN/KAMA panic in the native library below a two-bar window."""
        for indicator_type in ["MAX", "MIN", "KAMA"]:
            accepted = IndicatorConfig(id="w", type=indicator_type, params={"length": 2})
            rejected = IndicatorConfig(id="w", type=indicator_type, params={"length": 1})

            assert accepted.validate() == [], indicator_type
            assert len(rejected.validate()) == 1, indicator_type


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

    def test_atr_risk_requires_risk_pct_in_range(self) -> None:
        """A risk budget is the whole method, so it must be present and sane."""
        missing = PositionSizing(method="atr_risk")
        assert missing.validate() == ["method atr_risk requires risk_pct"]

        assert PositionSizing(method="atr_risk", risk_pct=0.0).validate() == [
            "risk_pct must be in (0, 100]; got 0.0"
        ]
        assert PositionSizing(method="atr_risk", risk_pct=101.0).validate() == [
            "risk_pct must be in (0, 100]; got 101.0"
        ]
        assert PositionSizing(method="atr_risk", risk_pct=1.0).validate() == []

    def test_risk_pct_rejected_for_other_methods(self) -> None:
        """A budget no sizing path reads would look honoured and be ignored."""
        ps = PositionSizing(method="equal_weight", risk_pct=1.0)

        assert ps.validate() == ["risk_pct applies only to method atr_risk"]


class TestRiskManagement:
    """Test risk management validation."""

    def test_pct_and_atr_stops_are_mutually_exclusive(self) -> None:
        """Two stops on one position is an ambiguous rule, not a tighter one."""
        risk = RiskManagement(stop_loss_pct=5.0, atr_indicator="atr_a", stop_atr_multiple=2.0)

        errors = risk.validate()

        assert len(errors) == 1
        assert "stop_loss_pct" in errors[0]
        assert "stop_atr_multiple" in errors[0]

    def test_atr_stop_requires_atr_indicator(self) -> None:
        """A multiple with nothing to multiply cannot place a stop."""
        risk = RiskManagement(stop_atr_multiple=2.0)

        assert risk.validate() == ["stop_atr_multiple requires atr_indicator"]

    def test_trailing_variants_are_mutually_exclusive_and_ranged(self) -> None:
        """One trail per position, and a distance that can actually be walked."""
        both = RiskManagement(
            trailing_stop_pct=10.0,
            atr_indicator="atr_a",
            trailing_stop_atr_multiple=2.0,
        )
        assert both.validate() == [
            "trailing_stop_pct and trailing_stop_atr_multiple are mutually exclusive; set one"
        ]

        assert RiskManagement(trailing_stop_pct=0.0).validate() == [
            "trailing_stop_pct must be in (0, 100); got 0.0"
        ]
        assert RiskManagement(trailing_stop_pct=100.0).validate() == [
            "trailing_stop_pct must be in (0, 100); got 100.0"
        ]

        assert RiskManagement(trailing_stop_atr_multiple=2.0).validate() == [
            "trailing_stop_atr_multiple requires atr_indicator"
        ]
        assert (
            RiskManagement(atr_indicator="atr_a", trailing_stop_atr_multiple=2.0).validate() == []
        )

    def test_max_holding_bars_must_be_a_positive_integer(self) -> None:
        """A holding cap is a whole number of bars, and at least one of them."""
        assert RiskManagement(max_holding_bars=0).validate() == [
            "max_holding_bars must be an integer >= 1; got 0"
        ]
        assert RiskManagement(max_holding_bars=-1).validate() == [
            "max_holding_bars must be an integer >= 1; got -1"
        ]
        assert RiskManagement(max_holding_bars=1.5).validate() == [  # type: ignore[arg-type]
            "max_holding_bars must be an integer >= 1; got 1.5"
        ]
        assert RiskManagement(max_holding_bars=True).validate() == [  # type: ignore[arg-type]
            "max_holding_bars must be an integer >= 1; got True"
        ]
        assert RiskManagement(max_holding_bars=3).validate() == []

    def test_reentry_cooldown_bars_must_be_a_positive_integer(self) -> None:
        """A cooldown shorter than one bar would block nothing."""
        assert RiskManagement(reentry_cooldown_bars=0).validate() == [
            "reentry_cooldown_bars must be an integer >= 1; got 0"
        ]
        assert RiskManagement(reentry_cooldown_bars=-1).validate() == [
            "reentry_cooldown_bars must be an integer >= 1; got -1"
        ]
        assert RiskManagement(reentry_cooldown_bars=1.5).validate() == [  # type: ignore[arg-type]
            "reentry_cooldown_bars must be an integer >= 1; got 1.5"
        ]
        assert RiskManagement(reentry_cooldown_bars=True).validate() == [  # type: ignore[arg-type]
            "reentry_cooldown_bars must be an integer >= 1; got True"
        ]
        assert RiskManagement(reentry_cooldown_bars=3).validate() == []

    def test_a_trailing_stop_may_sit_under_a_fixed_one(self) -> None:
        """The two levels combine into one effective stop, so both are allowed."""
        risk = RiskManagement(stop_loss_pct=5.0, trailing_stop_pct=10.0)

        assert risk.validate() == []


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

    def test_from_dict_unknown_indicator_param(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """A misspelled period must fail parsing, not run TA-Lib's 30-bar default."""
        sample_strategy_dict["indicators"] = [
            {"id": "sma200", "type": "SMA", "params": {"lenght": 200}},
        ]
        sample_strategy_dict["entry_rules"]["conditions"] = [
            {
                "left": {"indicator": "sma200"},
                "operator": "greater_than",
                "right": {"constant": 100.0},
            },
        ]
        sample_strategy_dict["exit_rules"]["conditions"] = []
        with pytest.raises(ValueError, match="unsupported param"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_from_dict_duplicate_indicator_ids(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Two indicators sharing an id silently overwrite one another."""
        sample_strategy_dict["indicators"][1]["id"] = "sma_fast"
        sample_strategy_dict["entry_rules"]["conditions"][0]["right"] = {"constant": 100.0}
        sample_strategy_dict["exit_rules"]["conditions"][0]["right"] = {"constant": 100.0}
        with pytest.raises(ValueError, match="Duplicate indicator id"):
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

    def test_source_naming_a_later_indicator_is_rejected(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Compute is one forward pass, so a forward source has no column to read."""
        data = copy.deepcopy(sample_strategy_dict)
        data["indicators"] = [
            {"id": "vol_20d", "type": "STDDEV", "params": {"length": 20}, "source": "ret_1d"},
            {"id": "ret_1d", "type": "ROC", "params": {"length": 1}, "source": "close"},
        ]
        data["entry_rules"] = {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "vol_20d"},
                    "operator": "greater_than",
                    "right": {"constant": 1.0},
                }
            ],
        }
        data["exit_rules"] = {"logic": "OR", "conditions": []}

        with pytest.raises(ValueError, match="declared before it"):
            StrategyDefinition.from_dict(data)

    def test_source_naming_an_earlier_indicator_parses(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Realized volatility is STDDEV over a ROC series declared above it."""
        data = copy.deepcopy(sample_strategy_dict)
        data["indicators"] = [
            {"id": "ret_1d", "type": "ROC", "params": {"length": 1}, "source": "close"},
            {"id": "vol_20d", "type": "STDDEV", "params": {"length": 20}, "source": "ret_1d"},
        ]
        data["entry_rules"] = {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "vol_20d"},
                    "operator": "greater_than",
                    "right": {"constant": 1.0},
                }
            ],
        }
        data["exit_rules"] = {"logic": "OR", "conditions": []}

        strategy = StrategyDefinition.from_dict(data)

        assert strategy.indicators[1].source == "ret_1d"

    def test_second_source_naming_a_later_indicator_is_rejected(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """A dual-input indicator's second series is a source reference too."""
        data = copy.deepcopy(sample_strategy_dict)
        data["indicators"] = [
            {
                "id": "corr",
                "type": "CORREL",
                "params": {"length": 20, "second_source": "sma_fast"},
                "source": "close",
            },
            {"id": "sma_fast", "type": "SMA", "params": {"length": 50}, "source": "close"},
        ]
        data["entry_rules"] = {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "corr"},
                    "operator": "greater_than",
                    "right": {"constant": 0.5},
                }
            ],
        }
        data["exit_rules"] = {"logic": "OR", "conditions": []}

        with pytest.raises(ValueError, match="declared before it"):
            StrategyDefinition.from_dict(data)

    def test_ratio_second_source_must_be_declared_earlier(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """RATIO divides by a named column, so that column must already exist."""
        data = copy.deepcopy(sample_strategy_dict)
        data["indicators"] = [
            {
                "id": "rel",
                "type": "RATIO",
                "params": {"second_source": "sma_slow"},
                "source": "close",
            },
            {"id": "sma_slow", "type": "SMA", "params": {"length": 50}, "source": "close"},
        ]
        data["entry_rules"] = {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "rel"},
                    "operator": "greater_than",
                    "right": {"constant": 1.0},
                }
            ],
        }
        data["exit_rules"] = {"logic": "OR", "conditions": []}

        with pytest.raises(ValueError, match="declared before it"):
            StrategyDefinition.from_dict(data)

    def test_donchian_suffixed_refs_validate(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """The channel emits upper/middle/lower, and only those names resolve."""
        data = copy.deepcopy(sample_strategy_dict)
        data["indicators"] = [{"id": "dc", "type": "DONCHIAN", "params": {"length": 20}}]
        data["entry_rules"] = {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "close"},
                    "operator": "greater_than",
                    "right": {"indicator": "dc_upper"},
                }
            ],
        }
        data["exit_rules"] = {
            "logic": "OR",
            "conditions": [
                {
                    "left": {"indicator": "close"},
                    "operator": "less_than",
                    "right": {"indicator": "dc_lower"},
                }
            ],
        }

        strategy = StrategyDefinition.from_dict(data)
        assert strategy.indicators[0].type == "DONCHIAN"

        data["entry_rules"]["conditions"][0]["right"] = {"indicator": "dc_top"}
        with pytest.raises(ValueError, match="references undefined indicator 'dc_top'"):
            StrategyDefinition.from_dict(data)

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

    def test_bbands_percent_b_ref_validates(self) -> None:
        """A rule on the derived %B column must resolve like any other band."""
        data: dict[str, Any] = {
            "name": "Squeeze Test",
            "universe": {"symbols": ["AAPL"]},
            "data_config": {"start_date": "2023-01-01", "end_date": "2024-01-01"},
            "indicators": [
                {"id": "bb", "type": "BBANDS", "params": {"length": 20, "std_dev": 2}},
            ],
            "entry_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "bb_percent_b"},
                        "operator": "greater_than",
                        "right": {"constant": 1.0},
                    },
                ],
            },
            "exit_rules": {
                "logic": "AND",
                "conditions": [
                    {
                        "left": {"indicator": "bb_bandwidth"},
                        "operator": "less_than",
                        "right": {"constant": 0.05},
                    },
                ],
            },
        }

        strategy = StrategyDefinition.from_dict(data)

        assert strategy.validate() == []

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

    def test_atr_indicator_must_name_a_declared_atr(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """The stop reads a price-unit ATR column, so the id has to be one."""
        sample_strategy_dict["indicators"] = [
            {"id": "rsi_a", "type": "RSI", "params": {"length": 14}},
            {"id": "atr_a", "type": "ATR", "params": {"length": 14}},
        ]
        sample_strategy_dict["entry_rules"]["conditions"] = [
            {"left": {"indicator": "rsi_a"}, "operator": "less_than", "right": {"constant": 30.0}},
        ]
        sample_strategy_dict["exit_rules"]["conditions"] = []
        sample_strategy_dict["risk_management"] = {
            "atr_indicator": "rsi_a",
            "stop_atr_multiple": 2.0,
        }

        with pytest.raises(ValueError, match="must name a declared ATR indicator"):
            StrategyDefinition.from_dict(sample_strategy_dict)

        sample_strategy_dict["risk_management"]["atr_indicator"] = "atr_a"
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)
        assert strategy.validate() == []

    def test_unused_atr_indicator_is_rejected(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Naming an ATR that nothing consumes is a half-written rule."""
        sample_strategy_dict["indicators"] = [
            {"id": "atr_a", "type": "ATR", "params": {"length": 14}},
        ]
        sample_strategy_dict["entry_rules"]["conditions"] = [
            {
                "left": {"indicator": "close"},
                "operator": "greater_than",
                "right": {"constant": 1.0},
            },
        ]
        sample_strategy_dict["exit_rules"]["conditions"] = []
        sample_strategy_dict["risk_management"] = {"atr_indicator": "atr_a"}

        with pytest.raises(ValueError, match="atr_indicator is set but nothing uses it"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_atr_stop_fields_roundtrip(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Both fields survive serialization and move the cache key."""
        sample_strategy_dict["indicators"].append(
            {"id": "atr_a", "type": "ATR", "params": {"length": 14}}
        )
        baseline = StrategyDefinition.from_dict(sample_strategy_dict)

        sample_strategy_dict["risk_management"] = {
            "atr_indicator": "atr_a",
            "stop_atr_multiple": 2.5,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)

        risk = strategy.to_dict()["risk_management"]
        assert risk["atr_indicator"] == "atr_a"
        assert risk["stop_atr_multiple"] == 2.5
        restored = StrategyDefinition.from_dict(strategy.to_dict())
        assert restored.risk_management.atr_indicator == "atr_a"
        assert restored.risk_management.stop_atr_multiple == 2.5
        assert strategy.cache_key() != baseline.cache_key()

    def test_atr_risk_requires_the_atr_stop(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Sizing to a stop the engine does not place makes risk_pct fictional."""
        sample_strategy_dict["position_sizing"]["method"] = "atr_risk"
        sample_strategy_dict["position_sizing"]["risk_pct"] = 1.0

        with pytest.raises(ValueError, match="stop_atr_multiple"):
            StrategyDefinition.from_dict(sample_strategy_dict)

    def test_trailing_stop_fields_roundtrip(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Both trail fields survive serialization and consume the named ATR."""
        sample_strategy_dict["indicators"].append(
            {"id": "atr_a", "type": "ATR", "params": {"length": 14}}
        )
        baseline = StrategyDefinition.from_dict(sample_strategy_dict)

        sample_strategy_dict["risk_management"] = {
            "atr_indicator": "atr_a",
            "trailing_stop_atr_multiple": 3.0,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)

        risk = strategy.to_dict()["risk_management"]
        assert risk["trailing_stop_pct"] is None
        assert risk["trailing_stop_atr_multiple"] == 3.0
        restored = StrategyDefinition.from_dict(strategy.to_dict())
        assert restored.risk_management.trailing_stop_atr_multiple == 3.0
        assert strategy.cache_key() != baseline.cache_key()

    def test_bar_count_limits_roundtrip(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """The holding cap and the cooldown survive serialization."""
        baseline = StrategyDefinition.from_dict(sample_strategy_dict)

        sample_strategy_dict["risk_management"] = {
            "max_holding_bars": 5,
            "reentry_cooldown_bars": 2,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)

        risk = strategy.to_dict()["risk_management"]
        assert risk["max_holding_bars"] == 5
        assert risk["reentry_cooldown_bars"] == 2
        restored = StrategyDefinition.from_dict(strategy.to_dict())
        assert restored.risk_management.max_holding_bars == 5
        assert restored.risk_management.reentry_cooldown_bars == 2
        assert strategy.cache_key() != baseline.cache_key()

    def test_risk_pct_roundtrips(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """The budget survives serialization and moves the cache key."""
        sample_strategy_dict["indicators"].append(
            {"id": "atr_a", "type": "ATR", "params": {"length": 14}}
        )
        baseline = StrategyDefinition.from_dict(sample_strategy_dict)

        sample_strategy_dict["position_sizing"]["method"] = "atr_risk"
        sample_strategy_dict["position_sizing"]["risk_pct"] = 0.75
        sample_strategy_dict["risk_management"] = {
            "atr_indicator": "atr_a",
            "stop_atr_multiple": 2.0,
        }
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)

        assert strategy.to_dict()["position_sizing"]["risk_pct"] == 0.75
        restored = StrategyDefinition.from_dict(strategy.to_dict())
        assert restored.position_sizing.risk_pct == 0.75
        assert strategy.cache_key() != baseline.cache_key()


def test_universe_above_the_cap_is_rejected() -> None:
    """A universe past the cap must name the cap and the way out of it."""
    over_cap = Universe(symbols=[f"S{i}" for i in range(251)])

    assert over_cap.validate() == [
        "Universe has 251 symbols; max supported is 250. "
        "Narrow with the screener before backtesting."
    ]
    assert Universe(symbols=[f"S{i}" for i in range(250)]).validate() == []


def _benchmark_close_strategy(
    sample_strategy_dict: dict[str, Any],
    benchmark: str | None,
) -> dict[str, Any]:
    """Return the sample strategy with one rule reading the benchmark column."""
    data = copy.deepcopy(sample_strategy_dict)
    data["universe"] = {"symbols": ["AAPL"], "benchmark": benchmark}
    data["indicators"] = []
    data["entry_rules"] = {
        "logic": "AND",
        "conditions": [
            {
                "left": {"indicator": "close"},
                "operator": "greater_than",
                "right": {"indicator": "benchmark_close"},
            }
        ],
    }
    data["exit_rules"] = {"logic": "OR", "conditions": []}
    return data


def test_benchmark_close_reference_requires_a_benchmark(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """The reserved column only exists when there is a benchmark to align."""
    with pytest.raises(
        ValueError,
        match="benchmark_close is referenced but universe.benchmark is not set",
    ):
        StrategyDefinition.from_dict(_benchmark_close_strategy(sample_strategy_dict, None))

    strategy = StrategyDefinition.from_dict(_benchmark_close_strategy(sample_strategy_dict, "SPY"))

    assert strategy.references_benchmark_close() is True


def test_an_indicator_may_source_the_benchmark_column(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """Momentum of the benchmark is an indicator over the reserved column."""
    data = _benchmark_close_strategy(sample_strategy_dict, "SPY")
    data["indicators"] = [
        {"id": "roc_bench", "type": "ROC", "params": {"length": 20}, "source": "benchmark_close"}
    ]
    data["entry_rules"]["conditions"][0]["right"] = {"indicator": "roc_bench"}

    strategy = StrategyDefinition.from_dict(data)

    assert strategy.references_benchmark_close() is True
    assert strategy.indicators[0].source == "benchmark_close"


def test_a_strategy_that_ignores_the_benchmark_column_does_not_reference_it(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """Only strategies that name the column pay for aligning it."""
    strategy = StrategyDefinition.from_dict(sample_strategy_dict)

    assert strategy.references_benchmark_close() is False


def _anchored_strategy(
    sample_strategy_dict: dict[str, Any],
    indicator: dict[str, Any],
    timeframe: str = "daily",
) -> dict[str, Any]:
    """Return the sample strategy carrying one session-anchored indicator."""
    data = copy.deepcopy(sample_strategy_dict)
    data["data_config"] = {
        "start_date": "2024-01-02",
        "end_date": "2024-06-28",
        "timeframe": timeframe,
    }
    data["indicators"] = [indicator]
    data["entry_rules"] = {
        "logic": "AND",
        "conditions": [
            {
                "left": {"indicator": "close"},
                "operator": "greater_than",
                "right": {"constant": 0.0},
            }
        ],
    }
    data["exit_rules"] = {"logic": "OR", "conditions": []}
    return data


def test_avwap_anchor_must_fall_inside_the_data_window(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """No history is fetched before start_date, so an earlier anchor has no bars."""
    before_window = _anchored_strategy(
        sample_strategy_dict,
        {"id": "avwap", "type": "AVWAP", "params": {"anchor_date": "2024-01-01"}},
    )

    with pytest.raises(ValueError, match="anchor_date"):
        StrategyDefinition.from_dict(before_window)

    inside_window = _anchored_strategy(
        sample_strategy_dict,
        {"id": "avwap", "type": "AVWAP", "params": {"anchor_date": "2024-02-01"}},
    )

    assert StrategyDefinition.from_dict(inside_window).indicators[0].id == "avwap"


def test_avwap_requires_an_anchor_date(sample_strategy_dict: dict[str, Any]) -> None:
    """The anchor is the whole definition, so there is no default to fall back on."""
    missing = _anchored_strategy(sample_strategy_dict, {"id": "avwap", "type": "AVWAP"})
    malformed = _anchored_strategy(
        sample_strategy_dict,
        {"id": "avwap", "type": "AVWAP", "params": {"anchor_date": "01/02/2024"}},
    )

    with pytest.raises(ValueError, match="anchor_date"):
        StrategyDefinition.from_dict(missing)
    with pytest.raises(ValueError, match="anchor_date"):
        StrategyDefinition.from_dict(malformed)


def test_opening_range_minutes_must_be_a_multiple_of_the_bar_size(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """A 20-minute range on 15-minute bars would silently measure 15 or 30."""
    unaligned = _anchored_strategy(
        sample_strategy_dict,
        {"id": "orb", "type": "OPENING_RANGE", "params": {"minutes": 20}},
        timeframe="15min",
    )

    with pytest.raises(ValueError, match="multiple of the bar size"):
        StrategyDefinition.from_dict(unaligned)

    aligned = _anchored_strategy(
        sample_strategy_dict,
        {"id": "orb", "type": "OPENING_RANGE", "params": {"minutes": 30}},
        timeframe="15min",
    )

    assert StrategyDefinition.from_dict(aligned).indicators[0].params == {"minutes": 30}


def test_opening_range_is_rejected_on_daily_bars(
    sample_strategy_dict: dict[str, Any],
) -> None:
    """Daily bars carry no session clock for the range to open against."""
    daily = _anchored_strategy(
        sample_strategy_dict,
        {"id": "orb", "type": "OPENING_RANGE", "params": {"minutes": 15}},
    )

    with pytest.raises(ValueError, match="requires intraday data"):
        StrategyDefinition.from_dict(daily)
