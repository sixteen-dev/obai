"""Core backtest loop — vectorized with numpy, intrabar stop/TP, EOD close.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phases 3.2-3.5.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from ..models.strategy import PositionSizing, RiskManagement
from .session import is_after_time, is_last_bar_of_session, parse_time_str
from .utils import date_to_str

# --- Volume-scaled slippage constants ---

REFERENCE_PARTICIPATION: float = 0.01
MIN_SLIPPAGE_PCT: float = 0.005
MAX_SLIPPAGE_PCT: float = 2.0


# --- Shared pure functions (importable by portfolio backtester) ---


def _effective_slippage(
    slippage_pct: float,
    order_shares: float | None,
    bar_volume: int | None,
) -> float:
    """Compute effective slippage, optionally scaled by participation rate.

    Args:
        slippage_pct: Base slippage percentage.
        order_shares: Number of shares in the order (None to skip scaling).
        bar_volume: Bar volume (None or 0 to skip scaling).

    Returns:
        Effective slippage percentage.

    """
    if order_shares is None or bar_volume is None or bar_volume <= 0:
        return slippage_pct
    if slippage_pct == 0.0:
        return 0.0
    participation = order_shares / bar_volume
    scaled = slippage_pct * math.sqrt(participation / REFERENCE_PARTICIPATION)
    return max(MIN_SLIPPAGE_PCT, min(scaled, MAX_SLIPPAGE_PCT))


def compute_entry_fill(
    open_price: float,
    slippage_pct: float,
    order_shares: float | None = None,
    bar_volume: int | None = None,
    spread_cost: float = 0.0,
) -> float:
    """Compute fill price for entry (buy). Slippage makes it worse (higher).

    When order_shares and bar_volume are provided, slippage scales with
    the square root of participation rate (simplified Almgren-Chriss).
    Otherwise falls back to flat slippage.

    Args:
        open_price: The bar's open price.
        slippage_pct: Slippage as a percentage (e.g. 0.1 for 0.1%).
        order_shares: Number of shares in the order (enables volume scaling).
        bar_volume: Bar volume for participation rate computation.
        spread_cost: Half-spread as a fraction of price (added to fill).

    Returns:
        Adjusted fill price.

    """
    effective = _effective_slippage(slippage_pct, order_shares, bar_volume)
    return open_price * (1 + effective / 100) + open_price * spread_cost


def compute_exit_fill(  # noqa: PLR0913
    reason: str,
    open_price: float,
    close_price: float,
    stop_level: float | None,
    tp_level: float | None,
    slippage_pct: float,
    order_shares: float | None = None,
    bar_volume: int | None = None,
    spread_cost: float = 0.0,
) -> float:
    """Compute fill price for exit based on exit reason.

    Volume-scaled slippage and spread cost apply only to signal-based exits.
    Stop/TP/EOD paths use level prices and are unaffected.

    Args:
        reason: Exit reason (signal, stop_loss, trailing_stop, take_profit,
            eod_close, time_stop).
        open_price: The bar's open price.
        close_price: The bar's close price.
        stop_level: Stop-loss price level (if applicable).
        tp_level: Take-profit price level (if applicable).
        slippage_pct: Slippage as a percentage.
        order_shares: Number of shares (enables volume scaling).
        bar_volume: Bar volume for participation rate computation.
        spread_cost: Half-spread as a fraction of price (subtracted from fill).

    Returns:
        Fill price for the exit.

    """
    if reason == "signal":
        effective = _effective_slippage(slippage_pct, order_shares, bar_volume)
        return open_price * (1 - effective / 100) - open_price * spread_cost
    if reason in ("stop_loss", "trailing_stop") and stop_level is not None:
        return min(stop_level, open_price)
    if reason == "take_profit" and tp_level is not None:
        return max(tp_level, open_price)
    # eod_close, time_stop, end_of_backtest, and fallback: market-on-close
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
    # "signal", "stop_loss", "trailing_stop", "take_profit", "eod_close",
    # "time_stop", "end_of_backtest"
    exit_reason: str
    # Realized dollar PnL. None when the producing engine has no notional to
    # report, which is what makes trade statistics fall back to percentages.
    pnl: float | None = None
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
    volume_scaled_slippage: bool = False
    spread_estimates: np.ndarray[Any, np.dtype[np.float64]] | None = None
    # Starting capital for the run. Used to seed the equity curve so dollar
    # P&L, position sizing, and drawdown match the strategy's
    # `execution_config.initial_capital`. Default mirrors
    # ``ExecutionConfig.initial_capital``.
    initial_capital: float = 100_000.0


def run_backtest(
    df: pl.DataFrame,
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    config: BacktestConfig | None = None,
    skipped: Counter[str] | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Run backtest on a single symbol with signals already computed.

    Signals on close[t], trades execute on open[t+1] (no look-ahead bias).

    Args:
        df: DataFrame with date, open, high, low, close, entry_signal, exit_signal.
        position_sizing: Position sizing configuration.
        risk_management: Risk management parameters.
        config: Optional backtest config (symbol, slippage, commission).
        skipped: Optional accumulator counting entry signals that fired but
            never filled, by reason. Supplied by the caller so one run over
            several symbols reports one total, like ``trades``.

    Returns:
        Tuple of (equity curve DataFrame, list of Trade objects).

    """
    cfg = config or BacktestConfig()
    return _execute_backtest(df, position_sizing, risk_management, cfg, skipped)


