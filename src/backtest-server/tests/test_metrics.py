"""Tests for performance and risk metric computation."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from src.engine.backtester import Trade
from src.engine.metrics import compute_metrics


class TestComputeMetrics:
    """Test metric computation from equity curves and trades."""

    def test_returns_backtest_result(
        self,
        equity_df: pl.DataFrame,
        sample_trades: list[Trade],
    ) -> None:
        """compute_metrics should return a BacktestResult."""
        result = compute_metrics(
            equity_df,
            sample_trades,
            strategy_name="Test",
            symbols=["AAPL"],
        )

        assert result.strategy_name == "Test"
        assert result.symbols == ["AAPL"]
        assert result.total_trades == 5

    def test_positive_cagr_for_uptrend(self, equity_df: pl.DataFrame) -> None:
        """Uptrending equity should produce positive CAGR."""
        result = compute_metrics(
            equity_df,
            [],
            strategy_name="Uptrend",
            symbols=["AAPL"],
        )

        assert result.cagr_pct > 0

    def test_sharpe_ratio_computed(self, equity_df: pl.DataFrame) -> None:
        """Sharpe ratio should be computed for valid equity curve."""
        result = compute_metrics(
            equity_df,
            [],
            strategy_name="Test",
            symbols=["AAPL"],
        )

        assert result.sharpe_ratio != 0.0

    def test_sharpe_uses_nonzero_rfr(self) -> None:
        """A nonzero risk-free rate lowers Sharpe by the annualized rf/sigma shift.

        Guards accuracy.md §5: Sharpe must be computed against the supplied
        risk-free rate, and the chosen rate must be surfaced on the result.
        """
        n = 252
        rng = np.random.default_rng(7)
        returns = 0.0008 + rng.normal(0.0, 0.006, n)
        equity = 100_000.0 * np.cumprod(1.0 + returns)
        dates = [date(2023, 1, 3) + timedelta(days=i) for i in range(n)]
        equity_df = pl.DataFrame({"date": dates, "equity": equity.tolist()})

        result_zero = compute_metrics(
            equity_df, [], strategy_name="RF", symbols=["X"], risk_free_rate=0.0
        )
        result_five = compute_metrics(
            equity_df, [], strategy_name="RF", symbols=["X"], risk_free_rate=0.05
        )

        # Positive-drift curve: charging a risk-free rate reduces excess return.
        assert result_five.sharpe_ratio < result_zero.sharpe_ratio

        # Golden gap: Sharpe_0 - Sharpe_rf = rf / (per-bar std * sqrt(bars/yr)),
        # matching _compute_sharpe's annualization for the daily timeframe.
        internal_returns = np.diff(equity) / equity[:-1]
        std = float(np.std(internal_returns, ddof=1))
        expected_gap = 0.05 / (std * np.sqrt(252))
        actual_gap = result_zero.sharpe_ratio - result_five.sharpe_ratio
        assert abs(actual_gap - expected_gap) < 1e-3

        # The chosen rate must be surfaced on the result for disclosure.
        assert result_five.risk_free_rate == 0.05

    def test_max_drawdown_negative(self, equity_df: pl.DataFrame) -> None:
        """Max drawdown should be negative (percentage decline)."""
        result = compute_metrics(
            equity_df,
            [],
            strategy_name="Test",
            symbols=["AAPL"],
        )

        assert result.max_drawdown_pct <= 0

    def test_win_rate_calculation(self, sample_trades: list[Trade]) -> None:
        """Win rate should reflect proportion of winning trades."""
        equity_df = pl.DataFrame(
            {
                "date": [__import__("datetime").date(2023, 1, 3 + i) for i in range(10)],
                "equity": [100_000.0 + i * 100 for i in range(10)],
            }
        )
        result = compute_metrics(
            equity_df,
            sample_trades,
            strategy_name="Test",
            symbols=["AAPL"],
        )

        # 3 wins, 2 losses out of 5 trades → 60%
        assert result.win_rate_pct == 60.0

    def test_profit_factor_computed(self, sample_trades: list[Trade]) -> None:
        """Profit factor should be gross profit / gross loss."""
        equity_df = pl.DataFrame(
            {
                "date": [__import__("datetime").date(2023, 1, 3 + i) for i in range(10)],
                "equity": [100_000.0] * 10,
            }
        )
        result = compute_metrics(
            equity_df,
            sample_trades,
            strategy_name="Test",
            symbols=["AAPL"],
        )

        # Gross profit: 10.0 + 9.68 + 9.09 = 28.77
        # Gross loss: |-5.0| + |-2.98| = 7.98
        expected_pf = 28.77 / 7.98
        assert abs(result.profit_factor - expected_pf) < 0.01

    def test_max_consecutive_losses(self) -> None:
        """Should correctly count max consecutive losses."""
        trades = [
            Trade("X", "2023-01-01", 100, "2023-01-10", 90, -10.0, 9, "signal"),
            Trade("X", "2023-01-15", 90, "2023-01-20", 85, -5.56, 5, "signal"),
            Trade("X", "2023-02-01", 85, "2023-02-15", 95, 11.76, 14, "signal"),
            Trade("X", "2023-03-01", 95, "2023-03-10", 92, -3.16, 9, "signal"),
        ]
        equity_df = pl.DataFrame(
            {
                "date": [__import__("datetime").date(2023, 1, 3 + i) for i in range(10)],
                "equity": [100_000.0] * 10,
            }
        )
        result = compute_metrics(
            equity_df,
            trades,
            strategy_name="Test",
            symbols=["X"],
        )

        assert result.max_consecutive_losses == 2  # First two trades are losses

    def test_no_trades_zero_stats(self) -> None:
        """No trades should give zero trading stats."""
        equity_df = pl.DataFrame(
            {
                "date": [__import__("datetime").date(2023, 1, 3 + i) for i in range(10)],
                "equity": [100_000.0] * 10,
            }
        )
        result = compute_metrics(
            equity_df,
            [],
            strategy_name="Test",
            symbols=["X"],
        )

        assert result.total_trades == 0
        assert result.win_rate_pct == 0.0
        assert result.profit_factor == 0.0

    def test_benchmark_metrics(self, equity_df: pl.DataFrame) -> None:
        """Benchmark metrics should be computed when benchmark_df is provided."""
        n = len(equity_df)
        rng = np.random.default_rng(99)
        bench_prices = 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, n))
        dates = equity_df["date"].to_list()

        benchmark_df = pl.DataFrame(
            {
                "date": dates,
                "close": bench_prices.tolist(),
                "symbol": ["SPY"] * n,
            }
        )

        result = compute_metrics(
            equity_df,
            [],
            strategy_name="Test",
            symbols=["AAPL"],
            benchmark_df=benchmark_df,
        )

        assert result.benchmark_return_pct != 0.0
        assert result.beta != 0.0

    def test_yearly_returns_computed(self) -> None:
        """Yearly returns should be computed from equity curve."""
        # Create 2-year equity curve
        n = 504
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(n)]
        equity = [100_000.0 + i * 50 for i in range(n)]

        eq_df = pl.DataFrame({"date": dates, "equity": equity})
        result = compute_metrics(eq_df, [], "Test", ["X"])

        assert len(result.yearly_returns) > 0
        assert "2022" in result.yearly_returns

    def test_data_points_tracked(self, equity_df: pl.DataFrame) -> None:
        """data_points_processed should match equity curve length."""
        result = compute_metrics(equity_df, [], "Test", ["X"])
        assert result.data_points_processed == len(equity_df)

    def test_to_dict_serialization(self, equity_df: pl.DataFrame) -> None:
        """Result should serialize to a nested dict."""
        result = compute_metrics(equity_df, [], "Test", ["X"])
        d = result.to_dict()

        assert "performance" in d
        assert "risk" in d
        assert "trading" in d
        assert "benchmark" in d
        assert d["performance"]["sharpe_ratio"] == result.sharpe_ratio


class TestDataQuality:
    """Test data quality metadata computation."""

    def test_quality_with_full_coverage(self) -> None:
        """Full-year data should produce ~100% coverage."""
        # ~252 trading days for 2023
        dates = [date(2023, 1, 3) + timedelta(days=i) for i in range(365)]
        trading_dates = [d for d in dates if d.weekday() < 5]  # noqa: PLR2004
        df = pl.DataFrame(
            {
                "date": trading_dates,
                "open": [100.0] * len(trading_dates),
                "high": [105.0] * len(trading_dates),
                "low": [95.0] * len(trading_dates),
                "close": [102.0] * len(trading_dates),
                "volume": [1000] * len(trading_dates),
            }
        )
        eq_df = pl.DataFrame(
            {
                "date": trading_dates,
                "equity": [100_000.0] * len(trading_dates),
            }
        )
        result = compute_metrics(
            eq_df,
            [],
            "Test",
            ["AAPL"],
            symbol_dfs={"AAPL": df},
            requested_start="2023-01-01",
            requested_end="2024-01-01",
        )

        dq = result.data_quality
        assert dq["coverage_pct"] > 90.0
        assert dq["total_zero_or_null_prices"] == 0
        assert "AAPL" in dq["symbols"]

    def test_quality_detects_zero_prices(self) -> None:
        """Zero close prices should be counted."""
        df = pl.DataFrame(
            {
                "date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)],
                "close": [100.0, 0.0, 100.0],
            }
        )
        eq_df = pl.DataFrame(
            {
                "date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)],
                "equity": [100_000.0] * 3,
            }
        )
        result = compute_metrics(
            eq_df,
            [],
            "Test",
            ["X"],
            symbol_dfs={"X": df},
            requested_start="2023-01-03",
            requested_end="2023-01-05",
        )

        assert result.data_quality["total_zero_or_null_prices"] == 1

    def test_quality_empty_when_no_symbol_dfs(self) -> None:
        """No symbol_dfs means empty data_quality dict."""
        eq_df = pl.DataFrame(
            {
                "date": [date(2023, 1, 3) + timedelta(days=i) for i in range(10)],
                "equity": [100_000.0 + i * 10 for i in range(10)],
            }
        )
        result = compute_metrics(eq_df, [], "Test", ["X"])
        assert result.data_quality == {}

    def test_quality_low_coverage_detected(self) -> None:
        """Sparse data should show low coverage percentage."""
        # Only 10 rows for a full year
        df = pl.DataFrame(
            {
                "date": [date(2023, 1, 3) + timedelta(days=i * 30) for i in range(10)],
                "close": [100.0] * 10,
            }
        )
        eq_df = pl.DataFrame(
            {
                "date": [date(2023, 1, 3) + timedelta(days=i * 30) for i in range(10)],
                "equity": [100_000.0] * 10,
            }
        )
        result = compute_metrics(
            eq_df,
            [],
            "Test",
            ["X"],
            symbol_dfs={"X": df},
            requested_start="2023-01-01",
            requested_end="2024-01-01",
        )

        assert result.data_quality["coverage_pct"] < 10.0
