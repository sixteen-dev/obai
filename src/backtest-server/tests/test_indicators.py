"""Tests for indicator computation engine."""

from __future__ import annotations

import polars as pl

from src.engine.indicators import compute_indicators, get_supported_indicators
from src.models.strategy import IndicatorConfig


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
        supported = get_supported_indicators()
        assert "SMA" in supported
        assert "RSI" in supported
        assert "MACD" in supported
        assert "BBANDS" in supported
        assert "ATR" in supported

    def test_indicator_info_structure(self) -> None:
        """Each indicator should have expected info fields."""
        supported = get_supported_indicators()
        for name, info in supported.items():
            assert "params" in info, f"{name} missing params"
            assert "multi_output" in info, f"{name} missing multi_output"
            assert "needs_hlc" in info, f"{name} missing needs_hlc"
            assert "needs_volume" in info, f"{name} missing needs_volume"

    def test_macd_is_multi_output(self) -> None:
        """MACD should be marked as multi-output."""
        supported = get_supported_indicators()
        assert supported["MACD"]["multi_output"] is True
        assert supported["MACD"]["output_columns"] == ["macd", "signal", "hist"]

    def test_sma_is_single_output(self) -> None:
        """SMA should be marked as single-output."""
        supported = get_supported_indicators()
        assert supported["SMA"]["multi_output"] is False
