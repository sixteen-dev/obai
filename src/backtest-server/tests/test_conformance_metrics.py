"""Conformance tests for performance, risk, and trade metrics."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from src.engine.backtester import BacktestConfig, Trade, run_backtest
from src.engine.metrics import (
    TRADING_DAYS_PER_YEAR,
    _compute_trading_stats,
    _convert_portfolio_trades,
    compute_metrics,
)
from src.engine.portfolio_backtester import PortfolioTradeRecord
from src.models.strategy import PositionSizing, RiskManagement
from tests.conformance_fixtures import (
    BENCHMARK_RETURNS,
    METRIC_GOLDEN,
    PERIOD_RETURNS,
    golden_benchmark_df,
    golden_equity_df,
    golden_metric_trades,
)


def _reference_downside_deviation(
    returns: np.ndarray,
    required_return: float,
    annualization: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Return empyrical-style annualized downside semi-deviation."""
    downside_diff = np.minimum(returns - required_return, 0.0)
    return float(np.sqrt(np.mean(np.square(downside_diff))) * np.sqrt(annualization))


class TestMetricConformance:
    """Golden metric checks anchored to empyrical and QuantStats conventions."""

    def test_performance_metrics_match_golden_fixture(self) -> None:
        """Core metrics should match committed deterministic golden values."""
        result = compute_metrics(
            equity_df=golden_equity_df(),
            trades=golden_metric_trades(),
            strategy_name="Conformance",
            symbols=["TST"],
            benchmark_df=golden_benchmark_df(),
            risk_free_rate=0.02,
        )

        assert result.total_return_pct == METRIC_GOLDEN["total_return_pct"]
        assert result.cagr_pct == METRIC_GOLDEN["cagr_pct"]
        assert result.sharpe_ratio == METRIC_GOLDEN["sharpe_ratio"]
        assert result.sortino_ratio == METRIC_GOLDEN["sortino_ratio"]
        assert result.calmar_ratio == METRIC_GOLDEN["calmar_ratio"]
        assert result.max_drawdown_pct == METRIC_GOLDEN["max_drawdown_pct"]
        assert result.max_drawdown_start == METRIC_GOLDEN["max_drawdown_start"]
        assert result.max_drawdown_end == METRIC_GOLDEN["max_drawdown_end"]
        assert result.annualized_volatility_pct == METRIC_GOLDEN["annualized_volatility_pct"]
        assert result.var_95_pct == METRIC_GOLDEN["var_95_pct"]
        assert result.downside_deviation_pct == METRIC_GOLDEN["downside_deviation_pct"]

    def test_benchmark_metrics_match_golden_fixture(self) -> None:
        """Benchmark alpha, beta, and information ratio should be deterministic."""
        result = compute_metrics(
            equity_df=golden_equity_df(),
            trades=golden_metric_trades(),
            strategy_name="Conformance",
            symbols=["TST"],
            benchmark_df=golden_benchmark_df(),
            risk_free_rate=0.02,
        )

        assert result.benchmark_symbol == "SPY"
        assert result.benchmark_return_pct == METRIC_GOLDEN["benchmark_return_pct"]
        assert result.benchmark_cagr_pct == METRIC_GOLDEN["benchmark_cagr_pct"]
        assert result.alpha_pct == METRIC_GOLDEN["alpha_pct"]
        assert result.beta == METRIC_GOLDEN["beta"]
        assert result.information_ratio == METRIC_GOLDEN["information_ratio"]

    def test_sortino_uses_downside_semideviation_reference_formula(self) -> None:
        """Sortino should use full-series downside risk, not std of negative returns only."""
        returns = np.array(PERIOD_RETURNS, dtype=np.float64)
        bar_rf = 0.02 / TRADING_DAYS_PER_YEAR
        downside = _reference_downside_deviation(returns, bar_rf)
        expected_sortino = (float(np.mean(returns)) - bar_rf) * TRADING_DAYS_PER_YEAR / downside

        result = compute_metrics(
            equity_df=golden_equity_df(),
            trades=[],
            strategy_name="Conformance",
            symbols=["TST"],
            risk_free_rate=0.02,
        )

        negative_only = returns[returns < 0]
        negative_only_sortino = (
            (float(np.mean(returns)) - bar_rf)
            / float(np.std(negative_only, ddof=1))
            * np.sqrt(TRADING_DAYS_PER_YEAR)
        )
        assert result.sortino_ratio == round(expected_sortino, 4)
        assert result.sortino_ratio != round(negative_only_sortino, 4)

    def test_trade_statistics_are_trade_based_not_period_based(self) -> None:
        """Win rate and profit factor should use closed trades, unlike QuantStats periods."""
        result = compute_metrics(
            equity_df=golden_equity_df(),
            trades=golden_metric_trades(),
            strategy_name="Conformance",
            symbols=["TST"],
        )

        period_win_rate = sum(ret > 0 for ret in PERIOD_RETURNS) / len(PERIOD_RETURNS) * 100
        assert result.win_rate_pct == METRIC_GOLDEN["win_rate_pct"]
        assert result.profit_factor == METRIC_GOLDEN["profit_factor"]
        assert result.avg_trade_return_pct == METRIC_GOLDEN["avg_trade_return_pct"]
        assert result.avg_holding_days == METRIC_GOLDEN["avg_holding_days"]
        assert result.max_consecutive_losses == METRIC_GOLDEN["max_consecutive_losses"]
        assert result.win_rate_pct != round(period_win_rate, 2)

    def test_metric_helpers_handle_flat_or_insufficient_series(self) -> None:
        """Flat or single-point equity should not emit NaN or infinite public metrics."""
        flat = pl.DataFrame(
            {
                "date": golden_equity_df()["date"].to_list(),
                "equity": [100_000.0] * len(golden_equity_df()),
            }
        )
        one_point = pl.DataFrame({"date": [golden_equity_df()["date"][0]], "equity": [100_000.0]})

        flat_result = compute_metrics(flat, [], "Flat", ["TST"])
        one_point_result = compute_metrics(one_point, [], "One point", ["TST"])

        assert flat_result.sharpe_ratio == 0.0
        assert flat_result.sortino_ratio == 0.0
        assert flat_result.max_drawdown_pct == 0.0
        assert one_point_result.total_return_pct == 0.0
        assert one_point_result.sharpe_ratio == 0.0

    def test_sortino_returns_zero_when_no_downside_observations_exist(self) -> None:
        """Positive-only returns should not divide by zero or emit infinity."""
        dates = golden_equity_df()["date"].to_list()[:5]
        equity = pl.DataFrame(
            {
                "date": dates,
                "equity": [100_000.0, 101_000.0, 102_010.0, 103_030.1, 104_060.401],
            }
        )

        result = compute_metrics(equity, [], "Positive only", ["TST"], risk_free_rate=0.0)

        assert result.sortino_ratio == 0.0
        assert np.isfinite(result.sharpe_ratio)

    def test_reference_arrays_are_same_length(self) -> None:
        """Strategy and benchmark fixtures should stay aligned."""
        assert len(PERIOD_RETURNS) == len(BENCHMARK_RETURNS)
        assert len(golden_equity_df()) == len(golden_benchmark_df())
        assert pytest.approx(golden_equity_df()["equity"][0]) == 100_000.0


