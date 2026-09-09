"""Tests for the typed indicator catalog and the validation it drives."""

from __future__ import annotations

from src.models.indicator_catalog import CDL_PATTERN_NAMES, INDICATOR_CATALOG, ParamSpec
from src.models.strategy import IndicatorConfig


class TestParamRanges:
    """A param outside TA-Lib's accepted range must be rejected at validation.

    Below the minimum or above the ceiling the plugin panics, the engine turns
    the panic into a "Failed to compute" warning, and the backtest runs on a
    strategy that is missing the indicator it was built around.
    """

    def test_lookback_below_minimum_is_rejected(self) -> None:
        """SMA needs two bars; ATR's true range is defined on one."""
        errors = IndicatorConfig(id="sma", type="SMA", params={"length": 1}).validate()

        assert len(errors) == 1
        assert "[2, 100000]" in errors[0]
        assert IndicatorConfig(id="atr", type="ATR", params={"length": 1}).validate() == []

    def test_lookback_must_be_an_integer(self) -> None:
        """The plugin decodes a period strictly as an integer."""
        for bad in (14.0, True, "14"):
            errors = IndicatorConfig(id="sma", type="SMA", params={"length": bad}).validate()
            assert len(errors) == 1, bad
            assert "must be an integer" in errors[0], bad

        assert IndicatorConfig(id="sma", type="SMA", params={"length": 14}).validate() == []

    def test_lookback_above_talib_limit_is_rejected(self) -> None:
        """Past the ceiling the native library panics rather than computing."""
        errors = IndicatorConfig(id="sma", type="SMA", params={"length": 100_001}).validate()

        assert len(errors) == 1
        assert "100000" in errors[0]

    def test_factor_range(self) -> None:
        """Band widths and acceleration factors carry their own bounds."""
        assert len(IndicatorConfig(id="bb", type="BBANDS", params={"std_dev": 0}).validate()) == 1
        assert IndicatorConfig(id="bb", type="BBANDS", params={"std_dev": 2}).validate() == []
        assert len(IndicatorConfig(id="bb", type="BBANDS", params={"std_dev": "x"}).validate()) == 1

        errors = IndicatorConfig(id="sar", type="SAR", params={"acceleration": -0.1}).validate()

        assert len(errors) == 1
        assert "must be a number in [0.0, 1.0]" in errors[0]

    def test_macd_fast_must_be_shorter_than_slow(self) -> None:
        """TA-Lib silently swaps the periods, inverting what was asked for."""
        errors = IndicatorConfig(
            id="macd", type="MACD", params={"fast_length": 26, "slow_length": 12}
        ).validate()

        assert len(errors) == 1
        assert "fast_length < slow_length" in errors[0]
        assert (
            IndicatorConfig(
                id="macd", type="MACD", params={"fast_length": 12, "slow_length": 26}
            ).validate()
            == []
        )

    def test_source_reference_params_must_name_a_column(self) -> None:
        """`second_source` names another column, so it must be a non-empty name."""
        errors = IndicatorConfig(id="b", type="BETA", params={"second_source": ""}).validate()

        assert len(errors) == 1
        assert "must be a column name" in errors[0]
        good = IndicatorConfig(id="b", type="BETA", params={"second_source": "low"})
        assert good.validate() == []


class TestDefaults:
    """Every default must be a value the same catalog would accept."""

    def test_every_default_is_inside_its_range(self) -> None:
        """A default outside its own bounds would fail the moment it is used."""
        for name, spec in INDICATOR_CATALOG.items():
            for param_name, param in spec.params.items():
                if param.default is None:
                    # Required param: there is no default value to range-check.
                    continue
                assert param.errors(name.lower(), name, param_name, param.default) == []

    def test_every_indicator_has_a_description_and_scale(self) -> None:
        """Discovery output is only useful if every entry is described."""
        for name, spec in INDICATOR_CATALOG.items():
            assert spec.description.endswith("."), name
            assert spec.output_scale, name

    def test_candlestick_patterns_are_catalogued(self) -> None:
        """All 61 pattern names must resolve to OHLC signal entries."""
        assert len(CDL_PATTERN_NAMES) == 61
        for name in CDL_PATTERN_NAMES:
            spec = INDICATOR_CATALOG[name]
            assert spec.inputs == "ohlc"
            assert spec.output_scale == "signal"
            assert spec.params == {}


class TestResolveParams:
    """The engine computes from resolved params, so resolution is the contract."""

    def test_defaults_fill_the_params_the_caller_omitted(self) -> None:
        """An omitted period must resolve to the library's own default."""
        assert INDICATOR_CATALOG["SMA"].resolve_params({}) == {"length": 30}
        assert INDICATOR_CATALOG["MACD"].resolve_params({}) == {
            "fast_length": 12,
            "slow_length": 26,
            "signal_length": 9,
        }

    def test_supplied_values_win_and_unknown_keys_are_dropped(self) -> None:
        """A typo must not reach the plugin as a keyword it does not take."""
        resolved = INDICATOR_CATALOG["BBANDS"].resolve_params({"length": 20, "lenght": 5})

        assert resolved == {"length": 20, "std_dev": 2.0}


class TestSourceRefs:
    """Which frame columns an indicator reads by name, before it is computed."""

    def test_single_source_indicators_reference_their_source(self) -> None:
        """A moving average reads exactly the column it was pointed at."""
        spec = INDICATOR_CATALOG["SMA"]

        assert spec.source_refs("vol_20d", spec.resolve_params({})) == ("vol_20d",)

    def test_dual_input_indicators_reference_both_series(self) -> None:
        """BETA compares two named columns, so both must be resolvable."""
        spec = INDICATOR_CATALOG["BETA"]

        assert spec.source_refs("close", spec.resolve_params({})) == ("close", "high")
        assert spec.source_refs("close", spec.resolve_params({"second_source": "sma"})) == (
            "close",
            "sma",
        )

    def test_price_input_indicators_reference_no_named_column(self) -> None:
        """ATR reads high/low/close directly and ignores `source`."""
        spec = INDICATOR_CATALOG["ATR"]

        assert spec.source_refs("close", spec.resolve_params({})) == ()


class TestParamSpecErrors:
    """The reusable value checks behind every indicator's param validation."""

    def test_a_required_param_reports_no_default(self) -> None:
        """A None default marks the param as caller-supplied."""
        param = ParamSpec(kind="source_ref", default=None)

        assert param.default is None
        assert param.errors("x", "TYPE", "ref", "close") == []
        assert len(param.errors("x", "TYPE", "ref", 3)) == 1

    def test_a_factor_rejects_non_finite_values(self) -> None:
        """An infinite multiplier produces an untradeable band, not a wide one."""
        param = ParamSpec(kind="factor", default=2.0, min=0.1, max=10.0)

        assert param.errors("x", "TYPE", "std_dev", float("inf")) != []
        assert param.errors("x", "TYPE", "std_dev", float("nan")) != []
        assert param.errors("x", "TYPE", "std_dev", 0.1) == []
