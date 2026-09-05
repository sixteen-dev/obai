"""Calibration metric computation and bucket aggregation (§12.1, §12.2).

Pure math over Observation lists. No DB, no HTTP, no sampling — the engine
caller decides which sampling mode produced the observations and what to
do with the aggregated output.

The bucket aggregation guarantees that `market_bucket_once` cannot let
long-lived markets dominate sample_size: `effective_n` always reports the
distinct-market count, and `raw_observation_count` reports the
``len(observations)`` separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .buckets import all_ttr_buckets
from .observations import Observation, SamplingMode

# Floor on probabilities going into log() to keep log_loss finite for
# exact 0/1 prices. Matches the convention used in scikit-learn\'s
# log_loss; documenting the choice here so a future tightening is
# deliberate, not silent.
_LOG_EPS = 1e-15
_WIN_THRESHOLD = 0.5
# A bucket's realized_frequency is a noisy estimate below this many markets.
# Buckets under the floor are tagged `low_n=True` and excluded from the
# overall expected_calibration_error so a small bucket's lucky run cannot
# bias the headline number, and downstream callers cannot mistake the
# bucket's frequency for a usable base rate.
_BUCKET_USABILITY_FLOOR = 10


@dataclass(frozen=True)
class CalibrationBucket:
    """Aggregated calibration result for one (price_bucket, ttr_bucket)."""

    price_bucket: str
    ttr_bucket: str
    sample_size: int  # raw observations contributing to the bucket
    market_count: int  # distinct condition_ids contributing
    implied_probability: float  # mean of implied prices
    realized_frequency: float  # mean of realized win flags
    excess_return: float  # realized - implied
    brier_score: float
    log_loss: float
    low_n: bool  # market_count < _BUCKET_USABILITY_FLOOR; frequency is unreliable


@dataclass(frozen=True)
class CalibrationSummary:
    """Top-level aggregated calibration over all observations."""

    sampling_mode: SamplingMode
    sample_size: int
    raw_observation_count: int
    market_count: int
    effective_n: int
    overall_brier: float
    overall_log_loss: float
    expected_calibration_error: float
    buckets: list[CalibrationBucket]
    low_n_bucket_count: int  # count of buckets excluded from expected_calibration_error


def aggregate_calibration(
    observations: list[Observation],
    *,
    sampling_mode: SamplingMode,
) -> CalibrationSummary:
    """Aggregate observations into a CalibrationSummary.

    The sampling_mode argument is recorded on the result but does not
    re-bucket the observations — the upstream observation generator
    already decided which rows survive each mode. Passing the mode here is
    explicit so the response contract can echo it.

    Args:
        observations: Observation list produced by engine.observations.
        sampling_mode: Mode the caller used to generate ``observations``.

    Returns:
        CalibrationSummary.

    """
    if not observations:
        return _empty_summary(sampling_mode)

    by_bucket: dict[tuple[str, str], list[Observation]] = {}
    for obs in observations:
        key = (obs.price_bucket.label, obs.ttr_bucket)
        by_bucket.setdefault(key, []).append(obs)

    buckets = [
        _summarize_bucket(price_label, ttr_label, group)
        for (price_label, ttr_label), group in sorted(
            by_bucket.items(), key=lambda kv: (kv[0][0], _ttr_order(kv[0][1]))
        )
    ]

    raw_count = len(observations)
    distinct_markets = len({obs.condition_id for obs in observations})
    sample_size = raw_count if sampling_mode == "sample_weighted" else distinct_markets
    effective_n = distinct_markets

    overall_brier = _mean(_brier_terms(observations))
    overall_log_loss = _mean(_log_loss_terms(observations))
    usable_buckets = [b for b in buckets if not b.low_n]
    usable_n = sum(b.sample_size for b in usable_buckets)
    ece = _expected_calibration_error(usable_buckets, usable_n)
    return CalibrationSummary(
        sampling_mode=sampling_mode,
        sample_size=sample_size,
        raw_observation_count=raw_count,
        market_count=distinct_markets,
        effective_n=effective_n,
        overall_brier=overall_brier,
        overall_log_loss=overall_log_loss,
        expected_calibration_error=ece,
        buckets=buckets,
        low_n_bucket_count=len(buckets) - len(usable_buckets),
    )


def summary_to_dict(summary: CalibrationSummary) -> dict[str, Any]:
    """Serialize a CalibrationSummary into a tool-response-friendly dict."""
    return {
        "sampling_mode": summary.sampling_mode,
        "sample_size": summary.sample_size,
        "raw_observation_count": summary.raw_observation_count,
        "market_count": summary.market_count,
        "effective_n": summary.effective_n,
        "overall_brier": round(summary.overall_brier, 6),
        "overall_log_loss": round(summary.overall_log_loss, 6),
        "expected_calibration_error": round(summary.expected_calibration_error, 6),
        "low_n_bucket_count": summary.low_n_bucket_count,
        "buckets": [_bucket_to_dict(b) for b in summary.buckets],
    }


def _bucket_to_dict(b: CalibrationBucket) -> dict[str, Any]:
    return {
        "price_bucket": b.price_bucket,
        "ttr_bucket": b.ttr_bucket,
        "sample_size": b.sample_size,
        "market_count": b.market_count,
        "implied_probability": round(b.implied_probability, 6),
        "realized_frequency": round(b.realized_frequency, 6),
        "excess_return": round(b.excess_return, 6),
        "brier_score": round(b.brier_score, 6),
        "log_loss": round(b.log_loss, 6),
        "low_n": b.low_n,
    }


def _summarize_bucket(
    price_label: str,
    ttr_label: str,
    group: list[Observation],
) -> CalibrationBucket:
    """Aggregate a single (price_bucket, ttr_bucket) group."""
    implied = _mean([o.implied_probability for o in group])
    realized = _mean([o.realized_win for o in group])
    # Observations from one market share a single resolution outcome, so the
    # independent unit for the usability gate is the distinct market count.
    market_count = len({o.condition_id for o in group})
    return CalibrationBucket(
        price_bucket=price_label,
        ttr_bucket=ttr_label,
        sample_size=len(group),
        market_count=market_count,
        implied_probability=implied,
        realized_frequency=realized,
        excess_return=realized - implied,
        brier_score=_mean(_brier_terms(group)),
        log_loss=_mean(_log_loss_terms(group)),
        low_n=market_count < _BUCKET_USABILITY_FLOOR,
    )


def _brier_terms(observations: list[Observation]) -> list[float]:
    return [(obs.implied_probability - obs.realized_win) ** 2 for obs in observations]


def _log_loss_terms(observations: list[Observation]) -> list[float]:
    terms: list[float] = []
    for obs in observations:
        p = max(min(obs.implied_probability, 1.0 - _LOG_EPS), _LOG_EPS)
        if obs.realized_win > _WIN_THRESHOLD:
            terms.append(-math.log(p))
        else:
            terms.append(-math.log(1.0 - p))
    return terms


def _expected_calibration_error(buckets: list[CalibrationBucket], total_n: int) -> float:
    """Compute the standard ECE: weighted absolute gap between realized and implied per bucket."""
    if total_n == 0:
        return 0.0
    weighted_gap = sum(
        b.sample_size * abs(b.realized_frequency - b.implied_probability) for b in buckets
    )
    return weighted_gap / total_n


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _ttr_order(ttr_label: str) -> int:
    """Stable ordering for TTR buckets in the response."""
    buckets = all_ttr_buckets()
    return buckets.index(ttr_label) if ttr_label in buckets else len(buckets)


def _empty_summary(sampling_mode: SamplingMode) -> CalibrationSummary:
    """Stable zero-summary shape so tool responses stay schema-consistent."""
    return CalibrationSummary(
        sampling_mode=sampling_mode,
        sample_size=0,
        raw_observation_count=0,
        market_count=0,
        effective_n=0,
        overall_brier=0.0,
        overall_log_loss=0.0,
        expected_calibration_error=0.0,
        buckets=[],
        low_n_bucket_count=0,
    )
