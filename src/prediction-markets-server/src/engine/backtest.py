"""Backtest simulator + summary + monte_carlo_input.

Pure functions over PriceRows + a validated PredictionRule. The tool layer
owns universe selection, backfill, and response packaging; this module just
turns market-level observations into Trade dataclasses and aggregates them.

Two exit paths share the same simulator:

* ``hold_to_resolution`` — terminal §10.4 payoff. win = (1-p)/p, lose = -1.
* ``stop_take_profit``   — walk sampled rows after entry; first row that
  crosses stop / take-profit / max-hold wins. Booked PnL uses the observed
  sampled price at trigger (not the trigger level). When no row triggers
  and ``max_hold_days`` is unset or resolution falls on/before the
  boundary, fall back to the terminal payoff above. Otherwise skip the
  trade with ``no_exit_price_for_max_hold``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from statistics import median
from typing import Any, Literal

from ..storage import PriceRow
from .observations import select_earliest_eligible_observation
from .rules import HoldToResolutionExit, PredictionRule, StopTakeProfitExit

ExitReason = Literal["stop", "take_profit", "expiry", "resolution"]
EXIT_REASONS: tuple[ExitReason, ...] = ("stop", "take_profit", "expiry", "resolution")

# Upper bound on an assumed execution-cost knob (§11.6), in probability points.
# A round-trip beyond half the price range is not a realistic transaction cost.
_MAX_ASSUMED_COST = 0.5


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
    exit_reason: ExitReason
    time_to_exit_days: float


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
    entry_cost: float = 0.0,
    exit_cost: float = 0.0,
) -> list[Trade]:
    """Run the rule against one row per market.

    V1 is single-entry per market. The earliest sampled YES row satisfying
    the FULL entry predicate wins — price band plus any TTR filter — so a
    later in-band row is still considered when the earliest one fails the
    time bound; ties on timestamp are broken by source order in the
    iterable.

    TTR filters (``min_days_to_resolution``/``max_days_to_resolution``)
    are checked at each candidate entry timestamp, not at tool-invocation
    time — for resolved markets the invocation time is always after
    ``end_date`` so a wall-clock check would skip every trade.

    Dispatches on ``rule.exit.type``: ``hold_to_resolution`` uses the
    terminal payoff; ``stop_take_profit`` walks sampled rows after entry
    and exits on the first stop/take-profit/max-hold crossing, falling
    back to resolution only when the max-hold boundary is not violated.

    Args:
        rule: Validated PredictionRule.
        markets: Per-market inputs (already filtered to resolved markets).
        out_skipped: Optional dict; when supplied, simulate_rule writes
            named skip counts into it (``no_eligible_entry``,
            ``ttr_min_unmet``, ``ttr_max_exceeded``,
            ``no_exit_price_for_max_hold``, ``cost_makes_entry_invalid``).
            Lets callers surface drop reasons in their response without
            re-running the eligibility loop.
        entry_cost: Assumed cost added to the entry price paid, in
            probability points (§11.6). Default 0.0 reproduces the no-cost
            result exactly; a cost-adjusted entry outside (0, 1] skips the
            trade with ``cost_makes_entry_invalid``.
        exit_cost: Assumed cost subtracted from intermediate exit prices
            (stop / take_profit / expiry). Never applied to the resolution
            leg, which settles to 0/1 with no exit transaction.

    Returns:
        List of Trade dataclasses, one per market that had an eligible
        entry AND passed any per-entry TTR filter AND produced an exit
        consistent with the rule's exit type.

    """
    _validate_costs(entry_cost=entry_cost, exit_cost=exit_cost)
    trades: list[Trade] = []
    for market in markets:
        entry_row = select_earliest_eligible_observation(
            market.yes_token_rows,
            partial(_is_eligible_entry, rule=rule, market=market),
        )
        if entry_row is None:
            _bump(out_skipped, _classify_entry_skip(market, rule))
            continue
        trade = _build_trade(
            market,
            entry_row,
            rule,
            out_skipped=out_skipped,
            entry_cost=entry_cost,
            exit_cost=exit_cost,
        )
        if trade is not None:
            trades.append(trade)
    return trades


def _is_in_entry_band(row: PriceRow, *, rule: PredictionRule) -> bool:
    """Price-only half of the entry predicate."""
    return rule.entry.price_min <= row.price <= rule.entry.price_max


def _is_eligible_entry(row: PriceRow, *, rule: PredictionRule, market: BacktestMarket) -> bool:
    """Full entry predicate: price band AND the rule's TTR bounds at this row."""
    if not _is_in_entry_band(row, rule=rule):
        return False
    days_to_resolution = _days_between(row.timestamp, market.end_date)
    min_days = rule.filters.min_days_to_resolution
    if min_days is not None and days_to_resolution < min_days:
        return False
    max_days = rule.filters.max_days_to_resolution
    return max_days is None or days_to_resolution <= max_days


