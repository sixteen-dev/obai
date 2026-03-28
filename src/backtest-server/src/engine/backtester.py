"""Core backtest loop — vectorized with numpy, intrabar stop/TP, EOD close.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phases 3.2-3.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from ..models.strategy import PositionSizing, RiskManagement
from .session import is_after_time, is_last_bar_of_session, parse_time_str
from .utils import date_to_str

# --- Shared pure functions (importable by portfolio backtester) ---


def compute_entry_fill(open_price: float, slippage_pct: float) -> float:
    """Compute fill price for entry (buy). Slippage makes it worse (higher).

    Args:
        open_price: The bar's open price.
        slippage_pct: Slippage as a percentage (e.g. 0.1 for 0.1%).

    Returns:
        Adjusted fill price.

    """
    return open_price * (1 + slippage_pct / 100)


def compute_exit_fill(  # noqa: PLR0913
    reason: str,
    open_price: float,
    close_price: float,
    stop_level: float | None,
    tp_level: float | None,
    slippage_pct: float,
) -> float:
    """Compute fill price for exit based on exit reason.

    Args:
        reason: Exit reason (signal, stop_loss, take_profit, eod_close).
        open_price: The bar's open price.
        close_price: The bar's close price.
        stop_level: Stop-loss price level (if applicable).
        tp_level: Take-profit price level (if applicable).
        slippage_pct: Slippage as a percentage.

    Returns:
        Fill price for the exit.

    """
    if reason == "signal":
        return open_price * (1 - slippage_pct / 100)
    if reason == "stop_loss" and stop_level is not None:
        return min(stop_level, open_price)
    if reason == "take_profit" and tp_level is not None:
        return max(tp_level, open_price)
    # eod_close and fallback
    return close_price


def check_intrabar_stop(
    entry_price: float,
    low_price: float,
    stop_pct: float,
) -> tuple[bool, float]:
    """Check if stop loss was hit intrabar.

    Args:
        entry_price: Entry price of the position.
        low_price: The bar's low price.
        stop_pct: Stop-loss percentage.

    Returns:
        Tuple of (hit, stop_level).

    """
    stop_level = entry_price * (1 - stop_pct / 100)
    return low_price <= stop_level, stop_level


def check_intrabar_tp(
    entry_price: float,
    high_price: float,
    tp_pct: float,
) -> tuple[bool, float]:
    """Check if take profit was hit intrabar.

    Args:
        entry_price: Entry price of the position.
        high_price: The bar's high price.
        tp_pct: Take-profit percentage.

    Returns:
        Tuple of (hit, tp_level).

    """
    tp_level = entry_price * (1 + tp_pct / 100)
    return high_price >= tp_level, tp_level


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
    exit_reason: str  # "signal", "stop_loss", "take_profit", "eod_close"
    holding_minutes: int = 0  # Phase 3.2: intraday precision
    timeframe: str = "daily"  # Phase 3.2: record timeframe

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict.

        Conditionally includes intraday fields so daily output is unchanged.
        """
        d: dict[str, Any] = {
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "return_pct": round(self.return_pct, 4),
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason,
        }
        if self.timeframe != "daily":
            d["holding_minutes"] = self.holding_minutes
            d["timeframe"] = self.timeframe
        return d


@dataclass
class BacktestConfig:
    """Configuration bundle for backtest execution."""

    symbol: str = ""
    slippage_pct: float = 0.1
    commission_pct: float = 0.1
    timeframe: str = "daily"  # Phase 3: threaded through


