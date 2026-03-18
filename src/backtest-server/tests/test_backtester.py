"""Tests for core backtest loop."""

from __future__ import annotations

from datetime import date

import polars as pl

from src.engine.backtester import BacktestConfig, Trade, run_backtest, run_multi_symbol_backtest
from src.models.strategy import PositionSizing, RiskManagement


def _make_signal_df(
    prices: list[float],
    entries: list[bool],
    exits: list[bool],
) -> pl.DataFrame:
    """Create a DataFrame with signals for testing."""
    n = len(prices)
    dates = [date(2023, 1, i + 2) for i in range(n)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": prices,
            "close": prices,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )


class TestRunBacktest:
    """Test the core backtest loop."""

    def test_no_trades_with_no_signals(self) -> None:
        """No entry signals should produce no trades."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0, 103.0, 104.0],
            entries=[False, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        equity_df, trades = run_backtest(df, sizing, risk)

        assert len(trades) == 0
        assert len(equity_df) == 5
        # Equity should be flat at 100k
        assert equity_df["equity"][0] == 100_000.0
        assert equity_df["equity"][-1] == 100_000.0

    def test_single_trade_entry_exit(self) -> None:
        """Entry then exit signal should produce exactly one trade."""
        df = _make_signal_df(
            prices=[100.0, 102.0, 104.0, 106.0, 108.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(symbol="TEST", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].symbol == "TEST"
        assert trades[0].exit_reason == "signal"

    def test_stop_loss_triggers(self) -> None:
        """Stop loss should exit when price drops sufficiently."""
        # Enter at 100, then price drops to 90 (10% loss)
        df = _make_signal_df(
            prices=[100.0, 100.0, 95.0, 90.0, 85.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement(stop_loss_pct=5.0)  # 5% stop loss

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) >= 1
        assert trades[0].exit_reason == "stop_loss"

    def test_take_profit_triggers(self) -> None:
        """Take profit should exit when price rises sufficiently."""
        # Enter at 100, price rises to 120 (20% gain)
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 120.0, 125.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement(take_profit_pct=15.0)  # 15% take profit

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) >= 1
        assert trades[0].exit_reason == "take_profit"

    def test_equity_starts_at_100k(self) -> None:
        """Equity curve should start at 100,000."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )
        eq_df, _ = run_backtest(df, PositionSizing(), RiskManagement())

        assert eq_df["equity"][0] == 100_000.0

    def test_slippage_reduces_returns(self) -> None:
        """Slippage should reduce trade returns."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades_no_slip = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )
        _, trades_with_slip = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.5, commission_pct=0.0),
        )

        # With slippage, returns should be worse
        assert trades_with_slip[0].return_pct < trades_no_slip[0].return_pct

    def test_commission_reduces_returns(self) -> None:
        """Commission should reduce trade returns."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades_no_comm = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )
        _, trades_with_comm = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.5),
        )

        assert trades_with_comm[0].return_pct < trades_no_comm[0].return_pct


class TestTradeDataclass:
    """Test Trade serialization."""

    def test_to_dict(self) -> None:
        """Trade should serialize to dict with rounded values."""
        trade = Trade(
            symbol="AAPL",
            entry_date="2023-01-10",
            entry_price=150.123,
            exit_date="2023-02-15",
            exit_price=165.456,
            return_pct=10.2345,
            holding_days=36,
            exit_reason="signal",
        )
        d = trade.to_dict()
        assert d["entry_price"] == 150.12
        assert d["exit_price"] == 165.46
        assert d["return_pct"] == 10.2345


class TestMultiSymbolBacktest:
    """Test multi-symbol backtest."""

    def test_combines_equity_curves(self) -> None:
        """Multi-symbol should produce a single combined equity curve."""
        df1 = _make_signal_df(
            prices=[100.0, 102.0, 104.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )
        df2 = _make_signal_df(
            prices=[50.0, 51.0, 52.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )

        combined_eq, trades = run_multi_symbol_backtest(
            {"AAPL": df1, "MSFT": df2},
            PositionSizing(),
            RiskManagement(),
        )

        assert "equity" in combined_eq.columns
        assert "date" in combined_eq.columns
        assert len(trades) == 0

    def test_empty_symbols_returns_empty(self) -> None:
        """No symbols should return empty results."""
        combined_eq, trades = run_multi_symbol_backtest(
            {},
            PositionSizing(),
            RiskManagement(),
        )

        assert combined_eq.is_empty()
        assert len(trades) == 0
