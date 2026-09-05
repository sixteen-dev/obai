"""Conformance tests for fill timing, slippage, commission, and portfolio semantics."""

from __future__ import annotations

import pytest

from src.engine.backtester import (
    BacktestConfig,
    compute_entry_fill,
    compute_exit_fill,
    run_backtest,
)
from src.engine.portfolio_backtester import run_portfolio_backtest
from src.models.strategy import FILL_MODEL, PositionSizing, RiskManagement
from tests.conformance_fixtures import signal_df


class TestExecutionSemantics:
    """Backtest execution semantics borrowed from common event-driven engines."""

    def test_close_signal_executes_at_next_bar_open_without_lookahead(self) -> None:
        """Entry and exit signals from close[t] should fill at open[t+1]."""
        df = signal_df(
            opens=[100.0, 90.0, 95.0, 130.0, 125.0],
            closes=[1000.0, 91.0, 96.0, 131.0, 126.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(),
            BacktestConfig(symbol="NL", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].entry_date == "2024-01-03"
        assert trades[0].entry_price == 90.0
        assert trades[0].exit_date == "2024-01-05"
        assert trades[0].exit_price == 130.0

    def test_stop_gap_through_fills_at_worse_open(self) -> None:
        """Sell stop should fill at the open when price gaps below the stop level."""
        df = signal_df(
            opens=[100.0, 100.0, 90.0, 91.0],
            closes=[100.0, 100.0, 91.0, 91.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=[101.0, 101.0, 92.0, 92.0],
            lows=[99.0, 99.0, 88.0, 90.0],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(stop_loss_pct=5.0),
            BacktestConfig(symbol="GAP", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == 90.0

    def test_stop_precedes_take_profit_when_same_bar_hits_both(self) -> None:
        """Same-bar stop and target collision should choose the conservative stop path."""
        df = signal_df(
            opens=[100.0, 100.0, 100.0, 100.0],
            closes=[100.0, 100.0, 100.0, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=[101.0, 101.0, 110.0, 101.0],
            lows=[99.0, 99.0, 94.0, 99.0],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(stop_loss_pct=5.0, take_profit_pct=5.0),
            BacktestConfig(symbol="BOTH", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == 95.0

    def test_scheduled_signal_exit_fills_before_a_later_intraday_target(self) -> None:
        """A previous-bar exit signal fills at the open, ahead of the bar's high."""
        df = signal_df(
            opens=[100.0, 100.0, 100.0],
            closes=[100.0, 100.0, 100.0],
            entries=[True, False, False],
            exits=[False, True, False],
            highs=[100.0, 100.0, 120.0],
            lows=[100.0, 100.0, 100.0],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(take_profit_pct=10.0),
            BacktestConfig(symbol="SIG", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "signal"
        assert trades[0].exit_price == pytest.approx(100.0)

    def test_fixed_notional_round_trip_leaves_equity_unchanged(self) -> None:
        """A held position is marked as fixed shares, not re-levered every bar."""
        df = signal_df(
            opens=[100.0, 100.0, 200.0, 100.0, 100.0],
            closes=[100.0, 100.0, 200.0, 100.0, 100.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, True, False],
        )

        equity, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=50.0),
            RiskManagement(),
            BacktestConfig(
                symbol="MARK",
                slippage_pct=0.0,
                commission_pct=0.0,
                initial_capital=1000.0,
            ),
        )

        # 5 shares bought with $500 of $1,000: equity = 1000 + 5 * (price - 100).
        assert equity["equity"].to_list() == pytest.approx([1000.0, 1000.0, 1500.0, 1000.0, 1000.0])
        assert len(trades) == 1
        assert trades[0].return_pct == pytest.approx(0.0)


class TestCostsAndFills:
    """Golden checks for execution cost calculations."""

    def test_flat_slippage_makes_buy_and_sell_prices_worse(self) -> None:
        """Flat slippage should increase buys and decrease signal exits."""
        assert compute_entry_fill(100.0, 0.25) == pytest.approx(100.25)
        assert compute_exit_fill("signal", 100.0, 99.0, None, None, 0.25) == pytest.approx(99.75)

    def test_spread_cost_applies_half_spread_to_each_side(self) -> None:
        """Half-spread is added for buys and subtracted for signal exits."""
        assert compute_entry_fill(100.0, 0.0, spread_cost=0.005) == pytest.approx(100.5)
        assert compute_exit_fill(
            "signal",
            100.0,
            99.0,
            None,
            None,
            0.0,
            spread_cost=0.005,
        ) == pytest.approx(99.5)

    def test_volume_scaled_slippage_uses_square_root_participation(self) -> None:
        """At 4 percent participation, slippage should be twice the 1 percent reference."""
        fill = compute_entry_fill(100.0, 0.1, order_shares=400.0, bar_volume=10_000)
        assert fill == pytest.approx(100.2)

    def test_commission_is_round_trip_percent_of_trade_value(self) -> None:
        """A 0.5 percent commission on entry and exit should reduce return by 1 percent."""
        df = signal_df(
            opens=[100.0, 100.0, 110.0, 110.0],
            closes=[100.0, 100.0, 110.0, 110.0],
            entries=[True, False, False, False],
            exits=[False, False, True, False],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(),
            BacktestConfig(symbol="COMM", slippage_pct=0.0, commission_pct=0.5),
        )

        assert len(trades) == 1
        assert trades[0].return_pct == pytest.approx(9.0)


class TestPortfolioExecutionSemantics:
    """Shared-capital portfolio conformance checks."""

    def test_portfolio_uses_discrete_shares_and_exact_cash_pnl(self) -> None:
        """Portfolio mode should floor shares and leave remainder as cash."""
        df = signal_df(
            opens=[100.0, 100.0, 110.0, 110.0],
            closes=[100.0, 100.0, 110.0, 110.0],
            entries=[True, False, False, False],
            exits=[False, False, True, False],
        )
        sizing = PositionSizing(
            method="fixed_pct",
            max_position_pct=50.0,
            max_positions=2,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"PORT": df},
            initial_capital=10_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert len(result.trades) == 1
        assert result.trades[0].shares == 50
        assert result.trades[0].pnl == pytest.approx(500.0)
        assert result.equity_curve[-1] == pytest.approx(10_500.0)

    def test_portfolio_processes_exits_before_new_entries_same_day(self) -> None:
        """Cash freed by an exit should be available for entries on the same bar."""
        held = signal_df(
            opens=[100.0, 100.0, 105.0, 110.0, 110.0],
            closes=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        replacement = signal_df(
            opens=[50.0, 50.0, 52.0, 55.0, 55.0],
            closes=[50.0, 50.0, 52.0, 55.0, 55.0],
            entries=[False, False, True, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": held, "BBB": replacement},
            initial_capital=10_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert {trade.symbol for trade in result.trades} == {"AAA", "BBB"}

    def test_one_symbol_strategy_reconciles_across_allocation_modes(self) -> None:
        """A one-symbol strategy must produce the same ledger in both engines.

        The fixture is chosen so 50 percent of 10,000 at a price of 100 is a
        whole 50 shares. Any fixture where notional divided by price is not
        integral will NOT reconcile: the portfolio engine floors shares and
        leaves the remainder in cash, while the single-symbol engine sizes by
        notional alone.
        """
        df = signal_df(
            opens=[100.0, 100.0, 110.0, 110.0],
            closes=[100.0, 100.0, 110.0, 110.0],
            entries=[True, False, False, False],
            exits=[False, False, True, False],
        )

        equity, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=50.0),
            RiskManagement(),
            BacktestConfig(
                symbol="R",
                slippage_pct=0.0,
                commission_pct=0.0,
                initial_capital=10_000.0,
            ),
        )
        portfolio = run_portfolio_backtest(
            signal_dfs={"R": df},
            initial_capital=10_000.0,
            position_sizing=PositionSizing(
                method="fixed_pct",
                max_position_pct=50.0,
                max_positions=2,
                allocation_mode="portfolio",
            ),
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        expected_equity = [10_000.0, 10_000.0, 10_500.0, 10_500.0]
        assert equity["equity"].to_list() == expected_equity
        assert portfolio.equity_curve == expected_equity
        assert equity["equity"].to_list() == portfolio.equity_curve

        assert len(trades) == 1
        assert len(portfolio.trades) == 1
        assert trades[0].pnl == pytest.approx(500.0)
        assert trades[0].return_pct == pytest.approx(10.0)
        assert portfolio.trades[0].shares == 50
        assert portfolio.trades[0].pnl == pytest.approx(500.0)
        assert portfolio.trades[0].return_pct == pytest.approx(10.0)
        assert trades[0].pnl == portfolio.trades[0].pnl


def test_fill_model_names_trailing_and_time_stops() -> None:
    """The published fill model must name every exit whose fill it governs."""
    assert "trailing" in FILL_MODEL
    assert "time stop" in FILL_MODEL
