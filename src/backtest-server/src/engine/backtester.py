"""Core backtest loop — vectorized with numpy for stop-loss/take-profit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from ..models.strategy import PositionSizing, RiskManagement


@dataclass
class Trade:
    """A single completed trade."""

    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    return_pct: float
    holding_days: int
    exit_reason: str  # "signal", "stop_loss", "take_profit"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "return_pct": round(self.return_pct, 4),
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason,
        }


@dataclass
class BacktestConfig:
    """Configuration bundle for backtest execution."""

    symbol: str = ""
    slippage_pct: float = 0.1
    commission_pct: float = 0.1


def run_backtest(
    df: pl.DataFrame,
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    config: BacktestConfig | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Run backtest on a single symbol with signals already computed.

    Signals on close[t], trades execute on open[t+1] (no look-ahead bias).

    Args:
        df: DataFrame with date, open, close, entry_signal, exit_signal columns.
        position_sizing: Position sizing configuration.
        risk_management: Risk management parameters.
        config: Optional backtest config (symbol, slippage, commission).

    Returns:
        Tuple of (equity curve DataFrame, list of Trade objects).

    """
    cfg = config or BacktestConfig()
    return _execute_backtest(df, position_sizing, risk_management, cfg)


def _execute_backtest(
    df: pl.DataFrame,
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    cfg: BacktestConfig,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Execute the backtest loop (internal implementation)."""
    market = _MarketData(
        dates=df["date"].to_list(),
        opens=df["open"].to_numpy().astype(np.float64),
        closes=df["close"].to_numpy().astype(np.float64),
        entries=df["entry_signal"].to_numpy(),
        exits=df["exit_signal"].to_numpy(),
    )

    n = len(market.dates)
    equity = np.ones(n, dtype=np.float64) * 100_000.0
    trades: list[Trade] = []

    state = _BacktestState(
        stop_loss=risk_management.stop_loss_pct,
        take_profit=risk_management.take_profit_pct,
        position_size=position_sizing.max_position_pct / 100.0,
    )

    for i in range(1, n):
        equity[i] = equity[i - 1]
        if not state.in_position:
            _check_entry(state, market, equity, i, cfg.slippage_pct)
        else:
            _check_exit(state, market, equity, trades, i, cfg)

    equity_df = pl.DataFrame(
        {
            "date": market.dates,
            "equity": equity.tolist(),
        }
    )
    return equity_df, trades


@dataclass
class _MarketData:
    """Bundled market data arrays for the backtest loop."""

    dates: list[Any]
    opens: np.ndarray[Any, np.dtype[np.float64]]
    closes: np.ndarray[Any, np.dtype[np.float64]]
    entries: np.ndarray[Any, np.dtype[Any]]
    exits: np.ndarray[Any, np.dtype[Any]]


@dataclass
class _BacktestState:
    """Mutable state for the backtest loop."""

    stop_loss: float | None
    take_profit: float | None
    position_size: float
    in_position: bool = False
    entry_price: float = 0.0
    entry_idx: int = 0
    last_price: float = 0.0


def _check_entry(
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
    slippage_pct: float,
) -> None:
    """Check and execute entry signal from previous bar."""
    if market.entries[idx - 1]:
        state.entry_price = float(market.opens[idx]) * (1 + slippage_pct / 100)
        state.entry_idx = idx
        state.in_position = True
        state.last_price = state.entry_price
        # Entry-day PnL: entry_price → close[entry_day]
        day_pnl = (market.closes[idx] - state.entry_price) / state.entry_price
        equity[idx] += equity[idx - 1] * state.position_size * day_pnl
        state.last_price = float(market.closes[idx])


def _check_exit(  # noqa: PLR0913
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    trades: list[Trade],
    idx: int,
    cfg: BacktestConfig,
) -> None:
    """Check exit conditions and update equity."""
    ret = (market.closes[idx] - state.entry_price) / state.entry_price
    exit_reason = _get_exit_reason(state, ret, market.exits, idx)

    if exit_reason:
        _record_trade(state, market, equity, trades, idx, exit_reason, cfg)
    else:
        pnl = (market.closes[idx] - state.last_price) / state.last_price
        equity[idx] += equity[idx - 1] * state.position_size * pnl
        state.last_price = float(market.closes[idx])


def _get_exit_reason(
    state: _BacktestState,
    current_return: Any,
    exits: np.ndarray[Any, np.dtype[Any]],
    idx: int,
) -> str:
    """Determine exit reason from stop/take-profit/signal."""
    if state.stop_loss and current_return <= -(state.stop_loss / 100):
        return "stop_loss"
    if state.take_profit and current_return >= (state.take_profit / 100):
        return "take_profit"
    if exits[idx - 1]:
        return "signal"
    return ""


def _record_trade(  # noqa: PLR0913
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    trades: list[Trade],
    idx: int,
    exit_reason: str,
    cfg: BacktestConfig,
) -> None:
    """Record a completed trade and update equity."""
    if exit_reason == "signal":
        exit_price = float(market.opens[idx]) * (1 - cfg.slippage_pct / 100)
    else:
        exit_price = float(market.closes[idx])

    # Full trade return (for Trade record only)
    trade_return = (exit_price - state.entry_price) / state.entry_price
    trade_return -= cfg.commission_pct / 100 * 2

    # Exit-day equity: only the last leg (last_price → exit_price) + commission
    exit_day_pnl = (exit_price - state.last_price) / state.last_price
    exit_day_pnl -= cfg.commission_pct / 100 * 2
    equity[idx] += equity[idx - 1] * state.position_size * exit_day_pnl
    holding = _compute_holding_days(market.dates, state.entry_idx, idx)

    trades.append(
        Trade(
            symbol=cfg.symbol,
            entry_date=_date_to_str(market.dates[state.entry_idx]),
            entry_price=state.entry_price,
            exit_date=_date_to_str(market.dates[idx]),
            exit_price=exit_price,
            return_pct=trade_return * 100,
            holding_days=holding,
            exit_reason=exit_reason,
        )
    )
    state.in_position = False


def _compute_holding_days(
    dates: list[Any],
    entry_idx: int,
    exit_idx: int,
) -> int:
    """Compute holding period in calendar days."""
    entry_date = dates[entry_idx]
    exit_date = dates[exit_idx]
    if isinstance(exit_date, date) and isinstance(entry_date, date):
        return (exit_date - entry_date).days
    return exit_idx - entry_idx


def run_multi_symbol_backtest(
    symbol_dfs: dict[str, pl.DataFrame],
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    config: BacktestConfig | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Run backtest across multiple symbols and combine equity curves.

    Args:
        symbol_dfs: Dict of symbol → DataFrame with signals computed.
        position_sizing: Position sizing configuration.
        risk_management: Risk management parameters.
        config: Optional backtest config (slippage, commission).

    Returns:
        Tuple of (combined equity curve, all trades).

    """
    cfg = config or BacktestConfig()
    all_trades: list[Trade] = []
    equity_curves: list[pl.DataFrame] = []

    for symbol, sym_df in symbol_dfs.items():
        sym_cfg = BacktestConfig(
            symbol=symbol,
            slippage_pct=cfg.slippage_pct,
            commission_pct=cfg.commission_pct,
        )
        eq_curve, trades = run_backtest(
            df=sym_df,
            position_sizing=position_sizing,
            risk_management=risk_management,
            config=sym_cfg,
        )
        equity_curves.append(
            eq_curve.rename({"equity": f"equity_{symbol}"}),
        )
        all_trades.extend(trades)

    if not equity_curves:
        return pl.DataFrame({"date": [], "equity": []}), []

    combined = _combine_equity_curves(equity_curves)
    return combined, all_trades


def _combine_equity_curves(
    curves: list[pl.DataFrame],
) -> pl.DataFrame:
    """Combine per-symbol equity curves into a single averaged curve."""
    combined = curves[0]
    for ec in curves[1:]:
        combined = combined.join(ec, on="date", how="outer_coalesce")  # type: ignore[arg-type]  # polars stubs lag behind

    equity_cols = [c for c in combined.columns if c.startswith("equity_")]
    return combined.with_columns(
        pl.mean_horizontal(*[pl.col(c) for c in equity_cols]).alias(
            "equity",
        ),
    ).select(["date", "equity"])


def _date_to_str(val: Any) -> str:
    """Convert a date value to ISO string."""
    if isinstance(val, date):
        return val.isoformat()
    return str(val)