def _classify_entry_skip(market: BacktestMarket, rule: PredictionRule) -> str:
    """Name the skip reason when no row satisfied the full entry predicate.

    Falls back to the price-only predicate so the diagnostics still separate
    "never printed inside the band" from "printed in band, but every such
    print sat outside the time-to-resolution window".
    """
    band_row = select_earliest_eligible_observation(
        market.yes_token_rows,
        partial(_is_in_entry_band, rule=rule),
    )
    if band_row is None:
        return "no_eligible_entry"
    days_to_resolution = _days_between(band_row.timestamp, market.end_date)
    min_days = rule.filters.min_days_to_resolution
    if min_days is not None and days_to_resolution < min_days:
        return "ttr_min_unmet"
    return "ttr_max_exceeded"


def _build_trade(
    market: BacktestMarket,
    entry_row: PriceRow,
    rule: PredictionRule,
    *,
    out_skipped: dict[str, int] | None,
    entry_cost: float,
    exit_cost: float,
) -> Trade | None:
    """Construct one Trade from an eligible entry, dispatching on exit type.

    ``entry_cost`` is folded into the cost basis here (§11.6); a cost that
    pushes the effective entry outside (0, 1] skips the trade with
    ``cost_makes_entry_invalid`` rather than feeding ``_terminal_payoff`` an
    invalid price.
    """
    entry_price_effective = entry_row.price + entry_cost
    if not 0.0 < entry_price_effective <= 1.0:
        _bump(out_skipped, "cost_makes_entry_invalid")
        return None
    if isinstance(rule.exit, HoldToResolutionExit):
        return _trade_at_resolution(market, entry_row, rule.side, entry_price_effective)
    if isinstance(rule.exit, StopTakeProfitExit):
        return _trade_with_intermediate_exits(
            market,
            entry_row,
            rule.side,
            rule.exit,
            entry_price_effective=entry_price_effective,
            exit_cost=exit_cost,
            out_skipped=out_skipped,
        )
    # Unreachable while ExitRule remains the declared discriminated union;
    # surface loudly if a new variant lands without engine wiring.
    msg = f"Unsupported exit type {type(rule.exit).__name__!r}"
    raise TypeError(msg)


def _trade_at_resolution(
    market: BacktestMarket,
    entry_row: PriceRow,
    side: str,
    entry_price_effective: float,
) -> Trade:
    """Exit at settlement; payoff math from winning_outcome.

    Per docs/prediction-markets-intermediate-exits-plan.md:103 the exit
    metadata describes the settlement, not the last sampled quote:
    ``exit_price`` is the terminal 0/1 settlement value, ``exit_ts`` is the
    market's scheduled ``end_date``, and ``time_to_exit_days`` measures the
    capital lock-up from entry to that date. (``end_date`` is the scheduled
    close, which need not be the exact settlement instant.) PnL math is the
    §10.4 terminal payoff on the cost-adjusted entry
    (``entry_price_effective``) — ``1 - entry`` (win) or ``-entry`` (lose) —
    and is unchanged. ``exit_cost`` is not applied: resolution settles to
    0/1 with no exit transaction (§11.6). The displayed ``entry_price``
    stays the observed market price. Reused for the stop_take_profit
    max-hold-not-violated fallthrough.
    """
    # Winning outcome is matched against the YES outcome label per market
    # because the YES side maps onto a specific outcome ("Yes" by
    # convention but not guaranteed).
    realized_win = market.winning_outcome_label.strip().lower() == "yes"
    return_on_cost, pnl_per_contract = _terminal_payoff(entry_price_effective, realized_win)
    return Trade(
        condition_id=market.condition_id,
        event_slug=market.event_slug,
        side=side,
        entry_ts=entry_row.timestamp,
        entry_price=entry_row.price,
        exit_ts=market.end_date,
        exit_price=1.0 if realized_win else 0.0,
        realized_win=realized_win,
        return_on_cost=return_on_cost,
        pnl_per_contract=pnl_per_contract,
        exit_reason="resolution",
        time_to_exit_days=_days_between(entry_row.timestamp, market.end_date),
    )


