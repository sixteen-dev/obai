"""Hold-to-resolution backtest simulator + summary + monte_carlo_input.

Pure functions over PriceRows + a validated PredictionRule. The tool layer
owns universe selection, backfill, and response packaging; this module just
turns market-level observations into Trade dataclasses and aggregates them.

Terminal payoff (§10.4):
    win  : (1 - p) / p     (return_on_cost)
    lose : -1
    pnl per contract:
        win  : 1 - p
        lose : -p
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from ..storage import PriceRow
from .observations import select_earliest_eligible_observation
from .rules import PredictionRule


@dataclass(frozen=True)
class Trade:
    """One simulated trade (one market, one entry)."""

    condition_id: str
    event_slug: str | None
    side: str
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    realized_win: bool
    return_on_cost: float
    pnl_per_contract: float


@dataclass(frozen=True)
class BacktestMarket:
    """Per-market inputs the simulator needs."""

    condition_id: str
    event_slug: str | None
    end_date: datetime
    winning_outcome_label: str
    yes_token_rows: list[PriceRow]


def simulate_rule(
    rule: PredictionRule,
    markets: list[BacktestMarket],
    *,
    out_skipped: dict[str, int] | None = None,
) -> list[Trade]:
    """Run the rule against one row per market.

    V1 is single-entry per market. The earliest sampled YES price inside
    the entry band of the rule wins; ties on timestamp are broken by source
    order in the iterable.

    TTR filters (``min_days_to_resolution``/``max_days_to_resolution``)
    are checked at the candidate entry timestamp, not at tool-invocation
    time — for resolved markets the invocation time is always after
    ``end_date`` so a wall-clock check would skip every trade.

    Args:
        rule: Validated PredictionRule.
        markets: Per-market inputs (already filtered to resolved markets).
        out_skipped: Optional dict; when supplied, simulate_rule writes
            named skip counts into it (``no_eligible_entry``,
            ``ttr_min_unmet``, ``ttr_max_exceeded``). Lets callers
            surface drop reasons in their response without re-running
            the eligibility loop.

    Returns:
        List of Trade dataclasses, one per market that had an eligible
        entry AND passed any per-entry TTR filter.

    """
    p_min = rule.entry.price_min
    p_max = rule.entry.price_max
    min_days = rule.filters.min_days_to_resolution
    max_days = rule.filters.max_days_to_resolution

    def _eligibility(row: PriceRow) -> bool:
        return p_min <= row.price <= p_max

    trades: list[Trade] = []
    for market in markets:
        entry_row = select_earliest_eligible_observation(market.yes_token_rows, _eligibility)
        if entry_row is None:
            _bump(out_skipped, "no_eligible_entry")
            continue
        days_to_resolution = (market.end_date - entry_row.timestamp).total_seconds() / 86_400.0
        if min_days is not None and days_to_resolution < min_days:
            _bump(out_skipped, "ttr_min_unmet")
            continue
        if max_days is not None and days_to_resolution > max_days:
            _bump(out_skipped, "ttr_max_exceeded")
            continue
        # hold_to_resolution: exit is the terminal price on the YES token.
        exit_row = market.yes_token_rows[-1] if market.yes_token_rows else None
        if exit_row is None:
            continue
        # Winning outcome is matched against the YES outcome label per market
        # because the YES side maps onto a specific outcome ("Yes" by
        # convention but not guaranteed).
        realized_win = market.winning_outcome_label.strip().lower() == "yes"
        return_on_cost, pnl_per_contract = _payoff(entry_row.price, realized_win)
        trades.append(
            Trade(
                condition_id=market.condition_id,
                event_slug=market.event_slug,
                side=rule.side,
                entry_ts=entry_row.timestamp,
                entry_price=entry_row.price,
                exit_ts=exit_row.timestamp,
                exit_price=exit_row.price,
                realized_win=realized_win,
                return_on_cost=return_on_cost,
                pnl_per_contract=pnl_per_contract,
            )
        )
    return trades


def _bump(counter: dict[str, int] | None, key: str) -> None:
    """Increment ``counter[key]`` when bookkeeping was requested."""
    if counter is None:
        return
    counter[key] = counter.get(key, 0) + 1


def summarize_trades(trades: list[Trade]) -> dict[str, Any]:
    """Aggregate trade statistics.

    Args:
        trades: Output of simulate_rule.

    Returns:
        Dict with sample_size, win_rate, distributional return stats, plus
        best/worst trade pointers. All numeric fields are rounded to 6
        decimals so JSON serialization stays tight.

    """
    if not trades:
        return _empty_summary()
    sample_size = len(trades)
    wins = sum(1 for t in trades if t.realized_win)
    win_rate = wins / sample_size
    returns = [t.return_on_cost for t in trades]
    pnls = [t.pnl_per_contract for t in trades]
    return {
        "sample_size": sample_size,
        "win_count": wins,
        "loss_count": sample_size - wins,
        "win_rate": round(win_rate, 6),
        "avg_return_on_cost": round(_mean(returns), 6),
        "median_return_on_cost": round(median(returns), 6),
        "return_p10": round(_percentile(returns, 0.10), 6),
        "return_p50": round(_percentile(returns, 0.50), 6),
        "return_p90": round(_percentile(returns, 0.90), 6),
        "avg_pnl_per_contract": round(_mean(pnls), 6),
        "best_trade": _trade_to_dict(max(trades, key=lambda t: t.return_on_cost)),
        "worst_trade": _trade_to_dict(min(trades, key=lambda t: t.return_on_cost)),
    }


def build_monte_carlo_input(
    *,
    trades: list[Trade],
    seed: int,
    source_backtest_fingerprint: str,
    limitations: list[str],
) -> dict[str, Any]:
    """Pack the compact §10.5 monte_carlo_input payload.

    The Monte Carlo tool (Phase 5) accepts this dict directly, avoiding a
    round-trip through the full backtest response.

    Args:
        trades: Output of simulate_rule.
        seed: Seed the upstream backtest used (or a freshly chosen one).
        source_backtest_fingerprint: Fingerprint identifying the backtest
            that produced ``trades``; the Monte Carlo response echoes it
            so users can verify they paired the right inputs.
        limitations: Limitations to forward unchanged.

    Returns:
        Dict matching the §10.5 minimum monte_carlo_input shape.

    """
    return {
        "return_type": "return_on_cost",
        "returns": [round(t.return_on_cost, 6) for t in trades],
        "seed": seed,
        "source_backtest_fingerprint": source_backtest_fingerprint,
        "condition_ids": [t.condition_id for t in trades],
        "entry_timestamps": [t.entry_ts.isoformat() for t in trades],
        "event_slugs": [t.event_slug for t in trades],
        "limitations": list(limitations),
    }


def trade_to_dict(t: Trade) -> dict[str, Any]:
    """Render a Trade for the examples block of a tool response."""
    return _trade_to_dict(t)


# -- helpers ------------------------------------------------------------------


def _payoff(entry_price: float, realized_win: bool) -> tuple[float, float]:
    """Compute (return_on_cost, pnl_per_contract) per §10.4 terminal payoff."""
    if entry_price <= 0.0 or entry_price > 1.0:
        msg = f"entry_price must be in (0, 1]; got {entry_price}"
        raise ValueError(msg)
    if realized_win:
        return_on_cost = (1.0 - entry_price) / entry_price
        pnl = 1.0 - entry_price
    else:
        return_on_cost = -1.0
        pnl = -entry_price
    return return_on_cost, pnl


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy parity, no numpy dep)."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = q * (len(sorted_values) - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return sorted_values[lower]
    fraction = idx - lower
    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


def _trade_to_dict(trade: Trade) -> dict[str, Any]:
    return {
        "condition_id": trade.condition_id,
        "event_slug": trade.event_slug,
        "side": trade.side,
        "entry_ts": trade.entry_ts.isoformat(),
        "entry_price": round(trade.entry_price, 6),
        "exit_ts": trade.exit_ts.isoformat(),
        "exit_price": round(trade.exit_price, 6),
        "realized_win": trade.realized_win,
        "return_on_cost": round(trade.return_on_cost, 6),
        "pnl_per_contract": round(trade.pnl_per_contract, 6),
    }


def _empty_summary() -> dict[str, Any]:
    """Stable empty-summary shape so the response contract is honored on no-trade cases."""
    return {
        "sample_size": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_return_on_cost": 0.0,
        "median_return_on_cost": 0.0,
        "return_p10": 0.0,
        "return_p50": 0.0,
        "return_p90": 0.0,
        "avg_pnl_per_contract": 0.0,
        "best_trade": None,
        "worst_trade": None,
    }
