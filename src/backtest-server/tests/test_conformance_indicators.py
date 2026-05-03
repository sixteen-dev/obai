"""Conformance tests for indicator mapping and TA-Lib parity."""

from __future__ import annotations

import math

import polars as pl
import polars_talib as ta  # type: ignore[import-untyped]

from src.engine.indicators import compute_indicators
from src.models.strategy import IndicatorConfig
from tests.conformance_fixtures import (
    INDICATOR_TAIL_GOLDEN,
    golden_ohlcv_df,
    null_safe_round_tail,
)


def _assert_series_close(actual: pl.Series, expected: pl.Series, tolerance: float = 1e-10) -> None:
    """Assert two numeric Polars series match, treating NaN as equal."""
    assert len(actual) == len(expected)
    for actual_value, expected_value in zip(actual.to_list(), expected.to_list(), strict=True):
        if _is_nan(actual_value) and _is_nan(expected_value):
            continue
        assert (
            actual_value == expected_value
            or abs(float(actual_value) - float(expected_value)) < tolerance
        )


def _is_nan(value: object) -> bool:
    """Return True for floating NaN values."""
    return isinstance(value, float) and math.isnan(value)


class TestIndicatorParity:
    """Parity checks against direct polars-talib expressions."""

    def test_single_output_indicators_match_polars_talib_reference(self) -> None:
        """SMA, RSI, ATR, and OBV should match direct polars-talib calls."""
        df = golden_ohlcv_df()
        configs = [
            IndicatorConfig(id="sma_5", type="SMA", params={"length": 5}),
            IndicatorConfig(id="rsi_5", type="RSI", params={"length": 5}),
            IndicatorConfig(id="atr_5", type="ATR", params={"length": 5}),
            IndicatorConfig(id="obv", type="OBV"),
        ]

        result, warnings = compute_indicators(df, configs)

        assert warnings == []
        expected = df.select(
            ta.sma(pl.col("close"), timeperiod=5).alias("sma_5"),
            ta.rsi(pl.col("close"), timeperiod=5).alias("rsi_5"),
            ta.atr(pl.col("high"), pl.col("low"), pl.col("close"), timeperiod=5).alias("atr_5"),
            ta.obv(pl.col("close"), pl.col("volume")).alias("obv"),
        )
        for column in expected.columns:
            _assert_series_close(result[column], expected[column])

    def test_multi_output_indicators_match_polars_talib_reference(self) -> None:
        """MACD, BBANDS, and STOCH output mapping should match TA-Lib field order."""
        df = golden_ohlcv_df()
        configs = [
            IndicatorConfig(
                id="macd",
                type="MACD",
                params={"fast_length": 3, "slow_length": 6, "signal_length": 3},
            ),
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 5, "std_dev": 2}),
            IndicatorConfig(
                id="stoch",
                type="STOCH",
                params={"fastk_period": 5, "slowk_period": 3, "slowd_period": 3},
            ),
        ]

        result, warnings = compute_indicators(df, configs)

        assert warnings == []
        macd = df.select(
            ta.macd(pl.col("close"), fastperiod=3, slowperiod=6, signalperiod=3).alias("macd")
        ).unnest("macd")
        bbands = df.select(ta.bbands(pl.col("close"), timeperiod=5, nbdevup=2).alias("bb")).unnest(
            "bb"
        )
        stoch = df.select(
            ta.stoch(
                pl.col("high"),
                pl.col("low"),
                pl.col("close"),
                fastk_period=5,
                slowk_period=3,
                slowd_period=3,
            ).alias("stoch")
        ).unnest("stoch")

        _assert_series_close(result["macd_macd"], macd["macd"])
        _assert_series_close(result["macd_signal"], macd["macdsignal"])
        _assert_series_close(result["macd_hist"], macd["macdhist"])
        _assert_series_close(result["bb_upper"], bbands["upperband"])
        _assert_series_close(result["bb_middle"], bbands["middleband"])
        _assert_series_close(result["bb_lower"], bbands["lowerband"])
        _assert_series_close(result["stoch_slowk"], stoch["slowk"])
        _assert_series_close(result["stoch_slowd"], stoch["slowd"])

    def test_indicator_tail_values_match_committed_golden_fixture(self) -> None:
        """Committed tail values should detect upstream or mapping drift."""
        df = golden_ohlcv_df()
        configs = [
            IndicatorConfig(id="sma_5", type="SMA", params={"length": 5}),
            IndicatorConfig(id="rsi_5", type="RSI", params={"length": 5}),
            IndicatorConfig(
                id="macd",
                type="MACD",
                params={"fast_length": 3, "slow_length": 6, "signal_length": 3},
            ),
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 5, "std_dev": 2}),
            IndicatorConfig(
                id="stoch",
                type="STOCH",
                params={"fastk_period": 5, "slowk_period": 3, "slowd_period": 3},
            ),
            IndicatorConfig(id="atr_5", type="ATR", params={"length": 5}),
            IndicatorConfig(id="obv", type="OBV"),
        ]

        result, warnings = compute_indicators(df, configs)

        assert warnings == []
        for column, expected_tail in INDICATOR_TAIL_GOLDEN.items():
            assert null_safe_round_tail(result[column].to_list()) == expected_tail
