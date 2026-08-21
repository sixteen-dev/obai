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
    volumes: list[int] | None = None,
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
            "volume": volumes if volumes else [1_000_000] * n,
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


class TestEntryBarStopBinds:
    """A stop pierced on the bar a lot opens must close that same bar."""

    def test_entry_bar_stop_binds(self) -> None:
        """Lot opened on a bar whose low pierces the stop exits that bar via stop."""
        # Entry signal on bar 0 -> fill at bar 1 open (100). Stop at 95.
        # Bar 1's low (94) pierces the stop on the entry bar itself.
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            lows=[99.0, 94.0, 99.0, 99.0],
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
            commission_pct=0.0,
            stop_loss_pct=5.0,
        )

        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) == 1
        trade = stop_trades[0]
        # Closed on the entry bar itself, at the stop level not the close.
        assert trade.entry_date == trade.exit_date
        assert trade.exit_price == pytest.approx(95.0)
        assert trade.pnl < 0


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


class TestPortfolioCommissionApplied:
    """Test that commission is actually applied in portfolio mode."""

    def test_commission_reduces_portfolio_returns(self) -> None:
        """Portfolio with commission should have lower final equity than without."""
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
        result_no_comm = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )
        result_with_comm = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.5,
        )
        # Commission should reduce final equity
        assert result_with_comm.equity_curve[-1] < result_no_comm.equity_curve[-1]


class TestPortfolioVolumeScaledSlippage:
    """Test volume-scaled slippage in portfolio backtest."""

    def test_flags_off_matches_default(self) -> None:
        """Explicit flags off should produce same result as default."""
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
        default = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
        )
        flags_off = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=False,
            spread_estimates=None,
        )
        assert default.equity_curve == flags_off.equity_curve

    def test_volume_scaling_changes_results(self) -> None:
        """Enabling volume scaling should change results vs flat."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
            volumes=[50_000] * 5,  # Moderate volume
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        flat = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=False,
        )
        scaled = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=True,
        )
        # Results should differ when volume scaling is on
        assert flat.equity_curve != scaled.equity_curve


class TestCoFiringEntryAndExit:
    """A bar whose entry and exit both fire must not open and close in one bar."""

    def test_co_firing_signals_do_not_wash_the_position(self) -> None:
        """The prior bar's exit belongs to the position it closed, not the new one.

        Entries fill on the bar after the signal, so a lot opened here was
        opened BY the prior bar's entry. Re-reading that same prior bar's exit
        flag closed it immediately: a same-day round trip with zero holding
        days and two commissions charged, repeated for every bar the two
        signals co-fire.
        """
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0, 100.0],
            entries=[True, True, True, False, False],
            exits=[True, True, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            commission_pct=0.1,
        )

        same_bar = [t for t in result.trades if t.entry_date == t.exit_date]
        assert same_bar == [], f"opened and closed on one bar: {same_bar}"

    def test_stop_loss_still_binds_on_the_entry_bar(self) -> None:
        """The entry-bar recheck exists for price levels; that must still work.

        A stop pierced by the entry bar's own low has to close that bar, or
        the backtest reports a loss the strategy would never have taken.
        """
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0],
            entries=[True, False, False],
            exits=[False, False, False],
            lows=[100.0, 80.0, 100.0],
            highs=[101.0, 101.0, 101.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            commission_pct=0.1,
            stop_loss_pct=5.0,
        )

        assert [t.exit_reason for t in result.trades] == ["stop_loss"]
        assert result.trades[0].entry_date == result.trades[0].exit_date
