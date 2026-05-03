"""Conformance tests for data-quality metadata and edge cases."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from src.engine.metrics import compute_metrics, expected_trading_days


class TestDataQualityConformance:
    """Data-quality checks should be deterministic and non-invasive."""

    def test_data_quality_reports_coverage_dates_and_bad_prices(self) -> None:
        """Coverage metadata should expose actual date span and zero/null counts."""
        dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(6)]
        ohlcv = pl.DataFrame(
            {
                "date": dates,
                "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
                "high": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
                "low": [99.0, 100.0, 101.0, 102.0, 103.0, 104.0],
                "close": [100.0, 0.0, 102.0, None, 104.0, 105.0],
                "volume": [1000, 1100, 1200, 1300, 1400, 1500],
            }
        )
        equity = pl.DataFrame({"date": dates, "equity": [100_000.0] * len(dates)})

        result = compute_metrics(
            equity_df=equity,
            trades=[],
            strategy_name="Data quality",
            symbols=["DQ"],
            symbol_dfs={"DQ": ohlcv},
            requested_start="2024-01-02",
            requested_end="2024-01-08",
        )

        expected_rows = expected_trading_days("2024-01-02", "2024-01-08")
        dq = result.data_quality
        assert dq["requested_start"] == "2024-01-02"
        assert dq["requested_end"] == "2024-01-08"
        assert dq["total_rows"] == 6
        assert dq["total_expected_rows"] == expected_rows
        assert dq["total_zero_or_null_prices"] == 2
        assert dq["symbols"]["DQ"]["actual_start"] == "2024-01-02"
        assert dq["symbols"]["DQ"]["actual_end"] == "2024-01-07"
        assert dq["symbols"]["DQ"]["zero_or_null_prices"] == 2

    def test_expected_trading_days_scales_by_intraday_timeframe(self) -> None:
        """Intraday expected rows should scale by bars per trading day."""
        daily = expected_trading_days("2024-01-02", "2024-02-02", "daily")
        five_min = expected_trading_days("2024-01-02", "2024-02-02", "5min")
        hourly = expected_trading_days("2024-01-02", "2024-02-02", "1hour")

        assert five_min == daily * 78
        assert hourly == daily * 7

    def test_invalid_requested_dates_do_not_break_quality_metadata(self) -> None:
        """Invalid requested dates should produce zero expected rows without crashing."""
        dates = [date(2024, 1, 2), date(2024, 1, 3)]
        ohlcv = pl.DataFrame(
            {
                "date": dates,
                "open": [99.0, 100.0],
                "high": [101.0, 102.0],
                "low": [98.0, 99.0],
                "close": [100.0, 101.0],
                "volume": [1000, 1100],
            }
        )
        equity = pl.DataFrame({"date": dates, "equity": [100_000.0, 100_100.0]})

        result = compute_metrics(
            equity_df=equity,
            trades=[],
            strategy_name="Invalid dates",
            symbols=["BAD"],
            symbol_dfs={"BAD": ohlcv},
            requested_start="not-a-date",
            requested_end="2024-01-03",
        )

        assert result.data_quality["total_expected_rows"] == 0
        assert result.data_quality["coverage_pct"] == 0.0

    def test_no_symbol_data_keeps_agent_eval_separate_from_conformance(self) -> None:
        """Calculation conformance should not require the agent-level eval harness."""
        dates = [date(2024, 1, 2), date(2024, 1, 3)]
        equity = pl.DataFrame({"date": dates, "equity": [100_000.0, 100_100.0]})

        result = compute_metrics(equity, [], "No symbol data", ["SEP"])

        assert result.data_quality == {}
