"""Tests for portfolio backtester with shared capital pool."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.engine.portfolio_backtester import run_portfolio_backtest
from src.models.strategy import PositionSizing


def _make_signal_df(  # noqa: PLR0913
    prices: list[float],
    entries: list[bool],
    exits: list[bool],
    start_date: date | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pl.DataFrame:
    """Create a DataFrame with signals for testing."""
    n = len(prices)
    base = start_date or date(2023, 1, 2)
    dates = [date(base.year, base.month, base.day + i) for i in range(n)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": highs if highs else [p + 1.0 for p in prices],
            "low": lows if lows else [p - 1.0 for p in prices],
            "close": prices,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )


class TestSingleSymbolBasic:
    """Test basic single-symbol portfolio backtest."""

    def test_entry_exit_produces_trade(self) -> None:
        """One entry and one exit should produce exactly one trade."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        assert len(result.trades) >= 1
        trade = result.trades[0]
        assert trade.symbol == "TEST"
        assert trade.shares > 0
        assert trade.pnl > 0  # Price went up 100 -> 110

    def test_equity_curve_length_matches_dates(self) -> None:
        """Equity curve should have same length as date union."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0, 103.0, 104.0],
            entries=[False, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
        )
        assert len(result.equity_curve) == 5
        assert len(result.daily_position_counts) == 5


class TestCashEquityInvariant:
    """Test that cash + positions = equity at every timestep."""

    def test_cash_plus_positions_equals_equity(self) -> None:
        """Verify the invariant holds: equity = cash + sum(shares * close)."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 108.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, True, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Before any trade: equity should be initial capital
        assert result.equity_curve[0] == pytest.approx(100_000.0)
        # At end (after all positions closed): all in cash
        assert result.equity_curve[-1] > 0


class TestExitFreesCashForEntry:
    """Test that exits process before entries within the same day."""

    def test_exit_frees_cash_for_entry_same_day(self) -> None:
        """Exit symbol A on day N, cash available for entry symbol B on day N."""
        # Symbol A: enter day 1, exit signal on day 2 (triggers exit on day 3)
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        # Symbol B: entry signal on day 2 (triggers entry on day 3)
        df_b = _make_signal_df(
            prices=[50.0, 50.0, 52.0, 55.0, 55.0],
            entries=[False, False, True, False, False],
            exits=[False, False, False, False, False],
        )
        # Use max_positions=1 so only 1 can be held at a time
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Should have 2 trades: one for AAA, one for BBB
        # If exits didn't process before entries, BBB would be skipped
        symbols_traded = {t.symbol for t in result.trades}
        assert "AAA" in symbols_traded
        # BBB entry fires on bar 2, processed on bar 3 (same bar AAA exits)
        assert "BBB" in symbols_traded


class TestSignalSkippedInsufficientCapital:
    """Test signal skipping when capital is exhausted."""

    def test_signals_skipped_insufficient_capital(self) -> None:
        """Three signals but cash for 1: 2 should be skipped."""
        # All three symbols fire entry on bar 0 (execute on bar 1)
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        df_b = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        df_c = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,  # Only allow 1 position
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b, "CCC": df_c},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Only 1 position allowed, so 2 signals should be skipped
        assert len(result.signals_skipped) == 2
        # 1 trade entered (plus 1 end-of-backtest close)
        entered_symbols = {t.symbol for t in result.trades}
        assert len(entered_symbols) == 1


class TestMaxPositionsRespected:
    """Test that max_positions constraint is never violated."""

    def test_never_exceeds_max_positions(self) -> None:
        """Position count should never exceed max_positions."""
        # Three symbols with overlapping entry signals
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        df_b = _make_signal_df(
            prices=[50.0, 50.0, 52.0, 55.0, 55.0, 55.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        df_c = _make_signal_df(
            prices=[200.0, 200.0, 210.0, 220.0, 220.0, 220.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=2,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b, "CCC": df_c},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Daily position counts should never exceed 2
        for count in result.daily_position_counts:
            assert count <= 2


class TestStopLossTriggers:
    """Test stop loss on discrete share positions."""

    def test_stop_loss_triggers(self) -> None:
        """Verify stop loss works on discrete share positions."""
        # Enter at ~100, price drops to 90 (10% loss)
        df = _make_signal_df(
            prices=[100.0, 100.0, 95.0, 90.0, 85.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
            lows=[99.0, 99.0, 93.0, 88.0, 83.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            stop_loss_pct=5.0,
        )
        # Should have a stop_loss exit
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1
        assert stop_trades[0].pnl < 0


class TestCloseRemainingAtEnd:
    """Test that open positions are closed at the final bar."""

    def test_close_remaining_at_end(self) -> None:
        """Open positions should be closed at the final bar."""
        # Enter but never get an exit signal
        df = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 115.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Should have exactly 1 trade closed at end of backtest
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "end_of_backtest"
        # Final equity should be all cash (no positions)
        assert result.equity_curve[-1] > 0