def _trade_with_intermediate_exits(
    market: BacktestMarket,
    entry_row: PriceRow,
    side: str,
    exit_rule: StopTakeProfitExit,
    *,
    entry_price_effective: float,
    exit_cost: float,
    out_skipped: dict[str, int] | None,
) -> Trade | None:
    """Walk sampled rows after entry, returning the first trigger or resolution.

    Trigger order per row: stop, take_profit, expiry. ``stop_price`` and
    ``take_profit_price`` cannot simultaneously fire on a single scalar
    sample because the schema enforces ``stop_price < take_profit_price``.

    Fallthrough rules — when no row triggers an exit:
        - ``max_hold_days`` is None, or resolution occurs on/before
          ``entry_ts + max_hold_days``: terminal-resolution payoff.
        - Otherwise (resolution past the boundary and no sample
          straddles it): skip the trade with ``no_exit_price_for_max_hold``.
          Refusing to invent an expiry price keeps the response truthful
          when the price history has a gap across the boundary.
    """
    boundary_ts = _max_hold_boundary(entry_row.timestamp, exit_rule.max_hold_days)
    for row in market.yes_token_rows:
        if row.timestamp <= entry_row.timestamp:
            continue
        reason = _trigger_for_row(row, exit_rule, boundary_ts)
        if reason is None:
            continue
        return _intermediate_trade(
            market,
            entry_row,
            row,
            side,
            reason,
            entry_price_effective=entry_price_effective,
            exit_cost=exit_cost,
        )
    if boundary_ts is not None and market.end_date > boundary_ts:
        _bump(out_skipped, "no_exit_price_for_max_hold")
        return None
    return _trade_at_resolution(market, entry_row, side, entry_price_effective)


def _trigger_for_row(
    row: PriceRow,
    exit_rule: StopTakeProfitExit,
    boundary_ts: datetime | None,
) -> ExitReason | None:
    """Return the exit reason fired by ``row`` (stop, take_profit, expiry), if any.

    Boundary wins ties. When ``row.timestamp >= boundary_ts`` the trade has
    logically expired under max_hold_days before this row is observed, so we
    label the exit ``expiry`` even if the row's price also crosses stop or
    take_profit. Otherwise ``exit_breakdown`` would attribute max-hold exits
    to whatever price trigger happened to be in the same sampling bucket.
    """
    if boundary_ts is not None and row.timestamp >= boundary_ts:
        return "expiry"
    if exit_rule.stop_price is not None and row.price <= exit_rule.stop_price:
        return "stop"
    if exit_rule.take_profit_price is not None and row.price >= exit_rule.take_profit_price:
        return "take_profit"
    return None


