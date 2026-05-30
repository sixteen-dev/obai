"""Empirical edge estimation: live price vs our own measured base rate (§12.5).

Pure functions over a ``CalibrationSummary``. Given a live YES price and a
time-to-resolution bucket, locate the matching calibration bucket, report the
realized base rate, the edge (``base_rate - price``), and a Wilson score
interval whose ``n`` is the bucket's **distinct-market count** — not the raw
observation count, because same-market points are correlated and share one
resolution (§5.2 reasoning). When no bucket matches or the bucket holds fewer
than ``_MIN_CI_MARKETS`` distinct markets, ``edge`` is ``None`` with a
``reason``; we never fabricate a base rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from .buckets import DEFAULT_PRICE_BUCKET_SIZE, price_bucket
from .calibration import CalibrationBucket, CalibrationSummary

# Standard-normal quantile for a two-sided 95% Wilson interval.
_WILSON_Z = 1.959963984540054

# Minimum distinct markets for a usable edge read. The CI treats markets as the
# independent sample (``ci_n = market_count``), so the usability gate must key
# off the SAME quantity — not the sample_size-based ``bucket.low_n`` flag, which
# can be False while ``market_count`` is below the floor (e.g. both outcomes of a
# handful of markets landing in one ~0.50 bucket). Matches the §12 floor of 10.
_MIN_CI_MARKETS = 10

EdgeReason = Literal["no_bucket", "low_n"]


@dataclass(frozen=True)
class EdgeEstimate:
    """A YES-side edge read for one live (price, ttr_bucket).

    ``edge`` is ``base_rate - price`` and is populated only when a usable
    (non-``low_n``) bucket matched; otherwise it is ``None`` and ``reason``
    names why. The estimate is a population base rate for the bucket, not a
    forecast for any specific market (§5.8).
    """

    side: str
    price: float
    price_bucket: str
    ttr_bucket: str
    base_rate: float | None
    base_rate_ci: tuple[float, float] | None
    ci_n: int
    edge: float | None
    reason: EdgeReason | None
    sample_size: int
    market_count: int
    low_n: bool


def estimate_edge(
    *,
    price: float,
    ttr_bucket: str,
    calibration: CalibrationSummary,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
) -> EdgeEstimate:
    """Estimate the YES-side edge for a live price against a calibration run.

    Args:
        price: Live YES price in (0, 1).
        ttr_bucket: Time-to-resolution bucket label (see ``buckets``).
        calibration: A ``CalibrationSummary`` over the chosen universe;
            should come from ``market_bucket_once`` sampling so the CI's
            Bernoulli trials are independent.
        price_bucket_size: Must match the width used for ``calibration``.

    Returns:
        An ``EdgeEstimate``. ``edge``/``base_rate_ci`` are ``None`` with a
        ``reason`` when no bucket matches or the bucket is too thin.

    Raises:
        ValueError: If ``price`` is not strictly inside (0, 1).

    """
    if not 0.0 < price < 1.0:
        msg = f"price must be in (0, 1); got {price}"
        raise ValueError(msg)
    label = price_bucket(price, price_bucket_size).label
    bucket = _find_bucket(calibration, label, ttr_bucket)
    if bucket is None:
        return _empty_estimate(price, label, ttr_bucket, reason="no_bucket")
    if bucket.low_n or bucket.market_count < _MIN_CI_MARKETS:
        return EdgeEstimate(
            side="YES",
            price=price,
            price_bucket=label,
            ttr_bucket=ttr_bucket,
            base_rate=round(bucket.realized_frequency, 6),
            base_rate_ci=None,
            ci_n=bucket.market_count,
            edge=None,
            reason="low_n",
            sample_size=bucket.sample_size,
            market_count=bucket.market_count,
            low_n=True,
        )
    low, high = _wilson_interval(bucket.realized_frequency, bucket.market_count)
    return EdgeEstimate(
        side="YES",
        price=price,
        price_bucket=label,
        ttr_bucket=ttr_bucket,
        base_rate=round(bucket.realized_frequency, 6),
        base_rate_ci=(round(low, 6), round(high, 6)),
        ci_n=bucket.market_count,
        edge=round(bucket.realized_frequency - price, 6),
        reason=None,
        sample_size=bucket.sample_size,
        market_count=bucket.market_count,
        low_n=False,
    )


def estimate_to_dict(estimate: EdgeEstimate) -> dict[str, Any]:
    """Render an ``EdgeEstimate`` as a JSON-friendly dict (CI tuple → list)."""
    return {
        "side": estimate.side,
        "price": estimate.price,
        "price_bucket": estimate.price_bucket,
        "ttr_bucket": estimate.ttr_bucket,
        "base_rate": estimate.base_rate,
        "base_rate_ci": list(estimate.base_rate_ci) if estimate.base_rate_ci else None,
        "ci_n": estimate.ci_n,
        "edge": estimate.edge,
        "reason": estimate.reason,
        "sample_size": estimate.sample_size,
        "market_count": estimate.market_count,
        "low_n": estimate.low_n,
    }


def _empty_estimate(
    price: float, label: str, ttr_bucket: str, *, reason: EdgeReason
) -> EdgeEstimate:
    """No usable bucket: a fully-null estimate carrying only the ``reason``."""
    return EdgeEstimate(
        side="YES",
        price=price,
        price_bucket=label,
        ttr_bucket=ttr_bucket,
        base_rate=None,
        base_rate_ci=None,
        ci_n=0,
        edge=None,
        reason=reason,
        sample_size=0,
        market_count=0,
        low_n=True,
    )


def _find_bucket(
    calibration: CalibrationSummary, price_label: str, ttr_bucket: str
) -> CalibrationBucket | None:
    """Return the bucket matching ``(price_label, ttr_bucket)`` or None."""
    for bucket in calibration.buckets:
        if bucket.price_bucket == price_label and bucket.ttr_bucket == ttr_bucket:
            return bucket
    return None


def _wilson_interval(p_hat: float, n: int) -> tuple[float, float]:
    """Two-sided 95% Wilson score interval for ``p_hat`` over ``n`` trials.

    Clamped to [0, 1]. Returns the trivial (0, 1) when ``n <= 0`` so callers
    never divide by zero; usable buckets always pass ``n >= _MIN_CI_MARKETS``.
    """
    if n <= 0:
        return (0.0, 1.0)
    z_sq = _WILSON_Z * _WILSON_Z
    denom = 1.0 + z_sq / n
    center = (p_hat + z_sq / (2.0 * n)) / denom
    margin = (_WILSON_Z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z_sq / (4.0 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))
