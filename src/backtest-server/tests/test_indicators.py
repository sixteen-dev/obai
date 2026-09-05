"""Tests for indicator computation engine."""

from __future__ import annotations

import importlib.metadata
import inspect
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl
import polars_talib as ta  # type: ignore[import-untyped]
import pytest

from src.engine.indicators import (
    _CUSTOM_BUILDERS,
    TALIB_FUNCTIONS,
    attach_benchmark_close,
    compute_indicators,
    get_supported_indicators,
    indicator_stack_versions,
)
from src.engine.signals import generate_signals
from src.models.indicator_catalog import INDICATOR_CATALOG, IndicatorSpec
from src.models.strategy import (
    BENCHMARK_CLOSE_COLUMN,
    INDICATOR_PARAM_NAMES,
    Condition,
    DataConfig,
    IndicatorConfig,
    Operand,
    RuleSet,
    StrategyDefinition,
    Universe,
)
from src.server import _prepare_symbol_signals, _required_warmup_bars
from tests.conformance_fixtures import golden_ohlcv_df


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

    def test_bbands_lists_five_output_columns(self) -> None:
        """%B and bandwidth are addressable in rules, so discovery must name them."""
        supported = get_supported_indicators()["indicators"]

        assert supported["BBANDS"]["output_columns"] == [
            "upper",
            "middle",
            "lower",
            "percent_b",
            "bandwidth",
        ]

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

    def test_param_specs_and_lookback_are_reported(self) -> None:
        """The agent picks periods from this output, so it must carry the bounds."""
        supported = get_supported_indicators()["indicators"]

        assert supported["ADX"]["param_specs"] == {
            "length": {
                "kind": "lookback",
                "default": 14,
                "min": 2,
                "max": 100_000,
                "required": False,
            }
        }
        assert supported["ADX"]["lookback_bars_at_defaults"] == 27
        assert supported["ADX"]["recursive"] is True
        assert supported["ADX"]["description"]
        assert supported["SMA"]["recursive"] is False
        assert supported["BETA"]["param_specs"]["second_source"]["default"] == "high"
        assert supported["BETA"]["param_specs"]["second_source"]["kind"] == "source_ref"

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