def _intermediate_trade(
    market: BacktestMarket,
    entry_row: PriceRow,
    exit_row: PriceRow,
    side: str,
    reason: ExitReason,
    *,
    entry_price_effective: float,
    exit_cost: float,
) -> Trade:
    """Construct a booked-PnL Trade from a triggered intermediate exit row.

    PnL is net of the assumed costs (§11.6): the effective exit
    (``exit_row.price - exit_cost``, floored at 0) less the cost-adjusted
    entry. Displayed ``entry_price``/``exit_price`` stay the observed
    samples, so at zero cost the booked numbers are unchanged.
    """
    exit_price_effective = max(0.0, exit_row.price - exit_cost)
    pnl_per_contract = exit_price_effective - entry_price_effective
    return_on_cost = pnl_per_contract / entry_price_effective
    return Trade(
        condition_id=market.condition_id,
        event_slug=market.event_slug,
        side=side,
        entry_ts=entry_row.timestamp,
        entry_price=entry_row.price,
        exit_ts=exit_row.timestamp,
        exit_price=exit_row.price,
        realized_win=pnl_per_contract > 0.0,
        return_on_cost=return_on_cost,
        pnl_per_contract=pnl_per_contract,
        exit_reason=reason,
        time_to_exit_days=_days_between(entry_row.timestamp, exit_row.timestamp),
    )


def _validate_costs(*, entry_cost: float, exit_cost: float) -> None:
    """Reject execution-cost assumptions outside [0, 0.5] (§11.6)."""
    for name, value in (("entry_cost", entry_cost), ("exit_cost", exit_cost)):
        if not 0.0 <= value <= _MAX_ASSUMED_COST:
            msg = f"{name} must be in [0, {_MAX_ASSUMED_COST}]; got {value}"
            raise ValueError(msg)


def _max_hold_boundary(entry_ts: datetime, max_hold_days: int | None) -> datetime | None:
    """Return ``entry_ts + max_hold_days`` as a datetime, or None when unset."""
    if max_hold_days is None:
        return None
    return entry_ts + timedelta(days=max_hold_days)


def _days_between(start: datetime, end: datetime) -> float:
    """Whole-day fraction between two timestamps; symmetric with the plan formula."""
    return (end - start).total_seconds() / 86_400.0


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
        best/worst trade pointers and a per-reason ``exit_breakdown`` block
        (count, share, avg_return_on_cost, median_time_to_exit_days, with
        win_rate_at_resolution on the resolution slot). All numeric fields
        are rounded to 6 decimals so JSON serialization stays tight.

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
        "exit_breakdown": _exit_breakdown(trades),
    }


def _exit_breakdown(trades: list[Trade]) -> dict[str, dict[str, Any]]:
    """Per-exit-reason aggregation.

    Always emits all four reasons even when their count is zero so the
    agent can render a stable four-row table without parsing tags. ``share``
    is ``count / total_trades`` and sums to 1.0 across the four reasons.
    ``win_rate_at_resolution`` is unique to the ``resolution`` slot — it
    answers the "TP vs. let-it-ride" comparison directly.
    """
    total = len(trades)
    by_reason: dict[ExitReason, list[Trade]] = {reason: [] for reason in EXIT_REASONS}
    for trade in trades:
        by_reason[trade.exit_reason].append(trade)
    breakdown: dict[str, dict[str, Any]] = {}
    for reason in EXIT_REASONS:
        bucket = by_reason[reason]
        count = len(bucket)
        share = count / total if total else 0.0
        if count == 0:
            breakdown[reason] = {
                "count": 0,
                "share": 0.0,
                "avg_return_on_cost": 0.0,
                "median_time_to_exit_days": 0.0,
            }
            if reason == "resolution":
                breakdown[reason]["win_rate_at_resolution"] = 0.0
            continue
        breakdown[reason] = {
            "count": count,
            "share": round(share, 6),
            "avg_return_on_cost": round(_mean([t.return_on_cost for t in bucket]), 6),
            "median_time_to_exit_days": round(median([t.time_to_exit_days for t in bucket]), 6),
        }
        if reason == "resolution":
            wins = sum(1 for t in bucket if t.realized_win)
            breakdown[reason]["win_rate_at_resolution"] = round(wins / count, 6)
    return breakdown


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


def _terminal_payoff(entry_price: float, realized_win: bool) -> tuple[float, float]:
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
        "exit_reason": trade.exit_reason,
        "time_to_exit_days": round(trade.time_to_exit_days, 6),
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
        "exit_breakdown": _exit_breakdown([]),
    }