class TestTradeLedgerConformance:
    """Sign and monetary-basis conventions for derived trade statistics."""

    def test_losing_strategy_reports_a_negative_calmar_ratio(self) -> None:
        """Calmar is CAGR over the drawdown magnitude, so a losing run stays negative."""
        equity = pl.DataFrame(
            {
                "date": [date(2023, 1, 2), date(2024, 1, 2)],
                "equity": [1000.0, 900.0],
            }
        )

        result = compute_metrics(equity, [], "Falling", ["TST"])

        assert result.cagr_pct < 0
        assert result.max_drawdown_pct < 0
        assert result.calmar_ratio < 0
        assert result.calmar_ratio == pytest.approx(-1.0006)

    def test_zero_drawdown_reports_calmar_as_zero(self) -> None:
        """A run that never drew down has no denominator, so Calmar is the 0.0 convention."""
        equity = pl.DataFrame(
            {
                "date": [date(2023, 1, 2), date(2024, 1, 2)],
                "equity": [1000.0, 1100.0],
            }
        )

        result = compute_metrics(equity, [], "Rising", ["TST"])

        assert result.max_drawdown_pct == 0.0
        assert result.calmar_ratio == 0.0

    def test_portfolio_profit_factor_uses_dollar_pnl_not_percent(self) -> None:
        """A +$20/+10% winner against a -$10/-10% loser is a profit factor of 2, not 1."""
        records = [
            PortfolioTradeRecord(
                "A", "2024-01-02", 10.0, "2024-01-03", 11.0, 20, 10.0, 20.0, "signal", 1
            ),
            PortfolioTradeRecord(
                "B", "2024-01-04", 100.0, "2024-01-05", 90.0, 1, -10.0, -10.0, "signal", 1
            ),
        ]

        trades = _convert_portfolio_trades(records)

        assert [t.pnl for t in trades] == [20.0, -10.0]
        assert _compute_trading_stats(trades).profit_factor == 2.0

    def test_percent_basis_still_applies_when_no_trade_carries_dollar_pnl(self) -> None:
        """Trades without a dollar PnL keep the percentage basis rather than being dropped."""
        trades = [
            Trade("A", "2024-01-02", 10.0, "2024-01-03", 11.0, 10.0, 1, "signal"),
            Trade("B", "2024-01-04", 100.0, "2024-01-05", 90.0, -10.0, 1, "signal"),
        ]

        assert all(t.pnl is None for t in trades)
        assert _compute_trading_stats(trades).profit_factor == 1.0

    def test_single_symbol_trades_carry_dollar_pnl(self) -> None:
        """The single-symbol engine sizes a notional, so its trades report dollars too."""
        frame = pl.DataFrame(
            {
                "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(4)],
                "open": [100.0, 100.0, 110.0, 110.0],
                "high": [100.0, 100.0, 110.0, 110.0],
                "low": [100.0, 100.0, 110.0, 110.0],
                "close": [100.0, 100.0, 110.0, 110.0],
                "volume": [1_000_000] * 4,
                "entry_signal": [True, False, False, False],
                "exit_signal": [False, False, True, False],
            }
        )
        sizing = PositionSizing(method="fixed_pct", max_position_pct=50, max_positions=1)
        config = BacktestConfig(initial_capital=1000, slippage_pct=0, commission_pct=0)

        _equity, trades = run_backtest(frame, sizing, RiskManagement(), config)

        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(500.0 * trades[0].return_pct / 100)