class TestNativeAdditions:
    """Stage 1 natives: percent-of-price range, rolling extremes, direction, adaptive MA."""

    _HIGH: list[float] = [101.0, 102.0, 106.0, 107.0, 104.0, 111.0, 113.0]
    _LOW: list[float] = [99.0, 100.0, 104.0, 103.0, 101.0, 109.0, 110.0]
    _CLOSE: list[float] = [100.0, 101.0, 105.0, 104.0, 103.0, 110.0, 112.0]
    _KAMA_CLOSE: list[float] = [*_CLOSE, 111.0, 115.0, 118.0]

    @staticmethod
    def _frame(high: list[float], low: list[float], close: list[float]) -> pl.DataFrame:
        """Build an OHLCV frame from explicit high/low/close series."""
        n = len(close)
        return pl.DataFrame(
            {
                "date": [date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": [1_000_000.0] * n,
            }
        )

    @classmethod
    def _gapped_frame(cls) -> pl.DataFrame:
        """Return the seven-bar gapped high/low/close fixture the audit pinned."""
        return cls._frame(cls._HIGH, cls._LOW, cls._CLOSE)

    @classmethod
    def _uptrend_frame(cls, n: int = 40) -> pl.DataFrame:
        """Return a steady one-point-per-bar uptrend with a two-point bar range."""
        closes = [100.0 + i for i in range(n)]
        return cls._frame([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)

    @staticmethod
    def _values(
        df: pl.DataFrame,
        indicator_type: str,
        params: dict[str, Any],
        source: str = "close",
    ) -> list[float | None]:
        """Compute one indicator through the engine and return its column."""
        config = IndicatorConfig(id="probe", type=indicator_type, params=params, source=source)
        result, warnings = compute_indicators(df, [config])
        assert warnings == [], warnings
        return result["probe"].to_list()

    @staticmethod
    def _assert_close(
        observed: list[float | None],
        expected: list[float | None],
        tolerance: float = 1e-10,
    ) -> None:
        """Assert two series agree bar by bar, undefined bars included."""
        assert len(observed) == len(expected)
        for idx, (got, want) in enumerate(zip(observed, expected, strict=True)):
            if want is None:
                assert got is None, (idx, got)
            else:
                assert got == pytest.approx(want, rel=tolerance, abs=tolerance), (idx, got, want)

    @staticmethod
    def _true_ranges(high: list[float], low: list[float], close: list[float]) -> list[float]:
        """Return true range for bars 1..n-1, each needing the prior close."""
        return [
            max(h - lo, abs(h - prev), abs(lo - prev))
            for h, lo, prev in zip(high[1:], low[1:], close[:-1], strict=True)
        ]

    @classmethod
    def _natr_reference(
        cls, high: list[float], low: list[float], close: list[float], length: int
    ) -> list[float | None]:
        """Wilder ATR seeded on the first `length` true ranges, as a percent of close."""
        ranges = cls._true_ranges(high, low, close)
        atr = sum(ranges[:length]) / length
        values: list[float | None] = [None] * length + [100.0 * atr / close[length]]
        for t in range(length + 1, len(close)):
            atr = (atr * (length - 1) + ranges[t - 1]) / length
            values.append(100.0 * atr / close[t])
        return values

    @staticmethod
    def _directional_movement(high: list[float], low: list[float], *, plus: bool) -> list[float]:
        """Return Wilder's +DM or -DM for bars 1..n-1."""
        ups = [high[i] - high[i - 1] for i in range(1, len(high))]
        downs = [low[i - 1] - low[i] for i in range(1, len(low))]
        pairs = list(zip(ups, downs, strict=True))
        if plus:
            return [up if up > down and up > 0 else 0.0 for up, down in pairs]
        return [down if down > up and down > 0 else 0.0 for up, down in pairs]

    @classmethod
    def _di_reference(
        cls,
        high: list[float],
        low: list[float],
        close: list[float],
        length: int,
        *,
        plus: bool,
    ) -> list[float | None]:
        """Smoothed directional movement as a percent of smoothed true range."""
        ranges = cls._true_ranges(high, low, close)
        moves = cls._directional_movement(high, low, plus=plus)
        smoothed_dm, smoothed_tr = sum(moves[: length - 1]), sum(ranges[: length - 1])
        values: list[float | None] = [None] * length
        for t in range(length, len(close)):
            smoothed_dm = smoothed_dm - smoothed_dm / length + moves[t - 1]
            smoothed_tr = smoothed_tr - smoothed_tr / length + ranges[t - 1]
            values.append(100.0 * smoothed_dm / smoothed_tr)
        return values

    @staticmethod
    def _kama_reference(close: list[float], length: int) -> list[float | None]:
        """EMA whose gain is the squared efficiency ratio scaled between TA-Lib's constants."""
        fast, slow = 2.0 / 3.0, 2.0 / 31.0
        level = close[length - 1]
        values: list[float | None] = [None] * length
        for t in range(length, len(close)):
            travel = sum(abs(close[i] - close[i - 1]) for i in range(t - length + 1, t + 1))
            ratio = abs(close[t] - close[t - length]) / travel if travel else 0.0
            level += (ratio * (fast - slow) + slow) ** 2 * (close[t] - level)
            values.append(level)
        return values

    def test_natr_constant_range_is_two_percent(self) -> None:
        """A constant two-point range on a 100 close is 2% of price, once warmed up."""
        df = self._frame([101.0] * 60, [99.0] * 60, [100.0] * 60)

        values = self._values(df, "NATR", {"length": 14})

        assert values[:14] == [None] * 14
        self._assert_close(values[14:], [2.0] * 46)

    def test_natr_matches_independent_wilder_recurrence(self) -> None:
        """Percent units and Wilder smoothing, pinned against an in-test recurrence."""
        pinned: list[float | None] = [
            None,
            None,
            None,
            3.5256410256410255,
            3.344120819848975,
            4.511784511784512,
            3.84700176366843,
        ]

        values = self._values(self._gapped_frame(), "NATR", {"length": 3})

        self._assert_close(values, pinned)
        self._assert_close(self._natr_reference(self._HIGH, self._LOW, self._CLOSE, 3), pinned)

    def test_max_over_rising_sequence(self) -> None:
        """MAX of a strictly rising close is the current bar, from the window's first full bar."""
        closes = [float(i + 1) for i in range(60)]
        df = self._frame([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)

        values = self._values(df, "MAX", {"length": 20})

        assert values[:19] == [None] * 19
        assert values[19] == pytest.approx(20.0)
        assert values[59] == pytest.approx(60.0)

    def test_max_includes_the_current_bar(self) -> None:
        """MAX spans the current bar, unlike a prior-bar channel."""
        expected: list[float | None] = [None, None, 106.0, 107.0, 107.0, 111.0, 113.0]

        values = self._values(self._gapped_frame(), "MAX", {"length": 3}, source="high")

        self._assert_close(values, expected)
        assert values[3] == pytest.approx(self._HIGH[3])

    def test_min_over_rising_sequence(self) -> None:
        """MIN of a strictly rising close is the window's oldest bar."""
        closes = [float(i + 1) for i in range(60)]
        df = self._frame([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)

        values = self._values(df, "MIN", {"length": 20})

        assert values[18] is None
        assert values[19] == pytest.approx(1.0)
        assert values[59] == pytest.approx(41.0)

    def test_min_on_low_fixture(self) -> None:
        """MIN tracks the three-bar low of the low series, current bar included."""
        expected: list[float | None] = [None, None, 99.0, 100.0, 101.0, 101.0, 101.0]

        values = self._values(self._gapped_frame(), "MIN", {"length": 3}, source="low")

        self._assert_close(values, expected)

    def test_plus_di_is_fifty_in_a_steady_uptrend(self) -> None:
        """One point of up-move against a two-point true range is +DI 50."""
        values = self._values(self._uptrend_frame(), "PLUS_DI", {"length": 14})

        assert values[13] is None
        self._assert_close(values[14:], [50.0] * 26, tolerance=1e-9)

    def test_plus_di_matches_wilder_reference(self) -> None:
        """+DI's seeding and smoothing, pinned against an in-test recurrence."""
        pinned: list[float | None] = [
            None,
            None,
            None,
            38.46153846153846,
            25.31645569620253,
            61.229946524064175,
            62.563067608476295,
        ]

        values = self._values(self._gapped_frame(), "PLUS_DI", {"length": 3})

        self._assert_close(values, pinned, tolerance=1e-9)
        reference = self._di_reference(self._HIGH, self._LOW, self._CLOSE, 3, plus=True)
        self._assert_close(reference, pinned, tolerance=1e-9)

    def test_minus_di_is_zero_in_a_steady_uptrend(self) -> None:
        """No down-move means no minus directional movement at all."""
        values = self._values(self._uptrend_frame(), "MINUS_DI", {"length": 14})

        assert values[13] is None
        self._assert_close(values[14:], [0.0] * 26, tolerance=1e-9)

    def test_minus_di_matches_wilder_reference(self) -> None:
        """-DI's seeding and smoothing, pinned against an in-test recurrence."""
        pinned: list[float | None] = [
            None,
            None,
            None,
            0.0,
            22.78481012658228,
            9.625668449197862,
            7.265388496468217,
        ]

        values = self._values(self._gapped_frame(), "MINUS_DI", {"length": 3})

        self._assert_close(values, pinned, tolerance=1e-9)
        reference = self._di_reference(self._HIGH, self._LOW, self._CLOSE, 3, plus=False)
        self._assert_close(reference, pinned, tolerance=1e-9)

    def test_di_crossover_rule_fires(self) -> None:
        """A +DI/-DI crossover rule must fire on the one bar the ordering flips."""
        closes = [120.0 - i for i in range(20)] + [100.0 + i for i in range(1, 21)]
        df = self._frame([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)
        enriched, warnings = compute_indicators(
            df,
            [
                IndicatorConfig(id="plus_di", type="PLUS_DI", params={"length": 14}),
                IndicatorConfig(id="minus_di", type="MINUS_DI", params={"length": 14}),
            ],
        )
        entry = RuleSet(
            conditions=[
                Condition(
                    left=Operand(indicator="plus_di"),
                    operator="crosses_above",
                    right=Operand(indicator="minus_di"),
                )
            ],
            logic="AND",
        )

        assert warnings == []
        signals = generate_signals(enriched, entry, RuleSet(conditions=[], logic="AND"))

        fired = [i for i, flag in enumerate(signals["entry_signal"].to_list()) if flag]
        assert fired == [29]
        assert signals["plus_di"][29] > signals["minus_di"][29]
        assert signals["plus_di"][28] < signals["minus_di"][28]

    def test_kama_is_flat_on_flat_prices(self) -> None:
        """With no price travel the adaptive average sits on the price."""
        df = self._frame([101.0] * 60, [99.0] * 60, [100.0] * 60)

        values = self._values(df, "KAMA", {"length": 14})

        assert values[13] is None
        self._assert_close(values[14:], [100.0] * 46)

    def test_kama_matches_independent_recurrence(self) -> None:
        """The efficiency-ratio gain and its seed, pinned against an in-test recurrence."""
        pinned: list[float | None] = [
            None,
            None,
            None,
            104.78289076450713,
            104.65746701832657,
            105.50819208509881,
            107.44518123731416,
            108.50584807645598,
            110.09465897600727,
            112.20055627272126,
        ]
        closes = self._KAMA_CLOSE
        df = self._frame([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)

        values = self._values(df, "KAMA", {"length": 3})

        self._assert_close(values, pinned)
        self._assert_close(self._kama_reference(closes, 3), pinned)


class TestComposites:
    """Stage 2 composites: indicators the engine computes in Polars itself."""

    _CHANNEL_HIGH: list[float | None] = [100.0, 102.0, 101.0, 104.0, 103.0]
    _CHANNEL_LOW: list[float | None] = [98.0, 99.0, 100.0, 101.0, 99.5]
    _CHANNEL_CLOSE: list[float | None] = [99.0, 101.0, 100.5, 103.0, 100.0]

    @staticmethod
    def _frame(
        close: list[float | None],
        *,
        high: list[float | None] | None = None,
        low: list[float | None] | None = None,
        open_: list[float | None] | None = None,
        extra: dict[str, pl.Series] | None = None,
    ) -> pl.DataFrame:
        """Build an OHLCV frame around a close series, defaulting the rest to it."""
        n = len(close)
        columns: dict[str, Any] = {
            "date": [date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": open_ if open_ is not None else close,
            "high": high if high is not None else close,
            "low": low if low is not None else close,
            "close": close,
            "volume": [1_000_000.0] * n,
        }
        columns.update(extra or {})
        return pl.DataFrame(columns)

    @staticmethod
    def _compute(df: pl.DataFrame, *indicators: IndicatorConfig) -> pl.DataFrame:
        """Run indicators through the engine, failing on any warning."""
        result, warnings = compute_indicators(df, list(indicators))
        assert warnings == [], warnings
        return result

    @classmethod
    def _values(
        cls,
        df: pl.DataFrame,
        indicator_type: str,
        params: dict[str, Any],
        source: str = "close",
    ) -> list[Any]:
        """Compute one composite through the engine and return its column."""
        config = IndicatorConfig(id="probe", type=indicator_type, params=params, source=source)
        return cls._compute(df, config)["probe"].to_list()

    @staticmethod
    def _assert_close(
        observed: list[Any],
        expected: list[float | None],
        tolerance: float = 1e-12,
    ) -> None:
        """Assert two series agree bar by bar, undefined bars included."""
        assert len(observed) == len(expected)
        for idx, (got, want) in enumerate(zip(observed, expected, strict=True)):
            if want is None:
                assert got is None, (idx, got)
            else:
                assert got == pytest.approx(want, abs=tolerance), (idx, got, want)

    @staticmethod
    def _entry_rules(left: str, operator: str, right: Operand) -> RuleSet:
        """Build a one-condition entry rule on a computed column."""
        return RuleSet(
            conditions=[Condition(left=Operand(indicator=left), operator=operator, right=right)],
            logic="AND",
        )

    @classmethod
    def _entry_signals(
        cls, df: pl.DataFrame, left: str, operator: str, right: Operand
    ) -> list[bool]:
        """Evaluate a one-condition entry rule over an enriched frame."""
        signals = generate_signals(
            df, cls._entry_rules(left, operator, right), RuleSet(conditions=[], logic="AND")
        )
        return signals["entry_signal"].to_list()

    def test_lag_shifts_by_periods(self) -> None:
        """Row t carries the value the source held `periods` bars earlier."""
        values = self._values(self._frame([1.0, 2.0, 3.0, 4.0]), "LAG", {"periods": 2})

        self._assert_close(values, [None, None, 1.0, 2.0])

    def test_lag_keeps_integer_dtype(self) -> None:
        """A candle-pattern column is still an integer signal after the shift."""
        pattern = pl.Series("pattern", [0, 100, -100], dtype=pl.Int32)
        df = self._frame([10.0, 11.0, 12.0], extra={"pattern": pattern})

        computed = self._compute(
            df,
            IndicatorConfig(id="prev", type="LAG", params={"periods": 1}, source="pattern"),
        )

        assert computed["prev"].to_list() == [None, 0, 100]
        assert computed.schema["prev"] == pl.Int32

    def test_close_above_prior_close_rule(self) -> None:
        """The rule the lag exists for: this bar's close against the one before."""
        enriched = self._compute(
            self._frame([10.0, 11.0, 10.5, 12.0]),
            IndicatorConfig(id="prev", type="LAG", params={"periods": 1}),
        )

        fired = self._entry_signals(enriched, "close", "greater_than", Operand(indicator="prev"))

        assert fired == [False, True, False, True]

    def test_lag_periods_zero_is_rejected(self) -> None:
        """A zero offset would read the current bar and defeat the point."""
        errors = IndicatorConfig(id="prev", type="LAG", params={"periods": 0}).validate()

        assert len(errors) == 1
        assert "[1, 100000]" in errors[0]

    def test_ratio_guards_zero_denominator(self) -> None:
        """A zero divisor leaves the bar undefined instead of infinite."""
        df = self._frame([1.0, 4.0, 9.0], open_=[2.0, 0.0, 3.0])

        values = self._values(df, "RATIO", {"second_source": "open"})

        self._assert_close(values, [0.5, None, 3.0])

    def test_diff_values(self) -> None:
        """The spread between two series, bar by bar."""
        df = self._frame([1.0, 4.0, 9.0], open_=[2.0, 0.0, 3.0])

        values = self._values(df, "DIFF", {"second_source": "open"})

        self._assert_close(values, [-1.0, 4.0, 6.0])

    def test_ratio_of_close_to_its_average_chains(self) -> None:
        """Price-to-average: the divisor is an indicator declared above it."""
        enriched = self._compute(
            self._frame([10.0, 12.0, 14.0, 16.0]),
            IndicatorConfig(id="sma", type="SMA", params={"length": 2}),
            IndicatorConfig(id="rel", type="RATIO", params={"second_source": "sma"}),
        )

        self._assert_close(enriched["rel"].to_list(), [None, 12 / 11, 14 / 13, 16 / 15])
        fired = self._entry_signals(enriched, "rel", "greater_than", Operand(constant=1.0))
        assert fired == [False, True, True, True]

    @classmethod
    def _channel_frame(cls) -> pl.DataFrame:
        """Return the five-bar breakout fixture from the audit."""
        return cls._frame(cls._CHANNEL_CLOSE, high=cls._CHANNEL_HIGH, low=cls._CHANNEL_LOW)

    @classmethod
    def _channel(cls, df: pl.DataFrame, length: int) -> pl.DataFrame:
        """Compute a Donchian channel with id ``dc`` over a frame."""
        return cls._compute(
            df, IndicatorConfig(id="dc", type="DONCHIAN", params={"length": length})
        )

    def test_donchian_excludes_the_current_bar(self) -> None:
        """The channel spans the prior `length` bars, so a close can clear it."""
        computed = self._channel(self._channel_frame(), 3)

        self._assert_close(computed["dc_upper"].to_list(), [None, None, None, 102.0, 104.0])
        self._assert_close(computed["dc_middle"].to_list(), [None, None, None, 100.0, 101.5])
        self._assert_close(computed["dc_lower"].to_list(), [None, None, None, 98.0, 99.0])

    def test_donchian_breakout_rule_fires_on_the_signal_bar(self) -> None:
        """Bar 3 closes at 103 over a 102 channel; a current-bar-inclusive one is 104."""
        computed = self._channel(self._channel_frame(), 3)

        fired = self._entry_signals(
            computed, "close", "greater_than", Operand(indicator="dc_upper")
        )

        assert fired == [False, False, False, True, False]

    def test_donchian_null_inside_window_is_null(self) -> None:
        """A missing high leaves every channel bar whose window reads it undefined."""
        df = self._frame(
            [*self._CHANNEL_CLOSE, 104.0],
            high=[100.0, None, 101.0, 104.0, 103.0, 105.0],
            low=[*self._CHANNEL_LOW, 99.0],
        )

        upper = self._channel(df, 3)["dc_upper"].to_list()

        assert upper[:5] == [None] * 5
        assert upper[5] == pytest.approx(104.0)

    def test_zscore_uses_population_stddev(self) -> None:
        """The divisor is TA-Lib's population deviation; a sample one gives 1.0."""
        values = self._values(self._frame([1.0, 2.0, 3.0]), "ZSCORE", {"length": 3})

        self._assert_close(values, [None, None, 1.224744871391589])

    def test_zscore_on_seven_bar_closes(self) -> None:
        """Standardized displacement over a five-bar window, pinned to the audit."""
        closes: list[float | None] = [100.0, 101.0, 105.0, 104.0, 103.0, 110.0, 112.0]

        values = self._values(self._frame(closes), "ZSCORE", {"length": 5})

        self._assert_close(
            values,
            [
                None,
                None,
                None,
                None,
                0.21566554640687988,
                1.7960132841418968,
                1.4672648847560603,
            ],
            tolerance=1e-9,
        )

    def test_zscore_flat_window_is_null(self) -> None:
        """A flat window has no dispersion to standardize by, so no rule can fire."""
        computed = self._compute(
            self._frame([5.0, 5.0, 5.0]),
            IndicatorConfig(id="z", type="ZSCORE", params={"length": 3}),
        )

        assert computed["z"].to_list() == [None, None, None]
        assert self._entry_signals(computed, "z", "greater_than", Operand(constant=0.0)) == [
            False,
            False,
            False,
        ]

    def test_bbands_percent_b_and_bandwidth_values(self) -> None:
        """The two derived band outputs, pinned to the audit's seven-bar closes."""
        closes: list[float | None] = [100.0, 101.0, 105.0, 104.0, 103.0, 110.0, 112.0]

        computed = self._compute(
            self._frame(closes),
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 3, "std_dev": 2.0}),
        )

        self._assert_close(
            computed["bb_percent_b"].to_list(),
            [
                None,
                None,
                0.8471825374147298,
                0.598058067569096,
                0.19381378215196426,
                0.8504565129111892,
                0.7375638170348616,
            ],
            tolerance=1e-9,
        )
        self._assert_close(
            computed["bb_bandwidth"].to_list(),
            [
                None,
                None,
                0.08471556468506446,
                0.06579380017538859,
                0.031403714651052184,
                0.11701726808195978,
                0.14247183880357966,
            ],
            tolerance=1e-9,
        )

    def test_bbands_percent_b_is_null_when_bands_collapse(self) -> None:
        """A flat window collapses the bands, leaving %B no width to divide by."""
        computed = self._compute(
            self._frame([100.0] * 6),
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 3}),
        )

        assert computed["bb_percent_b"].to_list() == [None] * 6
        self._assert_close(computed["bb_bandwidth"].to_list(), [None, None, 0.0, 0.0, 0.0, 0.0])

    @classmethod
    def _volume_frame(cls, volumes: list[float]) -> pl.DataFrame:
        """Build a flat-priced daily frame carrying the supplied volume series."""
        return cls._frame([100.0] * len(volumes), extra={"volume": pl.Series("volume", volumes)})

    @staticmethod
    def _intraday_volume_frame(volumes: list[float]) -> pl.DataFrame:
        """Build a two-session five-minute frame carrying the supplied volumes."""
        per_session = len(volumes) // 2
        stamps = [
            datetime(2024, 1, 2 + day, 10, 0) + timedelta(minutes=5 * bar)
            for day in range(2)
            for bar in range(per_session)
        ]
        return pl.DataFrame(
            {
                "date": stamps,
                "open": [100.0] * len(stamps),
                "high": [100.0] * len(stamps),
                "low": [100.0] * len(stamps),
                "close": [100.0] * len(stamps),
                "volume": volumes,
            }
        )

    def test_rvol_excludes_the_current_bar_from_the_reference(self) -> None:
        """The audit's fixture: 400 against a 100/200/300 reference is 2.0."""
        values = self._values(
            self._volume_frame([100.0, 200.0, 300.0, 400.0]), "RVOL", {"length": 3}
        )

        self._assert_close(values, [None, None, None, 2.0])

    def test_rvol_zero_reference_is_null_not_infinite(self) -> None:
        """A dead reference window leaves the bar undefined rather than infinite."""
        values = self._values(
            self._volume_frame([100.0, 200.0, 300.0, 400.0, 0.0, 0.0, 0.0, 50.0]),
            "RVOL",
            {"length": 3},
        )

        self._assert_close(values, [None, None, None, 2.0, 0.0, 0.0, 0.0, None])

    def test_rvol_on_intraday_bars_is_bar_relative(self) -> None:
        """Intraday the reference is the preceding bars, so it crosses the session open."""
        volumes = [1000.0] * 6 + [3000.0] + [1000.0] * 5

        computed, warnings = compute_indicators(
            self._intraday_volume_frame(volumes),
            [IndicatorConfig(id="rvol", type="RVOL", params={"length": 4})],
            timeframe="5min",
        )

        assert warnings == []
        two_thirds = 2.0 / 3.0
        self._assert_close(
            computed["rvol"].to_list(),
            [None, None, None, None, 1.0, 1.0, 3.0, *[two_thirds] * 4, 1.0],
        )

    def test_percentile_rank_uses_mid_rank_ties(self) -> None:
        """An equal prior value counts a half, which is the audit's 62.5."""
        values = self._values(
            self._frame([1.0, 2.0, 3.0, 4.0, 3.0]), "PERCENTILE_RANK", {"length": 4}
        )

        self._assert_close(values, [None, None, None, None, 62.5])

    def test_percentile_rank_longer_fixture(self) -> None:
        """Repeated values on both sides of the comparison, pinned bar by bar."""
        values = self._values(
            self._frame([5.0, 1.0, 4.0, 4.0, 2.0, 9.0, 9.0]), "PERCENTILE_RANK", {"length": 3}
        )

        self._assert_close(
            values,
            [None, None, None, 50.0, 33.333333333333336, 100.0, 83.33333333333333],
            tolerance=1e-9,
        )

    def test_percentile_rank_requires_a_full_prior_window(self) -> None:
        """One missing value in the reference window undefines the rank, not zero-fills it."""
        values = self._values(
            self._frame([1.0, None, 2.0, 3.0, 1.0]), "PERCENTILE_RANK", {"length": 2}
        )

        self._assert_close(values, [None, None, None, None, 0.0])

    def test_natr_percentile_chains(self) -> None:
        """The volatility-regime filter: a rank over a NATR column declared above it.

        The warm-ups add: NATR(3) is undefined for three bars and the rank needs
        two defined ones behind it, so the first ranked bar is row 5.
        """
        computed = self._compute(
            TestNativeAdditions._gapped_frame(),
            IndicatorConfig(id="natr", type="NATR", params={"length": 3}),
            IndicatorConfig(id="rank", type="PERCENTILE_RANK", params={"length": 2}, source="natr"),
        )

        self._assert_close(computed["rank"].to_list(), [None, None, None, None, None, 100.0, 50.0])

    def test_percentile_rank_length_over_5000_is_rejected(self) -> None:
        """The comparison materializes the whole window, so its length is bounded."""
        errors = IndicatorConfig(
            id="pr", type="PERCENTILE_RANK", params={"length": 5001}
        ).validate()

        assert len(errors) == 1
        assert "[1, 5000]" in errors[0]
        assert (
            IndicatorConfig(id="pr", type="PERCENTILE_RANK", params={"length": 5000}).validate()
            == []
        )

    def test_keltner_seven_bar_fixture(self) -> None:
        """EMA centre with a Wilder-ATR width, pinned to the audit's gapped fixture."""
        computed = self._compute(
            TestNativeAdditions._gapped_frame(),
            IndicatorConfig(
                id="kc",
                type="KELTNER",
                params={"length": 3, "atr_length": 3, "multiplier": 1.0},
            ),
        )

        self._assert_close(
            computed["kc_middle"].to_list(),
            [None, None, 102.0, 103.0, 103.0, 106.5, 109.25],
            tolerance=1e-9,
        )
        assert computed["kc_upper"].to_list()[:3] == [None, None, None]
        self._assert_close(
            computed["kc_upper"].to_list()[3:],
            [
                106.66666666666667,
                106.44444444444444,
                111.46296296296296,
                113.55864197530865,
            ],
            tolerance=1e-9,
        )
        self._assert_close(
            computed["kc_lower"].to_list()[3:],
            [
                99.33333333333333,
                99.55555555555556,
                101.53703703703704,
                104.94135802469135,
            ],
            tolerance=1e-9,
        )

    def test_keltner_constant_range_defaults(self) -> None:
        """A steady two-point range at the defaults is a four-point channel."""
        bars = 60
        computed = self._compute(
            self._frame([100.0] * bars, high=[101.0] * bars, low=[99.0] * bars),
            IndicatorConfig(id="kc", type="KELTNER"),
        )

        assert computed["kc_middle"].to_list()[:19] == [None] * 19
        self._assert_close(computed["kc_middle"].to_list()[19:], [100.0] * 41)
        self._assert_close(computed["kc_upper"].to_list()[19:], [104.0] * 41)
        self._assert_close(computed["kc_lower"].to_list()[19:], [96.0] * 41)

    def test_keltner_multiplier_zero_is_rejected(self) -> None:
        """A zero multiplier collapses the channel onto its own centre line."""
        errors = IndicatorConfig(id="kc", type="KELTNER", params={"multiplier": 0}).validate()

        assert len(errors) == 1
        assert "[0.1, 10.0]" in errors[0]
        assert IndicatorConfig(id="kc", type="KELTNER", params={"multiplier": 0.1}).validate() == []


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
                "date": [date(2023, 1, 1) + timedelta(days=i) for i in range(n)],
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
                "date": [date(2023, 1, 1) + timedelta(days=i) for i in range(n)],
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

    def test_adx_window_is_primed_with_the_planned_pre_roll(self) -> None:
        """The planner's pre-roll must silence the measured backstop."""
        indicators = [IndicatorConfig(id="adx", type="ADX", params={"length": 14})]
        pre_roll = _required_warmup_bars(indicators).bars
        start = date(2023, 1, 1) + timedelta(days=pre_roll)
        strategy = StrategyDefinition(
            name="adx",
            universe=Universe(symbols=["AAA"]),
            data_config=DataConfig(start_date=start.isoformat(), end_date="2024-01-01"),
            indicators=indicators,
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

        _, warnings = _prepare_symbol_signals(
            self._frame(pre_roll + 40), strategy, start.isoformat()
        )

        assert warnings == []

    def test_multi_output_indicator_is_checked(self) -> None:
        """A band indicator emits suffixed columns, and those need checking too."""
        strategy = StrategyDefinition(
            name="bbands",
            universe=Universe(symbols=["AAA"]),
            data_config=DataConfig(start_date="2023-01-06", end_date="2023-03-01"),
            indicators=[IndicatorConfig(id="bb", type="BBANDS", params={"length": 20})],
            entry_rules=RuleSet(
                conditions=[
                    Condition(
                        left=Operand(indicator="close"),
                        operator="greater_than",
                        right=Operand(indicator="bb_upper"),
                    )
                ],
                logic="AND",
            ),
            exit_rules=RuleSet(conditions=[], logic="AND"),
        )

        _, warnings = _prepare_symbol_signals(self._frame(60), strategy, "2023-01-06")

        assert any("bb" in w and "no value until bar 14" in w for w in warnings)


class TestCatalogEngineParity:
    """Every catalogued indicator must be executable, and nothing else may be."""

    def test_every_catalog_entry_has_exactly_one_binding(self) -> None:
        """An entry with no binding validates but cannot compute, and vice versa."""
        talib_names, custom_names = set(TALIB_FUNCTIONS), set(_CUSTOM_BUILDERS)

        assert talib_names | custom_names == set(INDICATOR_CATALOG)
        assert talib_names & custom_names == set()

    def test_talib_param_names_exist_on_the_bound_function(self) -> None:
        """A stale kwarg name would raise inside the compute warning instead."""
        for name, spec in INDICATOR_CATALOG.items():
            fn = TALIB_FUNCTIONS.get(name)
            if fn is None:
                continue
            accepted = inspect.signature(fn).parameters
            for param_name, param in spec.params.items():
                if param.talib_name is None:
                    continue
                assert param.talib_name in accepted, f"{name}.{param_name}"

    def test_the_schema_views_track_the_catalog(self) -> None:
        """Validation reads the same entries the engine dispatches on."""
        assert set(INDICATOR_PARAM_NAMES) == set(INDICATOR_CATALOG)
        for name, spec in INDICATOR_CATALOG.items():
            assert INDICATOR_PARAM_NAMES[name] == frozenset(spec.params), name


class TestLookbackContract:
    """Each spec's declared lookback must be the first bar it actually defines."""

    @staticmethod
    def _frame(n: int = 400) -> pl.DataFrame:
        """Return a seeded random-walk OHLCV frame long enough for any default."""
        rng = np.random.default_rng(20260904)
        closes = 100.0 + rng.normal(0.0, 1.0, n).cumsum()
        return pl.DataFrame(
            {
                "date": [date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
                "open": closes + rng.uniform(-1.0, 1.0, n),
                "high": closes + rng.uniform(0.1, 2.0, n),
                "low": closes - rng.uniform(0.1, 2.0, n),
                "close": closes,
                "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
            }
        )

    @staticmethod
    def _variants(spec: IndicatorSpec) -> list[dict[str, Any]]:
        """Return resolved param sets covering each lookback param's extremes."""
        variants = [spec.resolve_params({})]
        for name, param in spec.params.items():
            if param.kind != "lookback":
                continue
            for value in (param.min, 20):
                if param.min is None or param.max is None:
                    continue
                if param.min <= value <= param.max:
                    variants.append(spec.resolve_params({name: value}))
        return variants

    @staticmethod
    def _first_valid_index(df: pl.DataFrame, spec: IndicatorSpec, params: dict[str, Any]) -> int:
        """Return the first row where every column the indicator emits is defined."""
        config = IndicatorConfig(id="probe", type=spec.name, params=params)
        computed, warnings = compute_indicators(df, [config])
        assert warnings == [], (spec.name, params, warnings)
        columns = [col for col in computed.columns if col == "probe" or col.startswith("probe_")]
        assert columns, spec.name
        defined = computed.select(
            pl.all_horizontal([pl.col(col).is_not_null() for col in columns]).alias("defined")
        )["defined"]
        return int(defined.arg_max())

    def test_first_valid_index_matches_spec_lookback(self) -> None:
        """The warm-up planner and the discovery output both read this number."""
        df = self._frame()
        for name, spec in INDICATOR_CATALOG.items():
            if name.startswith("CDL_") or name not in TALIB_FUNCTIONS:
                continue
            for params in self._variants(spec):
                observed = self._first_valid_index(df, spec, params)
                assert observed == spec.lookback(params), (name, params, observed)

    def test_known_lookbacks_are_pinned(self) -> None:
        """Spot values from the audit, so a formula edit cannot pass silently."""
        expected = {
            "ADX": ({"length": 14}, 27),
            "TEMA": ({"length": 20}, 57),
            "STOCHRSI": ({"length": 14}, 20),
            "MACD": ({"fast_length": 12, "slow_length": 26, "signal_length": 9}, 33),
            "STOCH": ({"fastk_period": 5, "slowk_period": 3, "slowd_period": 3}, 8),
            "SMA": ({"length": 200}, 199),
            "OBV": ({}, 0),
        }
        for name, (params, bars) in expected.items():
            spec = INDICATOR_CATALOG[name]
            assert spec.lookback(spec.resolve_params(params)) == bars, name


class TestMultiOutputFieldOrder:
    """The engine maps struct fields positionally, so their order is a contract."""

    # Suffixes the engine derives after the plugin's struct is unpacked, which
    # therefore have no field of their own to be mapped from.
    _ENGINE_DERIVED_OUTPUTS = {"BBANDS": 2}

    def test_plugin_struct_fields_are_pinned(self) -> None:
        """A reordered plugin struct would silently rename every output column."""
        df = pl.DataFrame(
            {
                "high": [float(i) + 1.0 for i in range(40)],
                "low": [float(i) - 1.0 for i in range(40)],
                "close": [float(i) for i in range(40)],
            }
        )
        expected = {
            "MACD": (ta.macd(pl.col("close")), ["macd", "macdsignal", "macdhist"]),
            "BBANDS": (ta.bbands(pl.col("close")), ["upperband", "middleband", "lowerband"]),
            "STOCH": (
                ta.stoch(pl.col("high"), pl.col("low"), pl.col("close")),
                ["slowk", "slowd"],
            ),
            "STOCHRSI": (ta.stochrsi(pl.col("close")), ["fastk", "fastd"]),
            "AROON": (ta.aroon(pl.col("high"), pl.col("low")), ["aroondown", "aroonup"]),
        }
        for name, (expr, fields) in expected.items():
            assert df.select(expr.alias("v")).unnest("v").columns == fields, name
            outputs = INDICATOR_CATALOG[name].outputs or ()
            mapped = len(outputs) - self._ENGINE_DERIVED_OUTPUTS.get(name, 0)
            assert mapped == len(fields), name


class TestFrameIntegrity:
    """compute_indicators is the chokepoint every production frame passes through."""

    @staticmethod
    def _frame(dates: list[date]) -> pl.DataFrame:
        """Build a minimal OHLCV frame over the supplied dates."""
        n = len(dates)
        return pl.DataFrame(
            {
                "date": dates,
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1000] * n,
            }
        )

    @staticmethod
    def _sma() -> list[IndicatorConfig]:
        """Return a single short SMA config."""
        return [IndicatorConfig(id="sma", type="SMA", params={"length": 2})]

    def test_unsorted_timestamps_are_rejected(self) -> None:
        """Rolling windows and crossover shifts are meaningless out of order."""
        df = self._frame([date(2024, 1, 3), date(2024, 1, 2)])

        with pytest.raises(ValueError, match="strictly increasing"):
            compute_indicators(df, self._sma())

    def test_duplicate_timestamps_are_rejected(self) -> None:
        """A repeated bar double-counts history and corrupts every lookback."""
        df = self._frame([date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)])

        with pytest.raises(ValueError, match="strictly increasing"):
            compute_indicators(df, self._sma())

    def test_sorted_unique_timestamps_pass(self) -> None:
        """A well-formed frame must compute without complaint."""
        result, warnings = compute_indicators(golden_ohlcv_df(), self._sma())

        assert "sma" in result.columns
        assert warnings == []


def test_indicator_stack_versions_reports_the_installed_stack() -> None:
    """Provenance must name the running wrapper and native library versions."""
    versions = indicator_stack_versions()

    assert set(versions) == {"polars", "polars_talib", "talib"}
    assert versions["polars"] == pl.__version__
    assert versions["polars_talib"] == importlib.metadata.version("polars-talib")
    assert versions["talib"] == str(ta.__talib_version__)


class TestBenchmarkAlignment:
    """The benchmark's close is a source column, aligned as of each symbol bar."""

    @staticmethod
    def _symbol_df() -> pl.DataFrame:
        """Return six daily bars whose close rises two points a bar."""
        closes = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        return pl.DataFrame(
            {
                "date": [date(2024, 1, day) for day in range(2, 8)],
                "open": closes,
                "high": [close + 1.0 for close in closes],
                "low": [close - 1.0 for close in closes],
                "close": closes,
                "volume": [1_000_000] * len(closes),
            }
        )

    @staticmethod
    def _benchmark_df() -> pl.DataFrame:
        """Return the same six sessions, rising one point a bar."""
        return pl.DataFrame(
            {
                "date": [date(2024, 1, day) for day in range(2, 8)],
                "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            }
        )

    @classmethod
    def _gapped_benchmark_df(cls) -> pl.DataFrame:
        """Return the benchmark with its first and fourth sessions missing."""
        missing = [date(2024, 1, 2), date(2024, 1, 5)]
        return cls._benchmark_df().filter(~pl.col("date").is_in(missing))

    @staticmethod
    def _roc_pair() -> list[IndicatorConfig]:
        """Return two-bar rate of change for the symbol and for the benchmark."""
        return [
            IndicatorConfig(id="roc_sym", type="ROC", params={"length": 2}, source="close"),
            IndicatorConfig(
                id="roc_bench",
                type="ROC",
                params={"length": 2},
                source=BENCHMARK_CLOSE_COLUMN,
            ),
        ]

    def test_benchmark_close_aligns_exactly_on_matching_dates(self) -> None:
        """A benchmark trading the same sessions carries over bar for bar."""
        attached, warnings = attach_benchmark_close(self._symbol_df(), self._benchmark_df(), "AAA")

        assert attached[BENCHMARK_CLOSE_COLUMN].to_list() == [
            100.0,
            101.0,
            102.0,
            103.0,
            104.0,
            105.0,
        ]
        assert warnings == []
        assert attached.height == 6

    def test_missing_benchmark_bars_carry_backward_and_warn(self) -> None:
        """A missing benchmark bar holds its last close; a missing first bar is null."""
        attached, warnings = attach_benchmark_close(
            self._symbol_df(), self._gapped_benchmark_df(), "AAA"
        )

        assert attached[BENCHMARK_CLOSE_COLUMN].to_list() == [
            None,
            101.0,
            102.0,
            102.0,
            104.0,
            105.0,
        ]
        assert attached.height == 6
        assert warnings == [
            "benchmark_close for AAA: 1 bar(s) carried from an earlier benchmark date, "
            "1 bar(s) undefined before the first benchmark bar"
        ]

    def test_roc_on_benchmark_close_feeds_a_relative_momentum_rule(self) -> None:
        """Symbol momentum against benchmark momentum is one rule over two columns."""
        attached, _ = attach_benchmark_close(self._symbol_df(), self._benchmark_df(), "AAA")
        enriched, warnings = compute_indicators(attached, self._roc_pair())
        signaled = generate_signals(
            enriched,
            RuleSet(
                logic="AND",
                conditions=[
                    Condition(
                        left=Operand(indicator="roc_sym"),
                        operator="greater_than",
                        right=Operand(indicator="roc_bench"),
                    )
                ],
            ),
            RuleSet(
                logic="OR",
                conditions=[
                    Condition(
                        left=Operand(indicator="roc_sym"),
                        operator="less_than",
                        right=Operand(indicator="roc_bench"),
                    )
                ],
            ),
        )

        assert warnings == []
        assert enriched["roc_sym"].to_list()[:2] == [None, None]
        assert enriched["roc_sym"].to_list()[2:] == pytest.approx(
            [4.0, 3.9215686275, 3.8461538462, 3.7735849057], abs=1e-9
        )
        assert enriched["roc_bench"].to_list()[:2] == [None, None]
        assert enriched["roc_bench"].to_list()[2:] == pytest.approx(
            [2.0, 1.9801980198, 1.9607843137, 1.9417475728], abs=1e-9
        )
        assert signaled["entry_signal"].to_list() == [False, False, True, True, True, True]

    def test_a_benchmark_gap_leaves_the_momentum_window_undefined(self) -> None:
        """A null benchmark bar poisons only the windows that read it."""
        attached, _ = attach_benchmark_close(self._symbol_df(), self._gapped_benchmark_df(), "AAA")
        enriched, warnings = compute_indicators(attached, self._roc_pair())

        assert warnings == []
        assert enriched["roc_bench"].to_list()[:3] == [None, None, None]
        assert enriched["roc_bench"].to_list()[3:] == pytest.approx(
            [0.9900990099, 1.9607843137, 2.9411764706], abs=1e-9
        )

    def test_empty_benchmark_frame_is_rejected(self) -> None:
        """An empty benchmark cannot align anything, so the run stops loudly."""
        with pytest.raises(ValueError, match="AAA"):
            attach_benchmark_close(self._symbol_df(), self._benchmark_df().head(0), "AAA")


class TestAnchoredVWAP:
    """AVWAP accumulates from a date the caller fixes in advance."""

    @staticmethod
    def _daily_df(volumes: list[int] | None = None) -> pl.DataFrame:
        """Return five daily bars, optionally with a supplied volume series."""
        return pl.DataFrame(
            {
                "date": [date(2024, 1, day) for day in range(2, 7)],
                "open": [100.0, 101.0, 103.0, 103.0, 103.0],
                "high": [101.0, 103.0, 105.0, 104.0, 106.0],
                "low": [99.0, 101.0, 103.0, 100.0, 102.0],
                "close": [100.0, 102.0, 104.0, 102.0, 104.0],
                "volume": volumes if volumes is not None else [1000, 2000, 1000, 1000, 2000],
            }
        )

    @staticmethod
    def _config(anchor: str = "2024-01-03") -> list[IndicatorConfig]:
        """Return one AVWAP anchored on the given date."""
        return [IndicatorConfig(id="avwap", type="AVWAP", params={"anchor_date": anchor})]

    def test_avwap_accumulates_from_the_anchor_date(self) -> None:
        """Bars before the anchor contribute nothing and hold no value."""
        result, warnings = compute_indicators(self._daily_df(), self._config())

        values = result["avwap"].to_list()
        assert warnings == []
        assert values[0] is None
        assert values[1:] == pytest.approx([102.0, 102.6666666667, 102.5, 103.0], abs=1e-9)

    def test_zero_volume_after_the_anchor_stays_undefined(self) -> None:
        """A volume-weighted average of no volume is undefined, not zero."""
        result, _ = compute_indicators(
            self._daily_df(volumes=[1000, 0, 1000, 1000, 2000]), self._config()
        )

        values = result["avwap"].to_list()
        assert values[:2] == [None, None]
        assert values[2:] == pytest.approx([104.0, 103.0, 103.5], abs=1e-9)

    def test_avwap_is_allowed_on_daily_bars(self) -> None:
        """The anchor replaces the session reset, so daily bars are meaningful."""
        result, warnings = compute_indicators(self._daily_df(), self._config(), timeframe="daily")

        assert "avwap" in result.columns
        assert warnings == []
        assert result["avwap"].dtype == pl.Float64


class TestOpeningRange:
    """Opening-range levels are published only once the interval has closed."""

    @staticmethod
    def _intraday_df() -> pl.DataFrame:
        """Return two 5-minute sessions; the second is missing its 09:40 bar."""
        stamps = [
            datetime(2024, 1, 2, 9, 30),
            datetime(2024, 1, 2, 9, 35),
            datetime(2024, 1, 2, 9, 40),
            datetime(2024, 1, 2, 9, 45),
            datetime(2024, 1, 2, 9, 50),
            datetime(2024, 1, 3, 9, 30),
            datetime(2024, 1, 3, 9, 35),
            datetime(2024, 1, 3, 9, 45),
            datetime(2024, 1, 3, 9, 50),
        ]
        highs = [101.0, 103.0, 102.0, 105.0, 99.0, 110.0, 112.0, 113.0, 111.0]
        lows = [99.0, 100.0, 98.0, 101.0, 97.0, 108.0, 109.0, 110.0, 107.0]
        return pl.DataFrame(
            {
                "date": stamps,
                "open": lows,
                "high": highs,
                "low": lows,
                "close": highs,
                "volume": [1000] * len(stamps),
            }
        )

    @staticmethod
    def _computed(minutes: int = 15) -> pl.DataFrame:
        """Return the fixture enriched with one opening range."""
        configs = [IndicatorConfig(id="orb", type="OPENING_RANGE", params={"minutes": minutes})]
        result, warnings = compute_indicators(
            TestOpeningRange._intraday_df(), configs, timeframe="5min"
        )
        assert warnings == []
        return result

    def test_range_levels_appear_only_after_the_interval_completes(self) -> None:
        """A level known only at 09:45 must not be readable on the 09:30 bar."""
        result = self._computed()

        assert result["orb_high"].to_list() == [
            None,
            None,
            None,
            103.0,
            103.0,
            None,
            None,
            112.0,
            112.0,
        ]
        assert result["orb_low"].to_list() == [
            None,
            None,
            None,
            98.0,
            98.0,
            None,
            None,
            108.0,
            108.0,
        ]

    def test_a_missing_bar_inside_the_range_does_not_shift_it(self) -> None:
        """The window is a clock interval, so a gap narrows it but never moves it."""
        result = self._computed()
        second_session = result.filter(pl.col("date").cast(pl.Date) == date(2024, 1, 3))

        assert second_session["orb_high"].to_list() == [None, None, 112.0, 112.0]
        assert second_session["orb_low"].to_list() == [None, None, 108.0, 108.0]

    def test_opening_range_requires_intraday_bars(self) -> None:
        """Daily bars have no session clock to open a range against."""
        configs = [IndicatorConfig(id="orb", type="OPENING_RANGE", params={"minutes": 15})]

        with pytest.raises(ValueError, match="OPENING_RANGE requires intraday data"):
            compute_indicators(self._intraday_df(), configs, timeframe="daily")