def run_backtest(
    df: pl.DataFrame,
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    config: BacktestConfig | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Run backtest on a single symbol with signals already computed.

    Signals on close[t], trades execute on open[t+1] (no look-ahead bias).

    Args:
        df: DataFrame with date, open, high, low, close, entry_signal, exit_signal.
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
        highs=df["high"].to_numpy().astype(np.float64),
        lows=df["low"].to_numpy().astype(np.float64),
        closes=df["close"].to_numpy().astype(np.float64),
        entries=df["entry_signal"].to_numpy(),
        exits=df["exit_signal"].to_numpy(),
    )

    n = len(market.dates)
    equity = np.ones(n, dtype=np.float64) * 100_000.0
    trades: list[Trade] = []

    # Parse no_entry_after cutoff time if configured
    no_entry_cutoff = None
    if risk_management.no_entry_after:
        no_entry_cutoff = parse_time_str(risk_management.no_entry_after)

    state = _BacktestState(
        stop_loss=risk_management.stop_loss_pct,
        take_profit=risk_management.take_profit_pct,
        position_size=position_sizing.max_position_pct / 100.0,
        close_eod=risk_management.close_eod,
    )

    for i in range(1, n):
        equity[i] = equity[i - 1]
        if not state.in_position:
            _check_entry(state, market, equity, i, cfg, no_entry_cutoff)
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
    highs: np.ndarray[Any, np.dtype[np.float64]]
    lows: np.ndarray[Any, np.dtype[np.float64]]
    closes: np.ndarray[Any, np.dtype[np.float64]]
    entries: np.ndarray[Any, np.dtype[Any]]
    exits: np.ndarray[Any, np.dtype[Any]]


@dataclass
class _BacktestState:
    """Mutable state for the backtest loop."""

    stop_loss: float | None
    take_profit: float | None
    position_size: float
    close_eod: bool = False
    in_position: bool = False
    entry_price: float = 0.0
    entry_idx: int = 0
    last_price: float = 0.0
    stop_level: float | None = None
    tp_level: float | None = None


def _check_entry(  # noqa: PLR0913
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
    cfg: BacktestConfig,
    no_entry_cutoff: Any | None,
) -> None:
    """Check and execute entry signal from previous bar."""
    if not market.entries[idx - 1]:
        return

    # Phase 3.4: no_entry_after time filter
    if no_entry_cutoff is not None:
        bar_time = market.dates[idx]
        if isinstance(bar_time, datetime) and is_after_time(bar_time, no_entry_cutoff):
            return

    state.entry_price = compute_entry_fill(float(market.opens[idx]), cfg.slippage_pct)
    state.entry_idx = idx
    state.in_position = True
    state.last_price = state.entry_price
    state.stop_level = state.entry_price * (1 - state.stop_loss / 100) if state.stop_loss else None
    state.tp_level = (
        state.entry_price * (1 + state.take_profit / 100) if state.take_profit else None
    )
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
    exit_reason = _get_exit_reason(state, market, idx)

    if exit_reason:
        _record_trade(state, market, equity, trades, idx, exit_reason, cfg)
    else:
        pnl = (market.closes[idx] - state.last_price) / state.last_price
        equity[idx] += equity[idx - 1] * state.position_size * pnl
        state.last_price = float(market.closes[idx])


