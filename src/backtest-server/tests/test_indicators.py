"""Tests for indicator computation engine."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from src.engine.indicators import compute_indicators, get_supported_indicators
from src.models.strategy import (
    Condition,
    DataConfig,
    IndicatorConfig,
    Operand,
    RuleSet,
    StrategyDefinition,
    Universe,
)
from src.server import _prepare_symbol_signals


class TestComputeIndicators:
    """Test indicator computation via polars-talib."""

    def test_sma_computation(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """SMA should produce a column with correct length."""
        configs = [IndicatorConfig(id="sma_20", type="SMA", params={"length": 20})]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "sma_20" in result.columns
        assert len(result) == len(sample_ohlcv_df)
        assert not warnings

    def test_ema_computation(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """EMA should produce a valid column."""
        configs = [IndicatorConfig(id="ema_12", type="EMA", params={"length": 12})]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "ema_12" in result.columns
        assert not warnings

    def test_rsi_computation(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """RSI should produce values between 0 and 100."""
        configs = [IndicatorConfig(id="rsi_14", type="RSI", params={"length": 14})]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "rsi_14" in result.columns
        non_null = result.filter(pl.col("rsi_14").is_not_null())
        assert non_null["rsi_14"].min() >= 0  # type: ignore[operator]
        assert non_null["rsi_14"].max() <= 100  # type: ignore[operator]

    def test_macd_multi_output(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """MACD should produce three output columns."""
        configs = [
            IndicatorConfig(
                id="macd",
                type="MACD",
                params={"fast_length": 12, "slow_length": 26, "signal_length": 9},
            ),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "macd_macd" in result.columns
        assert "macd_signal" in result.columns
        assert "macd_hist" in result.columns

    def test_bbands_multi_output(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """Bollinger Bands should produce upper, middle, lower columns."""
        configs = [
            IndicatorConfig(
                id="bb",
                type="BBANDS",
                params={"length": 20, "std_dev": 2},
            ),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "bb_upper" in result.columns
        assert "bb_middle" in result.columns
        assert "bb_lower" in result.columns

    def test_atr_hlc_indicator(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """ATR should work with high/low/close columns."""
        configs = [IndicatorConfig(id="atr_14", type="ATR", params={"length": 14})]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "atr_14" in result.columns
        assert not warnings

    def test_obv_volume_indicator(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """OBV should use price and volume."""
        configs = [IndicatorConfig(id="obv", type="OBV")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "obv" in result.columns
        assert not warnings

    def test_unsupported_indicator_warning(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """Unsupported indicator should produce a warning but not crash."""
        configs = [IndicatorConfig(id="fake", type="NONEXISTENT")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "fake" not in result.columns
        assert len(warnings) == 1
        assert "unsupported" in warnings[0].lower()

    def test_multiple_indicators(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """Multiple indicators should all be computed."""
        configs = [
            IndicatorConfig(id="sma_10", type="SMA", params={"length": 10}),
            IndicatorConfig(id="rsi_14", type="RSI", params={"length": 14}),
            IndicatorConfig(id="ema_20", type="EMA", params={"length": 20}),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "sma_10" in result.columns
        assert "rsi_14" in result.columns
        assert "ema_20" in result.columns
        assert not warnings

    def test_original_df_unchanged(self, sample_ohlcv_df: pl.DataFrame) -> None:
        """compute_indicators should not modify the original DataFrame."""
        original_cols = set(sample_ohlcv_df.columns)
        configs = [IndicatorConfig(id="sma_10", type="SMA", params={"length": 10})]
        compute_indicators(sample_ohlcv_df, configs)

        assert set(sample_ohlcv_df.columns) == original_cols


class TestGetSupportedIndicators:
    """Test indicator registry introspection."""

    def test_returns_all_indicators(self) -> None:
        """Should return entries for all supported indicators."""
        result = get_supported_indicators()
        supported = result["indicators"]
        assert "SMA" in supported
        assert "RSI" in supported
        assert "MACD" in supported
        assert "BBANDS" in supported
        assert "ATR" in supported

    def test_indicator_info_structure(self) -> None:
        """Each indicator should have expected info fields."""
        result = get_supported_indicators()
        for name, info in result["indicators"].items():
            assert "params" in info, f"{name} missing params"
            assert "multi_output" in info, f"{name} missing multi_output"
            assert "needs_hlc" in info, f"{name} missing needs_hlc"
            assert "needs_volume" in info, f"{name} missing needs_volume"

    def test_macd_is_multi_output(self) -> None:
        """MACD should be marked as multi-output."""
        supported = get_supported_indicators()["indicators"]
        assert supported["MACD"]["multi_output"] is True
        assert supported["MACD"]["output_columns"] == ["macd", "signal", "hist"]

    def test_sma_is_single_output(self) -> None:
        """SMA should be marked as single-output."""
        supported = get_supported_indicators()["indicators"]
        assert supported["SMA"]["multi_output"] is False

    def test_candlestick_patterns_registered(self) -> None:
        """Candlestick patterns should be in the supported registry."""
        supported = get_supported_indicators()["indicators"]
        assert "CDL_DOJI" in supported
        assert "CDL_ENGULFING" in supported
        assert "CDL_HAMMER" in supported
        assert supported["CDL_DOJI"]["output_scale"] == "signal"
        assert supported["CDL_DOJI"]["needs_ohlc"] is True

    def test_statistical_indicators_registered(self) -> None:
        """Statistical indicators should be in the registry."""
        supported = get_supported_indicators()["indicators"]
        for name in ["LINEARREG", "LINEARREG_SLOPE", "LINEARREG_ANGLE", "STDDEV"]:
            assert name in supported, f"{name} missing"

    def test_dual_input_indicators_registered(self) -> None:
        """BETA and CORREL should be marked as dual-input."""
        supported = get_supported_indicators()["indicators"]
        assert supported["BETA"]["dual_input"] is True
        assert supported["CORREL"]["dual_input"] is True

    def test_vwap_marked_intraday_only(self) -> None:
        """VWAP should be marked as intraday-only."""
        supported = get_supported_indicators()["indicators"]
        assert supported["VWAP"]["intraday_only"] is True

    def test_raw_columns_included(self) -> None:
        """Raw OHLCV columns should be listed as valid operand references."""
        result = get_supported_indicators()
        assert "raw_columns" in result
        raw = result["raw_columns"]
        assert "close" in raw
        assert "open" in raw
        assert "high" in raw
        assert "low" in raw
        assert "volume" in raw

    def test_chaining_a_prior_indicator_as_source_is_disclosed(self) -> None:
        """The registry must say `source` accepts an earlier indicator's id.

        Indicators compute in order into one frame, so an indicator can be
        built on another - realized volatility is STDDEV over a ROC series.
        Nothing announced that, and an agent asked for exactly that filter
        declared it unrepresentable, dropped it, and backtested half the
        requested rule. `second_source` documents the same capability for
        dual-input indicators; plain `source` never did.
        """
        result = get_supported_indicators()

        assert "source_note" in result
        note = result["source_note"]
        assert "id" in note
        assert "order" in note


class TestIndicatorChaining:
    """An indicator may source the column another indicator produced."""

    def _series(self, n: int = 400) -> pl.DataFrame:
        closes = [100.0 + (i % 17) * 0.5 for i in range(n)]
        return pl.DataFrame(
            {
                "date": [date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
                "open": closes,
                "high": [c + 1.0 for c in closes],
                "low": [c - 1.0 for c in closes],
                "close": closes,
                "volume": [1_000_000] * n,
            }
        )

    def test_indicator_can_source_an_earlier_indicator(self) -> None:
        """Realized volatility: STDDEV over a ROC series, no warnings."""
        result, warnings = compute_indicators(
            self._series(),
            [
                IndicatorConfig(id="ret_1d", type="ROC", params={"length": 1}, source="close"),
                IndicatorConfig(
                    id="vol_20d", type="STDDEV", params={"length": 20}, source="ret_1d"
                ),
            ],
        )

        assert warnings == []
        assert "vol_20d" in result.columns
        assert result["vol_20d"].drop_nulls().len() > 0

    def test_an_adaptive_reference_chains_three_deep(self) -> None:
        """A volatility series can carry its own rolling reference level.

        This is what makes a threshold adaptive rather than a tuned constant,
        and it is the capability whose absence was wrongly assumed.
        """
        result, warnings = compute_indicators(
            self._series(),
            [
                IndicatorConfig(id="ret_1d", type="ROC", params={"length": 1}, source="close"),
                IndicatorConfig(
                    id="vol_20d", type="STDDEV", params={"length": 20}, source="ret_1d"
                ),
                IndicatorConfig(id="vol_ref", type="SMA", params={"length": 100}, source="vol_20d"),
            ],
        )

        assert warnings == []
        assert result["vol_ref"].drop_nulls().len() > 0

    def test_sourcing_an_indicator_declared_later_is_reported(self) -> None:
        """Order matters, and getting it wrong must not pass silently.

        Compute is a single forward pass, so a forward reference has no column
        to read. That has to surface as a warning rather than a missing filter
        the backtest then runs without.
        """
        _, warnings = compute_indicators(
            self._series(),
            [
                IndicatorConfig(
                    id="vol_20d", type="STDDEV", params={"length": 20}, source="ret_1d"
                ),
                IndicatorConfig(id="ret_1d", type="ROC", params={"length": 1}, source="close"),
            ],
        )

        assert any("vol_20d" in w for w in warnings), warnings


class TestVWAPIndicator:
    """Tests for VWAP computation."""

    @staticmethod
    def _make_intraday_df() -> pl.DataFrame:
        """Create a 2-day intraday DataFrame for VWAP testing."""
        # Day 1: 3 bars, Day 2: 3 bars
        dates = [
            datetime(2024, 1, 2, 10, 0),
            datetime(2024, 1, 2, 11, 0),
            datetime(2024, 1, 2, 12, 0),
            datetime(2024, 1, 3, 10, 0),
            datetime(2024, 1, 3, 11, 0),
            datetime(2024, 1, 3, 12, 0),
        ]
        return pl.DataFrame(
            {
                "date": dates,
                "open": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0],
                "high": [103.0, 105.0, 107.0, 109.0, 111.0, 113.0],
                "low": [99.0, 101.0, 103.0, 105.0, 107.0, 109.0],
                "close": [102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
                "volume": [1000, 2000, 3000, 1000, 2000, 3000],
            }
        )

    def test_vwap_produces_column(self) -> None:
        """VWAP should produce a named column with float values."""
        df = self._make_intraday_df()
        configs = [IndicatorConfig(id="vwap", type="VWAP")]
        result, warnings = compute_indicators(df, configs, timeframe="1hour")

        assert "vwap" in result.columns
        assert not warnings
        assert result["vwap"].dtype == pl.Float64

    def test_vwap_resets_per_session(self) -> None:
        """VWAP should reset at each new trading day boundary."""
        df = self._make_intraday_df()
        configs = [IndicatorConfig(id="vwap", type="VWAP")]
        result, _ = compute_indicators(df, configs, timeframe="5min")

        vwap_vals = result["vwap"].to_list()
        # First bar of each day: VWAP = typical_price (since it's the first bar)
        # Day 1, bar 0: tp = (103 + 99 + 102) / 3 = 101.333...
        expected_first_bar = (103.0 + 99.0 + 102.0) / 3.0
        assert abs(vwap_vals[0] - expected_first_bar) < 0.01

        # Day 2, bar 0: tp = (109 + 105 + 108) / 3 = 107.333...
        expected_day2_first = (109.0 + 105.0 + 108.0) / 3.0
        assert abs(vwap_vals[3] - expected_day2_first) < 0.01

    def test_vwap_daily_timeframe_raises(self) -> None:
        """VWAP with daily timeframe should raise ValueError."""
        df = self._make_intraday_df()
        configs = [IndicatorConfig(id="vwap", type="VWAP")]

        with pytest.raises(ValueError, match="VWAP requires intraday data"):
            compute_indicators(df, configs, timeframe="daily")


class TestCandlestickPatterns:
    """Tests for candlestick pattern indicators."""

    def test_cdl_doji_produces_integer_output(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """CDL_DOJI should produce an integer signal column."""
        configs = [IndicatorConfig(id="doji", type="CDL_DOJI")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "doji" in result.columns
        assert not warnings
        # Should be integer type (candlestick patterns return i32/i64)
        assert result["doji"].dtype in (pl.Int32, pl.Int64)

    def test_cdl_engulfing_produces_valid_signals(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """CDL_ENGULFING should produce values in {-100, 0, 100}."""
        configs = [IndicatorConfig(id="engulfing", type="CDL_ENGULFING")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "engulfing" in result.columns
        assert not warnings
        unique_vals = set(result["engulfing"].to_list())
        assert unique_vals.issubset({-100, 0, 100})

    def test_cdl_hammer_produces_column(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """CDL_HAMMER should produce a named output column."""
        configs = [IndicatorConfig(id="hammer", type="CDL_HAMMER")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "hammer" in result.columns
        assert not warnings

    def test_cdl_morningstar_produces_column(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """CDL_MORNINGSTAR should produce a named output column."""
        configs = [IndicatorConfig(id="mstar", type="CDL_MORNINGSTAR")]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "mstar" in result.columns
        assert not warnings


class TestStatisticalIndicators:
    """Tests for statistical indicator computation."""

    def test_linearreg_slope_produces_float(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """LINEARREG_SLOPE should produce a float column."""
        configs = [
            IndicatorConfig(
                id="slope_14",
                type="LINEARREG_SLOPE",
                params={"length": 14},
            ),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "slope_14" in result.columns
        assert not warnings
        assert result["slope_14"].dtype == pl.Float64

    def test_linearreg_angle_produces_column(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """LINEARREG_ANGLE should produce a valid column."""
        configs = [
            IndicatorConfig(
                id="angle_14",
                type="LINEARREG_ANGLE",
                params={"length": 14},
            ),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "angle_14" in result.columns
        assert not warnings

    def test_stddev_produces_column(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """STDDEV should produce a valid column."""
        configs = [
            IndicatorConfig(id="std_20", type="STDDEV", params={"length": 20}),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "std_20" in result.columns
        assert not warnings
        # Standard deviation should be non-negative
        non_null = result.filter(pl.col("std_20").is_not_null())
        assert non_null["std_20"].min() >= 0  # type: ignore[operator]

    def test_linearreg_produces_column(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """LINEARREG should produce a valid column."""
        configs = [
            IndicatorConfig(id="lr_14", type="LINEARREG", params={"length": 14}),
        ]
        result, warnings = compute_indicators(sample_ohlcv_df, configs)

        assert "lr_14" in result.columns
        assert not warnings


class TestDualInputIndicators:
    """Tests for dual-input indicators (BETA, CORREL)."""

    def test_beta_with_second_source(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """BETA should compute between source and second_source columns."""
        # First compute an SMA to use as second source
        sma_config = [
            IndicatorConfig(id="sma_20", type="SMA", params={"length": 20}),
        ]
        df_with_sma, _ = compute_indicators(sample_ohlcv_df, sma_config)

        beta_config = [
            IndicatorConfig(
                id="beta_20",
                type="BETA",
                source="close",
                params={"length": 5, "second_source": "sma_20"},
            ),
        ]
        result, warnings = compute_indicators(df_with_sma, beta_config)

        assert "beta_20" in result.columns
        assert not warnings
        assert result["beta_20"].dtype == pl.Float64

    def test_correl_with_second_source(
        self,
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """CORREL should compute correlation between two columns."""
        # First compute an EMA to use as second source
        ema_config = [
            IndicatorConfig(id="ema_20", type="EMA", params={"length": 20}),
        ]
        df_with_ema, _ = compute_indicators(sample_ohlcv_df, ema_config)

        correl_config = [
            IndicatorConfig(
                id="corr_20",
                type="CORREL",
                source="close",
                params={"length": 20, "second_source": "ema_20"},
            ),
        ]
        result, warnings = compute_indicators(df_with_ema, correl_config)

        assert "corr_20" in result.columns
        assert not warnings
        # Correlation between close and its EMA should be high
        valid = result.filter(pl.col("corr_20").is_not_nan() & pl.col("corr_20").is_not_null())
        assert valid["corr_20"].mean() > 0.5  # type: ignore[operator]


class TestWarmupBarsAreUndefined:
    """A warming-up indicator must be undefined, not NaN (guards signal firing)."""

    def test_talib_warmup_is_null_not_nan(self) -> None:
        """TA-Lib emits NaN, which compares True against a threshold in Polars.

        ADX(14) needs 27 bars before it is finite. Left as NaN, `adx > 25` is
        True on every one of those bars, so a strategy enters on an indicator
        that has no value yet. Null propagates through the comparison instead.
        """
        n = 60
        df = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * n,
                "open": np.linspace(100, 140, n),
                "high": np.linspace(101, 141, n),
                "low": np.linspace(99, 139, n),
                "close": np.linspace(100, 140, n),
                "volume": [1_000_000] * n,
            }
        )
        config = IndicatorConfig(id="adx", type="ADX", params={"length": 14})

        result, _ = compute_indicators(df, [config])

        assert result["adx"].is_nan().sum() == 0
        assert result["adx"].is_null().sum() > 0
        fired = result.select((pl.col("adx") > 25).alias("x"))["x"]
        assert fired[:27].fill_null(False).sum() == 0

    def test_defined_bars_are_untouched(self) -> None:
        """Only the warm-up is undefined; real values must survive intact."""
        n = 60
        close = np.linspace(100, 140, n)
        df = pl.DataFrame(
            {
                "date": [date(2023, 1, 1)] * n,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": [1_000_000] * n,
            }
        )
        config = IndicatorConfig(id="sma", type="SMA", params={"length": 20})

        result, _ = compute_indicators(df, [config])

        assert result["sma"].is_null().sum() == 19
        assert result["sma"][19] == pytest.approx(float(np.mean(close[:20])))


class TestUnprimedIndicatorWarning:
    """A window that opens inside an indicator's lookback must say so."""

    def _frame(self, n: int) -> pl.DataFrame:
        close = np.linspace(100, 140, n)
        return pl.DataFrame(
            {
                "date": [date(2023, 1, 1) + timedelta(days=i) for i in range(n)],
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": [1_000_000] * n,
            }
        )

    def test_warns_when_the_window_opens_before_the_indicator_is_primed(self) -> None:
        """ADX(14) needs 27 bars; a 20-bar pre-roll leaves the window blind."""
        strategy = StrategyDefinition(
            name="adx",
            universe=Universe(symbols=["AAA"]),
            data_config=DataConfig(start_date="2023-01-21", end_date="2023-03-01"),
            indicators=[IndicatorConfig(id="adx", type="ADX", params={"length": 14})],
            entry_rules=RuleSet(
                conditions=[
                    Condition(
                        left=Operand(indicator="adx"),
                        operator="greater_than",
                        right=Operand(constant=25.0),
                    )
                ],
                logic="AND",
            ),
            exit_rules=RuleSet(conditions=[], logic="AND"),
        )

        _, warnings = _prepare_symbol_signals(self._frame(60), strategy, "2023-01-21")

        assert any("adx" in w and "no value until bar" in w for w in warnings)

    def test_no_warning_once_the_pre_roll_is_long_enough(self) -> None:
        """A window opening after the lookback must stay quiet."""
        strategy = StrategyDefinition(
            name="sma",
            universe=Universe(symbols=["AAA"]),
            data_config=DataConfig(start_date="2023-01-21", end_date="2023-03-01"),
            indicators=[IndicatorConfig(id="sma", type="SMA", params={"length": 5})],
            entry_rules=RuleSet(
                conditions=[
                    Condition(
                        left=Operand(indicator="sma"),
                        operator="greater_than",
                        right=Operand(constant=1.0),
                    )
                ],
                logic="AND",
            ),
            exit_rules=RuleSet(conditions=[], logic="AND"),
        )

        _, warnings = _prepare_symbol_signals(self._frame(60), strategy, "2023-01-21")

        assert warnings == []
