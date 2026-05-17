"""Observation generation for calibration + backtest engines.

Both calibration and backtest sit on the same primitive: a set of
(market, outcome, sampled price, terminal win/lose) tuples. Centralizing the
generator here prevents the two tools from drifting on which sampled point
counts as "the" observation for a market (§11.2 + §12.1 cross-reference).

`select_earliest_eligible_observation` is the named helper the design doc
asks for and is reused by the V1 backtest engine in Phase 4.

All functions are pure — they accept dataclasses + plain values, no DB.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..storage import PriceRow
from .buckets import (
    DEFAULT_PRICE_BUCKET_SIZE,
    PriceBucketBounds,
    price_bucket,
    time_to_resolution_bucket,
)

SamplingMode = Literal["market_bucket_once", "sample_weighted", "both"]


@dataclass(frozen=True)
class MarketContext:
    """Per-market terminal context needed to score an observation."""

    condition_id: str
    end_date: datetime
    winning_outcome_label: str  # outcome label that resolved YES (resolved markets only)


@dataclass(frozen=True)
class Observation:
    """One calibration/backtest observation.

    realized_win is 1.0 when the chosen outcome resolved YES, 0.0 otherwise.
    implied_probability is the sampled CLOB price at observation_ts; for
    binary YES markets that price is the implied probability of the YES
    outcome the agent would be buying.
    """

    condition_id: str
    outcome_label: str
    observation_ts: datetime
    implied_probability: float
    realized_win: float
    price_bucket: PriceBucketBounds
    ttr_bucket: str


def select_earliest_eligible_observation(
    rows: Iterable[PriceRow],
    eligibility: Callable[[PriceRow], bool],
) -> PriceRow | None:
    """Return the earliest PriceRow satisfying ``eligibility`` (None if none).

    Time-tie behavior: when two rows share the earliest timestamp, the
    first one in iteration order wins. Callers should pre-sort if a stable
    secondary key matters.

    Args:
        rows: Iterable of PriceRow values for one token.
        eligibility: Predicate naming the entry condition (e.g. "price
            inside [pmin, pmax]" for backtest, or "always" for
            ``market_bucket_once`` calibration).

    Returns:
        Earliest eligible PriceRow, or None.

    """
    best: PriceRow | None = None
    for row in rows:
        if not eligibility(row):
            continue
        if best is None or row.timestamp < best.timestamp:
            best = row
    return best


def bucket_observations(
    *,
    market: MarketContext,
    outcome_label: str,
    rows: list[PriceRow],
    sampling_mode: SamplingMode,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
) -> list[Observation]:
    """Materialize observations for one (market, outcome) under a sampling mode.

    `market_bucket_once`: for each (price_bucket, ttr_bucket) pair, keep the
        earliest eligible sampled point.
    `sample_weighted`: every sampled point becomes its own observation.
    `both`: ``sample_weighted`` is delegated upstream — callers asking for
        "both" should call this function twice with each mode and merge.

    Args:
        market: MarketContext for the parent market.
        outcome_label: Outcome whose terminal win/lose drives realized_win.
        rows: PriceRows for the outcome token, in any order.
        sampling_mode: ``market_bucket_once`` or ``sample_weighted`` (never
            ``both`` — see note above).
        price_bucket_size: Price bucket width, default 0.05.

    Returns:
        List of Observation values; empty when no rows pass the filter.

    Raises:
        ValueError: If ``sampling_mode`` is ``"both"``.

    """
    if sampling_mode == "both":
        msg = "bucket_observations does not accept 'both'; call twice and merge."
        raise ValueError(msg)

    realized = 1.0 if outcome_label.strip() == market.winning_outcome_label.strip() else 0.0
    if sampling_mode == "sample_weighted":
        return [
            _make_observation(market, outcome_label, row, realized, price_bucket_size)
            for row in rows
        ]

    # market_bucket_once: at most one observation per (price_bucket, ttr_bucket)
    keyed: dict[tuple[str, str], Observation] = {}
    sorted_rows = sorted(rows, key=lambda r: r.timestamp)
    for row in sorted_rows:
        try:
            obs = _make_observation(market, outcome_label, row, realized, price_bucket_size)
        except ValueError:
            continue
        key = (obs.price_bucket.label, obs.ttr_bucket)
        if key not in keyed:
            keyed[key] = obs
    return list(keyed.values())


def _make_observation(
    market: MarketContext,
    outcome_label: str,
    row: PriceRow,
    realized_win: float,
    price_bucket_size: float,
) -> Observation:
    """Build a single Observation from a PriceRow + MarketContext."""
    pb = price_bucket(row.price, price_bucket_size)
    ttr = time_to_resolution_bucket(row.timestamp, market.end_date)
    return Observation(
        condition_id=market.condition_id,
        outcome_label=outcome_label,
        observation_ts=row.timestamp,
        implied_probability=row.price,
        realized_win=realized_win,
        price_bucket=pb,
        ttr_bucket=ttr,
    )