def _get_exit_reason(
    state: _BacktestState,
    market: _MarketData,
    idx: int,
) -> str:
    """Determine exit reason from stop/take-profit/signal/EOD.

    Phase 3.3: Uses high/low for intrabar stop/TP detection (long-only).
    Stop checked first — conservative assumption (adverse scenario wins).
    """
    # Intrabar stop-loss check using low (worst case for longs)
    if state.stop_level is not None and float(market.lows[idx]) <= state.stop_level:
        return "stop_loss"

    # Intrabar take-profit check using high (best case for longs)
    if state.tp_level is not None and float(market.highs[idx]) >= state.tp_level:
        return "take_profit"

    # Signal-based exit
    if market.exits[idx - 1]:
        return "signal"

    # Phase 3.4: forced EOD close
    if state.close_eod and is_last_bar_of_session(idx, market.dates):
        return "eod_close"

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
    """Record a completed trade with proper fill model.

    Phase 3.3 fill model:
    - signal: open[idx] with slippage (unchanged)
    - stop_loss: stop level, or open[idx] if gap-through (worse fill)
    - take_profit: target level, or open[idx] if gap-through (better fill)
    - eod_close: close[idx] (session close price)
    """
    exit_price = _compute_exit_price(state, market, idx, exit_reason, cfg)

    # Full trade return (for Trade record only)
    trade_return = (exit_price - state.entry_price) / state.entry_price
    trade_return -= cfg.commission_pct / 100 * 2

    # Exit-day equity: only the last leg (last_price → exit_price) + commission
    exit_day_pnl = (exit_price - state.last_price) / state.last_price
    exit_day_pnl -= cfg.commission_pct / 100 * 2
    equity[idx] += equity[idx - 1] * state.position_size * exit_day_pnl

    holding_days, holding_minutes = _compute_holding_period(
        market.dates,
        state.entry_idx,
        idx,
        cfg.timeframe,
    )

    trades.append(
        Trade(
            symbol=cfg.symbol,
            entry_date=date_to_str(market.dates[state.entry_idx]),
            entry_price=state.entry_price,
            exit_date=date_to_str(market.dates[idx]),
            exit_price=exit_price,
            return_pct=trade_return * 100,
            holding_days=holding_days,
            exit_reason=exit_reason,
            holding_minutes=holding_minutes,
            timeframe=cfg.timeframe,
        )
    )
    state.in_position = False


def _compute_exit_price(
    state: _BacktestState,
    market: _MarketData,
    idx: int,
    exit_reason: str,
    cfg: BacktestConfig,
) -> float:
    """Compute fill price based on exit reason.

    Uses precomputed stop/TP levels from state. Delegates to shared
    compute_exit_fill for fill model logic.

    Args:
        state: Current backtest state (with precomputed stop_level/tp_level).
        market: Market data arrays.
        idx: Current bar index.
        exit_reason: Why we're exiting.
        cfg: Backtest configuration.

    Returns:
        Fill price for the exit.

    """
    return compute_exit_fill(
        reason=exit_reason,
        open_price=float(market.opens[idx]),
        close_price=float(market.closes[idx]),
        stop_level=state.stop_level,
        tp_level=state.tp_level,
        slippage_pct=cfg.slippage_pct,
    )


def _compute_holding_period(
    dates: list[Any],
    entry_idx: int,
    exit_idx: int,
    timeframe: str = "daily",
) -> tuple[int, int]:
    """Compute holding period as (days, minutes).

    Phase 3.5: timeframe-aware, not type-based. DuckDB stores daily bars
    as TIMESTAMP (midnight), so isinstance(x, datetime) is True for both.

    Args:
        dates: List of date/datetime values.
        entry_idx: Index of entry bar.
        exit_idx: Index of exit bar.
        timeframe: Bar timeframe.

    Returns:
        Tuple of (holding_days, holding_minutes).

    """
    entry_dt = dates[entry_idx]
    exit_dt = dates[exit_idx]

    # Intraday: compute both days and minutes
    if timeframe != "daily" and isinstance(exit_dt, datetime) and isinstance(entry_dt, datetime):
        delta = exit_dt - entry_dt
        return delta.days, int(delta.total_seconds() / 60)

    # Daily: only compute days
    if isinstance(exit_dt, (date, datetime)) and isinstance(entry_dt, (date, datetime)):
        exit_d = exit_dt.date() if isinstance(exit_dt, datetime) else exit_dt
        entry_d = entry_dt.date() if isinstance(entry_dt, datetime) else entry_dt
        return (exit_d - entry_d).days, 0

    return exit_idx - entry_idx, 0


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
            timeframe=cfg.timeframe,
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
        combined = combined.join(ec, on="date", how="full", coalesce=True)

    equity_cols = [c for c in combined.columns if c.startswith("equity_")]
    return combined.with_columns(
        pl.mean_horizontal(*[pl.col(c) for c in equity_cols]).alias(
            "equity",
        ),
    ).select(["date", "equity"])
