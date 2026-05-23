"""Longshot vs favorite bias analysis (§10.3).

Pure function over Observation lists. Categorizes each observation as
longshot, favorite, or middle based on implied probability thresholds and
reports realized win-rate against implied probability for each tail.

The "side" parameter is a no-op for binary YES analyses but is part of the
contract so future multi-outcome or NO-side studies can be added without
breaking the response shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .observations import Observation

_WIN_THRESHOLD = 0.5

Side = Literal["yes", "no", "both"]


@dataclass(frozen=True)
class TailStats:
    """Aggregated outcome stats for the longshot or favorite tail."""

    label: str  # "longshot" or "favorite"
    sample_size: int  # raw observation count
    market_count: int  # distinct condition_ids contributing
    implied_probability: float
    realized_frequency: float
    excess_return: float  # realized - implied
    yes_return_per_contract: float  # avg (realized - implied) on cost basis


@dataclass(frozen=True)
class LongshotResult:
    """Top-level longshot vs favorite output."""

    side: Side
    longshot_max_price: float
    favorite_min_price: float
    middle_count: int
    longshot: TailStats
    favorite: TailStats
    bucket_detail: list[dict[str, Any]]


def evaluate_longshot_bias(
    observations: list[Observation],
    *,
    longshot_max_price: float,
    favorite_min_price: float,
    side: Side = "yes",
) -> LongshotResult:
    """Categorize observations into longshot vs favorite tails and aggregate.

    Args:
        observations: Observation list (typically ``market_bucket_once``
            sampled so a single market does not dominate).
        longshot_max_price: Strictly-less-than upper bound for the longshot
            tail (e.g. 0.10 keeps anything below 10¢).
        favorite_min_price: Greater-than-or-equal lower bound for the
            favorite tail (e.g. 0.90 keeps anything at 90¢ and above).
        side: ``"yes"``, ``"no"``, or ``"both"``; carried into the result
            for the response contract.

    Returns:
        LongshotResult with tail stats and per-bucket detail.

    Raises:
        ValueError: If price thresholds are out of order or out of [0, 1].

    """
    if not 0.0 <= longshot_max_price <= 1.0:
        msg = f"longshot_max_price must be in [0, 1], got {longshot_max_price}"
        raise ValueError(msg)
    if not 0.0 <= favorite_min_price <= 1.0:
        msg = f"favorite_min_price must be in [0, 1], got {favorite_min_price}"
        raise ValueError(msg)
    if favorite_min_price <= longshot_max_price:
        msg = (
            "favorite_min_price must be greater than longshot_max_price; "
            f"got {favorite_min_price=} {longshot_max_price=}"
        )
        raise ValueError(msg)

    longshot_obs = [o for o in observations if o.implied_probability < longshot_max_price]
    favorite_obs = [o for o in observations if o.implied_probability >= favorite_min_price]
    middle_count = len(observations) - len(longshot_obs) - len(favorite_obs)

    bucket_detail = _bucket_breakdown(observations)
    return LongshotResult(
        side=side,
        longshot_max_price=longshot_max_price,
        favorite_min_price=favorite_min_price,
        middle_count=middle_count,
        longshot=_tail_stats("longshot", longshot_obs),
        favorite=_tail_stats("favorite", favorite_obs),
        bucket_detail=bucket_detail,
    )


def result_to_dict(result: LongshotResult) -> dict[str, Any]:
    """Render a LongshotResult into a response-ready dict."""
    return {
        "side": result.side,
        "longshot_max_price": result.longshot_max_price,
        "favorite_min_price": result.favorite_min_price,
        "middle_count": result.middle_count,
        "longshot": _tail_to_dict(result.longshot),
        "favorite": _tail_to_dict(result.favorite),
        "bucket_detail": result.bucket_detail,
    }


def _tail_stats(label: str, obs: list[Observation]) -> TailStats:
    if not obs:
        return TailStats(
            label=label,
            sample_size=0,
            market_count=0,
            implied_probability=0.0,
            realized_frequency=0.0,
            excess_return=0.0,
            yes_return_per_contract=0.0,
        )
    sample_size = len(obs)
    market_count = len({o.condition_id for o in obs})
    implied = sum(o.implied_probability for o in obs) / sample_size
    realized = sum(o.realized_win for o in obs) / sample_size
    # YES return per contract for buying at implied price p:
    # win:  (1 - p)
    # lose: (-p)
    per_contract = sum(_yes_return(o) for o in obs) / sample_size
    return TailStats(
        label=label,
        sample_size=sample_size,
        market_count=market_count,
        implied_probability=implied,
        realized_frequency=realized,
        excess_return=realized - implied,
        yes_return_per_contract=per_contract,
    )


def _yes_return(obs: Observation) -> float:
    """Per-contract YES return at the implied price."""
    if obs.realized_win > _WIN_THRESHOLD:
        return 1.0 - obs.implied_probability
    return -obs.implied_probability


def _bucket_breakdown(observations: list[Observation]) -> list[dict[str, Any]]:
    """Stable per-price-bucket counts for the response."""
    by_bucket: dict[str, list[Observation]] = {}
    for o in observations:
        by_bucket.setdefault(o.price_bucket.label, []).append(o)
    return [
        {
            "price_bucket": label,
            "sample_size": len(group),
            "market_count": len({o.condition_id for o in group}),
            "implied_probability": round(sum(o.implied_probability for o in group) / len(group), 6),
            "realized_frequency": round(sum(o.realized_win for o in group) / len(group), 6),
        }
        for label, group in sorted(by_bucket.items())
    ]


def _tail_to_dict(stats: TailStats) -> dict[str, Any]:
    return {
        "label": stats.label,
        "sample_size": stats.sample_size,
        "market_count": stats.market_count,
        "implied_probability": round(stats.implied_probability, 6),
        "realized_frequency": round(stats.realized_frequency, 6),
        "excess_return": round(stats.excess_return, 6),
        "yes_return_per_contract": round(stats.yes_return_per_contract, 6),
    }