def _execute_backtest(
    df: pl.DataFrame,
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
    cfg: BacktestConfig,
    skipped: Counter[str] | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Execute the backtest loop (internal implementation)."""
    check_atr_risk_inputs(position_sizing, risk_management)
    market = _market_data(df, risk_management.atr_indicator)
    state = _initial_state(position_sizing, risk_management)

    n = len(market.dates)
    equity = np.ones(n, dtype=np.float64) * float(cfg.initial_capital)
    trades: list[Trade] = []
    skip_counts = skipped if skipped is not None else Counter()

    # Parse no_entry_after cutoff time if configured
    no_entry_cutoff = None
    if risk_management.no_entry_after:
        no_entry_cutoff = parse_time_str(risk_management.no_entry_after)

    for i in range(1, n):
        equity[i] = equity[i - 1]
        if not state.in_position:
            _check_entry(state, market, equity, trades, i, cfg, no_entry_cutoff, skip_counts)
            continue
        # An entry that fires while the position is held is a decision the run
        # made, not an absence of signal; counting it separates "never fires"
        # from "fires but cannot be acted on".
        if market.entries[i - 1]:
            skip_counts["in_position"] += 1
        _check_exit(state, market, equity, trades, i, cfg)

    # Close what is still open at the last bar, matching portfolio mode, so a
    # zero-trade result never hides an entry that was made.
    if state.in_position:
        _record_trade(state, market, equity, trades, n - 1, "end_of_backtest", cfg)

    equity_df = pl.DataFrame(
        {
            "date": market.dates,
            "equity": equity.tolist(),
        }
    )
    return equity_df, trades


def _market_data(df: pl.DataFrame, atr_indicator: str | None) -> _MarketData:
    """Bundle the signal frame's columns into typed arrays for the loop."""
    return _MarketData(
        dates=df["date"].to_list(),
        opens=df["open"].to_numpy().astype(np.float64),
        highs=df["high"].to_numpy().astype(np.float64),
        lows=df["low"].to_numpy().astype(np.float64),
        closes=df["close"].to_numpy().astype(np.float64),
        volumes=df["volume"].to_numpy().astype(np.int64),
        entries=df["entry_signal"].to_numpy(),
        exits=df["exit_signal"].to_numpy(),
        atr=atr_column(df, atr_indicator),
    )


def _initial_state(
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
) -> _BacktestState:
    """Copy the sizing and risk rules into the flat loop state."""
    return _BacktestState(
        stop_loss=risk_management.stop_loss_pct,
        take_profit=risk_management.take_profit_pct,
        position_size=position_sizing.max_position_pct / 100.0,
        close_eod=risk_management.close_eod,
        stop_atr_multiple=risk_management.stop_atr_multiple,
        trail_pct=risk_management.trailing_stop_pct,
        trail_atr_multiple=risk_management.trailing_stop_atr_multiple,
        sizing_method=position_sizing.method,
        risk_pct=position_sizing.risk_pct,
        max_holding_bars=risk_management.max_holding_bars,
        cooldown_bars=risk_management.reentry_cooldown_bars,
    )


@dataclass
class _MarketData:
    """Bundled market data arrays for the backtest loop."""

    dates: list[Any]
    opens: np.ndarray[Any, np.dtype[np.float64]]
    highs: np.ndarray[Any, np.dtype[np.float64]]
    lows: np.ndarray[Any, np.dtype[np.float64]]
    closes: np.ndarray[Any, np.dtype[np.float64]]
    volumes: np.ndarray[Any, np.dtype[np.int64]]
    entries: np.ndarray[Any, np.dtype[Any]]
    exits: np.ndarray[Any, np.dtype[Any]]
    # ATR in price units, present only when a risk rule reads it.
    atr: np.ndarray[Any, np.dtype[np.float64]] | None = None


@dataclass
class _BacktestState:
    """Mutable state for the backtest loop."""

    stop_loss: float | None
    take_profit: float | None
    position_size: float
    close_eod: bool = False
    stop_atr_multiple: float | None = None
    # Distance the trailing stop keeps below the highest high since entry,
    # as a percent of that high or a multiple of the completed bar's ATR.
    trail_pct: float | None = None
    trail_atr_multiple: float | None = None
    sizing_method: str = "equal_weight"
    # Share of equity budgeted to the ATR stop's loss under ``atr_risk``.
    risk_pct: float | None = None
    # Bars the position may be held, counting the entry bar as the first.
    max_holding_bars: int | None = None
    # Bars after an exit during which a new entry cannot fill.
    cooldown_bars: int | None = None
    in_position: bool = False
    entry_price: float = 0.0
    entry_idx: int = 0
    last_price: float = 0.0
    # Dollar value bought at entry. Held constant for the life of the trade so
    # the position is marked as a fixed share count instead of being silently
    # rebalanced to a constant fraction of equity on every bar.
    notional: float = 0.0
    stop_level: float | None = None
    tp_level: float | None = None
    order_shares: float = 0.0
    # Highest high the open position has printed, and the trail it has
    # ratcheted to. Both are reset at every fill.
    high_water_mark: float = 0.0
    trail_level: float | None = None
    # Bar the most recent exit filled on, whatever closed it.
    last_exit_idx: int | None = None


def atr_column(
    df: pl.DataFrame, atr_indicator: str | None
) -> np.ndarray[Any, np.dtype[np.float64]] | None:
    """Extract the ATR column the risk rules read, or None when none do.

    ``compute_indicators`` turns a failed indicator into a warning and drops
    the column, so a risk rule that depends on it must fail loudly here rather
    than silently run with no stop.

    Args:
        df: Signal frame, already carrying any computed indicator columns.
        atr_indicator: Id of the declared ATR indicator, or None.

    Returns:
        The ATR values as float64, or None when no rule reads an ATR.

    Raises:
        ValueError: If the named indicator produced no column.

    """
    if atr_indicator is None:
        return None
    if atr_indicator not in df.columns:
        msg = f"ATR indicator '{atr_indicator}' has no column; it failed to compute"
        raise ValueError(msg)
    return df[atr_indicator].to_numpy().astype(np.float64)


def atr_value(
    atr: np.ndarray[Any, np.dtype[np.float64]] | None,
    idx: int,
) -> float | None:
    """Return the ATR at ``idx`` when it is a usable stop distance.

    Args:
        atr: ATR values, or None when no rule reads an ATR.
        idx: Bar to read.

    Returns:
        The value, or None when it is missing, non-finite, or not positive.

    """
    if atr is None:
        return None
    value = float(atr[idx])
    return value if math.isfinite(value) and value > 0 else None


def check_atr_risk_inputs(
    position_sizing: PositionSizing,
    risk_management: RiskManagement,
) -> None:
    """Reject an ``atr_risk`` run whose budget or stop distance is missing.

    ``StrategyDefinition.validate`` already rejects both, so reaching here
    means the engine was called directly; sizing every entry to nothing would
    otherwise read as a strategy that never fires.

    Args:
        position_sizing: Sizing configuration for the run.
        risk_management: Risk rules the run's lots open under.

    Raises:
        ValueError: If the method is ``atr_risk`` and either input is missing.

    """
    if position_sizing.method != "atr_risk":
        return
    if position_sizing.risk_pct is None or risk_management.stop_atr_multiple is None:
        msg = (
            "atr_risk sizing requires position_sizing.risk_pct and "
            "risk_management.stop_atr_multiple"
        )
        raise ValueError(msg)


def needs_entry_atr(
    stop_atr_multiple: float | None,
    trail_atr_multiple: float | None,
    sizing_method: str,
) -> bool:
    """Report whether an entry cannot be priced without the signal bar's ATR.

    Args:
        stop_atr_multiple: Multiple placing the initial stop, if any.
        trail_atr_multiple: Multiple placing the initial trail, if any.
        sizing_method: Configured position sizing method.

    Returns:
        True when a stop, a trail or the share count is measured in ATR units.

    """
    return (
        stop_atr_multiple is not None
        or trail_atr_multiple is not None
        or sizing_method == "atr_risk"
    )


def _in_cooldown(state: _BacktestState, idx: int) -> bool:
    """Report whether the last exit is still too recent for a new entry.

    Args:
        state: Backtest state carrying the cooldown and its last exit bar.
        idx: Bar the entry would fill on.

    Returns:
        True when a cooldown is configured and has not elapsed. An entry can
        never fill on the exit bar itself here, so N blocks exactly the N
        bars that follow it.

    """
    if not state.cooldown_bars or state.last_exit_idx is None:
        return False
    return idx - state.last_exit_idx <= state.cooldown_bars


def _risk_budget_shares(
    state: _BacktestState,
    equity_at_decision: float,
    atr_prev: float | None,
) -> int:
    """Return the whole shares whose loss at the ATR stop equals the budget.

    The budget bounds the price loss down to the stop only; commission and
    slippage sit on top of it, and a gap through the stop can exceed it.

    Args:
        state: Backtest state carrying the sizing method and budget.
        equity_at_decision: Equity observable when the order is placed.
        atr_prev: ATR on the bar whose close produced the signal.

    Returns:
        The share count, or 0 when the method is not ``atr_risk`` or the
        distance cannot be priced.

    """
    if state.sizing_method != "atr_risk":
        return 0
    if state.risk_pct is None or state.stop_atr_multiple is None or atr_prev is None:
        return 0
    stop_distance = state.stop_atr_multiple * atr_prev
    return int((equity_at_decision * state.risk_pct / 100.0) // stop_distance)


def _entry_stop_level(state: _BacktestState, atr_prev: float | None) -> float | None:
    """Return the stop level frozen at this fill.

    A percent stop sits that far below the fill; an ATR stop sits that multiple
    of the signal bar's ATR below it. A non-positive level is kept as computed:
    it simply never triggers, and clamping it would invent a stop the rule did
    not ask for.

    Args:
        state: Backtest state, with the entry price already recorded.
        atr_prev: ATR on the bar whose close produced the signal.

    Returns:
        The frozen stop level, or None when no stop is configured.

    """
    if state.stop_loss:
        return state.entry_price * (1 - state.stop_loss / 100)
    if state.stop_atr_multiple is not None and atr_prev is not None:
        return state.entry_price - state.stop_atr_multiple * atr_prev
    return None


def _trail_distance(state: _BacktestState, atr_at: float | None) -> float | None:
    """Return how far below the high water mark the trail sits.

    Args:
        state: Backtest state, with ``high_water_mark`` already updated.
        atr_at: ATR on the bar the distance is measured from, or None.

    Returns:
        The distance, or None when no trail is configured or the ATR variant
        has no value to read on that bar.

    """
    if state.trail_pct is not None:
        return state.trail_pct / 100 * state.high_water_mark
    if state.trail_atr_multiple is not None and atr_at is not None:
        return state.trail_atr_multiple * atr_at
    return None


def _effective_stop(state: _BacktestState) -> float | None:
    """Return the highest stop level protecting the open position.

    Args:
        state: Backtest state carrying the frozen stop and the trail.

    Returns:
        The tighter of the two levels, or None when neither is set.

    """
    levels = [level for level in (state.stop_level, state.trail_level) if level is not None]
    return max(levels) if levels else None


def _stop_reason(state: _BacktestState) -> str:
    """Name which stop the effective level belongs to.

    Returns:
        ``trailing_stop`` only when the trail sits strictly above the frozen
        stop; a tie belongs to the fixed stop, which the trail did not improve.

    """
    trail, fixed = state.trail_level, state.stop_level
    if trail is not None and (fixed is None or trail > fixed):
        return "trailing_stop"
    return "stop_loss"


def _update_trail(
    state: _BacktestState,
    market: _MarketData,
    idx: int,
) -> None:
    """Ratchet the trail with the bar that just completed.

    Called once the position has survived bar ``idx``, so the high printed
    inside that bar can only tighten the level checked on the next one. The
    level never falls, and an undefined ATR leaves it where it was.

    Args:
        state: Backtest state; the high water mark and trail are mutated.
        market: Market data arrays.
        idx: Bar that just completed.

    """
    if state.trail_pct is None and state.trail_atr_multiple is None:
        return
    state.high_water_mark = max(state.high_water_mark, float(market.highs[idx]))
    distance = _trail_distance(state, atr_value(market.atr, idx))
    if distance is None:
        return
    candidate = state.high_water_mark - distance
    if state.trail_level is None or candidate > state.trail_level:
        state.trail_level = candidate


def _open_position(  # noqa: PLR0913
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
    cfg: BacktestConfig,
    atr_prev: float | None,
) -> bool:
    """Fill the entry at this bar's open and freeze the trade's exit levels.

    Args:
        state: Backtest state, mutated into the in-position state.
        market: Market data arrays.
        equity: Equity curve; the previous bar's value sizes the order.
        idx: Bar the order fills on.
        cfg: Backtest configuration.
        atr_prev: ATR on the bar whose close produced the signal, or None.

    Returns:
        True once the position is open; False when an ``atr_risk`` budget
        bought no whole share, in which case no state is mutated.

    """
    open_price = float(market.opens[idx])
    equity_at_decision = float(equity[idx - 1])
    shares_risk = _risk_budget_shares(state, equity_at_decision, atr_prev)

    # Derive order shares and spread cost for volume-scaled slippage
    order_shares: float | None = None
    bar_volume: int | None = None
    spread_cost = 0.0
    if cfg.volume_scaled_slippage:
        order_shares = (
            float(shares_risk)
            if state.sizing_method == "atr_risk"
            else (equity_at_decision * state.position_size) / open_price
        )
        bar_volume = int(market.volumes[idx])
        state.order_shares = order_shares
    if cfg.spread_estimates is not None and not np.isnan(cfg.spread_estimates[idx]):
        spread_cost = float(cfg.spread_estimates[idx]) / 2

    entry_price = compute_entry_fill(
        open_price,
        cfg.slippage_pct,
        order_shares,
        bar_volume,
        spread_cost,
    )
    notional = equity_at_decision * state.position_size
    if state.sizing_method == "atr_risk":
        shares = _capped_risk_shares(notional, entry_price, shares_risk)
        if shares <= 0:
            return False
        state.order_shares = float(shares)
        notional = shares * entry_price

    state.entry_price = entry_price
    state.entry_idx = idx
    state.in_position = True
    state.last_price = entry_price
    state.notional = notional
    state.stop_level = _entry_stop_level(state, atr_prev)
    state.tp_level = (
        state.entry_price * (1 + state.take_profit / 100) if state.take_profit else None
    )
    state.high_water_mark = entry_price
    distance = _trail_distance(state, atr_prev)
    state.trail_level = None if distance is None else entry_price - distance
    return True


def _capped_risk_shares(
    max_notional: float,
    entry_price: float,
    shares_risk: int,
) -> int:
    """Cap the risk budget's share count by the exposure limit.

    Args:
        max_notional: Dollar value the exposure cap allows at this fill.
        entry_price: Fill price per share.
        shares_risk: Whole shares the risk budget allows.

    Returns:
        The whole shares to buy; 0 when the budget or the cap leaves none.

    """
    if entry_price <= 0:
        return 0
    return min(shares_risk, int(max_notional // entry_price))


def _check_entry(  # noqa: PLR0913
    state: _BacktestState,
    market: _MarketData,
    equity: np.ndarray[Any, np.dtype[np.float64]],
    trades: list[Trade],
    idx: int,
    cfg: BacktestConfig,
    no_entry_cutoff: Any | None,
    skipped: Counter[str],
) -> None:
    """Check and execute entry signal from previous bar."""
    if not market.entries[idx - 1]:
        return

    # Phase 3.4: no_entry_after time filter
    if no_entry_cutoff is not None:
        bar_time = market.dates[idx]
        if isinstance(bar_time, datetime) and is_after_time(bar_time, no_entry_cutoff):
            skipped["no_entry_after"] += 1
            return

    # A re-entry inside the cooldown window is the whipsaw the rule exists
    # to prevent, so it is dropped before any fill or commission.
    if _in_cooldown(state, idx):
        skipped["cooldown"] += 1
        return

    # An ATR stop with no distance to place would enter an unprotected
    # position, and an ATR budget with none would size against nothing, so the
    # signal is dropped before any fill or commission.
    atr_prev = atr_value(market.atr, idx - 1)
    needs_atr = needs_entry_atr(
        state.stop_atr_multiple, state.trail_atr_multiple, state.sizing_method
    )
    if needs_atr and atr_prev is None:
        skipped["atr_undefined"] += 1
        return

    if not _open_position(state, market, equity, idx, cfg, atr_prev):
        skipped["zero_shares"] += 1
        return

    # Intrabar stop/TP can bind on the entry bar itself (stop wins ties).
    entry_bar_exit = _intrabar_stop_or_tp(state, market, idx)
    # A position opened on the session's last bar is force-closed like any other.
    if not entry_bar_exit and state.close_eod and is_last_bar_of_session(idx, market.dates):
        entry_bar_exit = "eod_close"
    # A one-bar holding cap is spent by the fill itself: the entry bar is the
    # first bar held, so it is also the last.
    if not entry_bar_exit and state.max_holding_bars == 1:
        entry_bar_exit = "time_stop"
    if entry_bar_exit:
        # Book entry-side commission here; _record_trade books the exit side.
        equity[idx] -= state.notional * (cfg.commission_pct / 100)
        _record_trade(state, market, equity, trades, idx, entry_bar_exit, cfg)
        return

    # Entry-day PnL: entry_price → close[entry_day], minus entry-side commission
    day_pnl = (market.closes[idx] - state.entry_price) / state.entry_price
    day_pnl -= cfg.commission_pct / 100
    equity[idx] += state.notional * day_pnl
    state.last_price = float(market.closes[idx])
    _update_trail(state, market, idx)


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
        # Shares are fixed, so the bar's PnL is the price change over the
        # entry price (notional / entry_price shares), not over the last mark.
        pnl = (market.closes[idx] - state.last_price) / state.entry_price
        equity[idx] += state.notional * pnl
        state.last_price = float(market.closes[idx])
        _update_trail(state, market, idx)


def _intrabar_stop_or_tp(
    state: _BacktestState,
    market: _MarketData,
    idx: int,
) -> str:
    """Return the intrabar stop/TP exit reason for this bar, or empty string.

    Uses high/low for intrabar stop/TP detection (long-only). Stop is checked
    first — conservative assumption (adverse scenario wins ties).
    """
    # Intrabar stop check using low (worst case for longs). The fixed stop and
    # the trail are one level: whichever sits higher is the one that binds.
    effective_stop = _effective_stop(state)
    if effective_stop is not None and float(market.lows[idx]) <= effective_stop:
        return _stop_reason(state)

    # Intrabar take-profit check using high (best case for longs)
    if state.tp_level is not None and float(market.highs[idx]) >= state.tp_level:
        return "take_profit"

    return ""


def _get_exit_reason(
    state: _BacktestState,
    market: _MarketData,
    idx: int,
) -> str:
    """Determine exit reason from signal/stop/take-profit/EOD/holding cap.

    An exit signal from the previous bar's close fills at this bar's open, so
    it is settled before anything that happens later inside the bar, and the
    holding cap is checked last because it is spent at the close.
    Phase 3.3: Uses high/low for intrabar stop/TP detection (long-only).
    Stop checked before take-profit — conservative assumption (adverse scenario
    wins).
    """
    # Signal-based exit — scheduled at the previous close, filled at this open.
    if market.exits[idx - 1]:
        return "signal"

    intrabar = _intrabar_stop_or_tp(state, market, idx)
    if intrabar:
        return intrabar

    # Phase 3.4: forced EOD close
    if state.close_eod and is_last_bar_of_session(idx, market.dates):
        return "eod_close"

    # The holding cap is spent at this close. A forced session close outranks
    # it: that exit would happen whatever the cap said, and both fill here.
    if state.max_holding_bars and idx - state.entry_idx + 1 >= state.max_holding_bars:
        return "time_stop"

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
    - time_stop: close[idx] (market-on-close, same fill as eod_close)
    - end_of_backtest: close[idx] (last available close)
    """
    exit_price = _compute_exit_price(state, market, idx, exit_reason, cfg)

    # Full trade return (for Trade record — round-trip commission)
    trade_return = (exit_price - state.entry_price) / state.entry_price
    trade_return -= cfg.commission_pct / 100 * 2

    # Exit-day equity: last leg minus exit-side commission only
    # (entry-side was already applied on entry day)
    exit_day_pnl = (exit_price - state.last_price) / state.entry_price
    exit_day_pnl -= cfg.commission_pct / 100
    equity[idx] += state.notional * exit_day_pnl

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
            pnl=state.notional * trade_return,
            holding_minutes=holding_minutes,
            timeframe=cfg.timeframe,
        )
    )
    state.in_position = False
    state.last_exit_idx = idx


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
    order_shares: float | None = None
    bar_volume: int | None = None
    spread_cost = 0.0
    if cfg.volume_scaled_slippage:
        order_shares = state.order_shares
        bar_volume = int(market.volumes[idx])
    if cfg.spread_estimates is not None and not np.isnan(cfg.spread_estimates[idx]):
        spread_cost = float(cfg.spread_estimates[idx]) / 2

    return compute_exit_fill(
        reason=exit_reason,
        open_price=float(market.opens[idx]),
        close_price=float(market.closes[idx]),
        stop_level=_effective_stop(state),
        tp_level=state.tp_level,
        slippage_pct=cfg.slippage_pct,
        order_shares=order_shares,
        bar_volume=bar_volume,
        spread_cost=spread_cost,
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
    skipped: Counter[str] | None = None,
) -> tuple[pl.DataFrame, list[Trade]]:
    """Run backtest across multiple symbols and combine equity curves.

    Args:
        symbol_dfs: Dict of symbol → DataFrame with signals computed.
        position_sizing: Position sizing configuration.
        risk_management: Risk management parameters.
        config: Optional backtest config (slippage, commission).
        skipped: Optional accumulator counting unfilled entry signals by
            reason. The same counter is passed to every symbol, so the run
            reports one total.

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
            volume_scaled_slippage=cfg.volume_scaled_slippage,
            spread_estimates=cfg.spread_estimates,
            initial_capital=cfg.initial_capital,
        )
        eq_curve, trades = run_backtest(
            df=sym_df,
            position_sizing=position_sizing,
            risk_management=risk_management,
            config=sym_cfg,
            skipped=skipped,
        )
        equity_curves.append(
            eq_curve.rename({"equity": f"equity_{symbol}"}),
        )
        all_trades.extend(trades)

    if not equity_curves:
        return pl.DataFrame({"date": [], "equity": []}), []

    combined = combine_equity_curves(equity_curves)
    return combined, all_trades


def _forward_fill_equity_cols(
    combined: pl.DataFrame,
    equity_cols: list[str],
) -> pl.DataFrame:
    """Sort by date and hold each sleeve's equity across missing bars.

    Forward-fills interior gaps so a missing bar carries the sleeve's last
    known equity ("hold"). Back-fills the remaining leading nulls (dates before
    a sleeve's first bar) to that sleeve's first equity, which equals its
    starting capital, so the sleeve reads as idle cash before inception.

    Args:
        combined: Full-joined curve with one ``equity_<symbol>`` column each.
        equity_cols: Names of the per-symbol equity columns to fill.

    Returns:
        The chronologically sorted frame with every equity column gap-filled.

    """
    return combined.sort("date").with_columns(
        pl.col(c).forward_fill().backward_fill() for c in equity_cols
    )


def combine_equity_curves(
    curves: list[pl.DataFrame],
) -> pl.DataFrame:
    """Combine per-symbol equity curves into a single averaged curve.

    Args:
        curves: Per-symbol equity curves, each with ``date`` and one
            ``equity_<symbol>`` column.

    Returns:
        A ``date``/``equity`` frame sorted chronologically, where ``equity`` is
        the horizontal mean of the gap-filled per-symbol curves (a missing bar
        holds the sleeve's last equity rather than dropping it from the mean).

    """
    combined = curves[0]
    for ec in curves[1:]:
        combined = combined.join(ec, on="date", how="full", coalesce=True)

    equity_cols = [c for c in combined.columns if c.startswith("equity_")]
    filled = _forward_fill_equity_cols(combined, equity_cols)
    return filled.with_columns(
        pl.mean_horizontal(*[pl.col(c) for c in equity_cols]).alias(
            "equity",
        ),
    ).select(["date", "equity"])
