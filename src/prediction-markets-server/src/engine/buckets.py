"""Price-band and time-to-resolution bucket helpers (§12.1, §12.3).

Pure functions and constants. Bucket boundaries are part of the contract
because they show up in tool responses and in the calibration prompt — if
they change, the prompt update and the contract scorer both need to follow,
so they live in one obvious place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

# Default price bucket width. Calibration tools accept this as a parameter
# and we re-export the default so the agent can pass nothing and still get
# the documented bucket layout.
DEFAULT_PRICE_BUCKET_SIZE: Final[float] = 0.05

# Time-to-resolution bucket boundaries (§12.3), in seconds. Order is the
# response-rendering order; the bucket names live in TTR_BUCKETS so callers
# can iterate deterministically.
TTR_BUCKETS: Final[tuple[str, ...]] = (
    "0_3h",
    "3_6h",
    "6_12h",
    "12_24h",
    "1_2d",
    "2_7d",
    "1_4w",
    "1m_plus",
)

# Integer scale used by price_bucket to dodge float fuzz on boundary prices.
# Multiplying both ``price`` and ``bucket_size`` by 10**10 + rounding keeps
# the conversion to ints exact for any bucket_size finer than 1e-9.
_BUCKET_SCALE: Final[int] = 10**10

_TTR_UPPER_BOUNDS_SECONDS: Final[tuple[tuple[str, float], ...]] = (
    ("0_3h", 3 * 3600.0),
    ("3_6h", 6 * 3600.0),
    ("6_12h", 12 * 3600.0),
    ("12_24h", 24 * 3600.0),
    ("1_2d", 2 * 86400.0),
    ("2_7d", 7 * 86400.0),
    ("1_4w", 28 * 86400.0),
    # 1m_plus is open-ended; anything above 28 days lands here.
)


@dataclass(frozen=True)
class PriceBucketBounds:
    """Inclusive-lower / exclusive-upper price bucket bounds."""

    low: float
    high: float

    @property
    def label(self) -> str:
        """Stable bucket label like ``0.05-0.10``."""
        return f"{self.low:.2f}-{self.high:.2f}"


def price_bucket(price: float, bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE) -> PriceBucketBounds:
    """Return the bucket containing ``price`` for the chosen bucket size.

    Buckets are half-open ``[low, high)`` so a price exactly on a boundary
    lands in the *higher* bucket; the very last bucket ``[1-h, 1.0]`` is
    closed on both ends so the 100% case is not lost.

    Args:
        price: Implied probability in [0.0, 1.0].
        bucket_size: Bucket width in [0.0, 1.0].

    Returns:
        PriceBucketBounds with a stable label.

    Raises:
        ValueError: If price is outside [0, 1] or bucket_size is non-positive.

    """
    if not 0.0 <= price <= 1.0:
        msg = f"price must be in [0, 1], got {price}"
        raise ValueError(msg)
    if bucket_size <= 0.0 or bucket_size > 1.0:
        msg = f"bucket_size must be in (0, 1], got {bucket_size}"
        raise ValueError(msg)
    if price == 1.0:
        # Close the top bucket so 100% prices are not dropped to the
        # right of the histogram.
        low = round(1.0 - bucket_size, 10)
        high = 1.0
    else:
        # Float `price // bucket_size` silently misclassifies common
        # boundaries (e.g. 0.90 // 0.05 → 17 not 18) because of IEEE-754
        # division fuzz. Scale into an integer space and use integer
        # division so boundary prices land in the documented [low, high)
        # bucket. _BUCKET_SCALE picks 10**10 — plenty of room for any
        # bucket_size finer than 1e-9.
        idx = int(round(price * _BUCKET_SCALE)) // int(round(bucket_size * _BUCKET_SCALE))
        low = round(idx * bucket_size, 10)
        high = round(low + bucket_size, 10)
    return PriceBucketBounds(low=low, high=high)


def time_to_resolution_bucket(
    observation_ts: datetime,
    end_date: datetime,
) -> str:
    """Pick the §12.3 TTR bucket for an observation.

    Args:
        observation_ts: When the implied-probability observation was taken.
        end_date: Market resolution timestamp.

    Returns:
        One of TTR_BUCKETS.

    Raises:
        ValueError: If either timestamp is naive — we require aware
            datetimes so callers cannot accidentally compare across time
            zones.

    """
    if observation_ts.tzinfo is None or end_date.tzinfo is None:
        msg = "time_to_resolution_bucket requires timezone-aware datetimes"
        raise ValueError(msg)
    remaining = max((end_date - observation_ts).total_seconds(), 0.0)
    for name, upper in _TTR_UPPER_BOUNDS_SECONDS:
        if remaining < upper:
            return name
    return "1m_plus"


def all_ttr_buckets() -> tuple[str, ...]:
    """Return TTR bucket names in display order (lets callers init counters)."""
    return TTR_BUCKETS


def ttr_bucket_seconds_upper(name: str) -> float | None:
    """Return the upper bound of the named TTR bucket (None for open-ended)."""
    for label, upper in _TTR_UPPER_BOUNDS_SECONDS:
        if label == name:
            return upper
    return None


def remaining_seconds(observation_ts: datetime, end_date: datetime) -> float:
    """Compute remaining time to resolution in seconds, floored at zero."""
    delta: timedelta = end_date - observation_ts
    return max(delta.total_seconds(), 0.0)
