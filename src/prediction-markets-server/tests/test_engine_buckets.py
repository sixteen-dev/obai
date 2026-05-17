"""Tests for engine.buckets — price + TTR bucket helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.buckets import (
    TTR_BUCKETS,
    PriceBucketBounds,
    price_bucket,
    remaining_seconds,
    time_to_resolution_bucket,
    ttr_bucket_seconds_upper,
)


def _aware(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_price_bucket_assigns_left_inclusive_half_open() -> None:
    """Price = 0.05 should land in the [0.05, 0.10) bucket, not (0.00, 0.05]."""
    bucket = price_bucket(0.05, 0.05)
    assert bucket == PriceBucketBounds(low=0.05, high=0.10)
    assert bucket.label == "0.05-0.10"


def test_price_bucket_closes_top_bucket_for_full_certainty() -> None:
    """Price = 1.0 must land in the last bucket, not be dropped or overflow."""
    bucket = price_bucket(1.0, 0.05)
    assert bucket.high == 1.0
    assert bucket.low == 0.95


def test_price_bucket_rejects_out_of_range_price() -> None:
    """Prices outside [0, 1] are programming errors — fail loud."""
    with pytest.raises(ValueError):
        price_bucket(1.5, 0.05)
    with pytest.raises(ValueError):
        price_bucket(-0.1, 0.05)


def test_price_bucket_rejects_invalid_bucket_size() -> None:
    with pytest.raises(ValueError):
        price_bucket(0.5, 0)
    with pytest.raises(ValueError):
        price_bucket(0.5, 1.5)


def test_time_to_resolution_bucket_boundaries() -> None:
    """Each bucket boundary should land in the *next* bucket (half-open)."""
    end = _aware(2026, 5, 16)
    # 0 hours remaining → 0_3h
    assert time_to_resolution_bucket(end, end) == "0_3h"
    # 3 hours remaining → 3_6h (boundary up)
    assert time_to_resolution_bucket(end - timedelta(hours=3), end) == "3_6h"
    # 12 hours remaining → 12_24h
    assert time_to_resolution_bucket(end - timedelta(hours=12), end) == "12_24h"
    # 7 days remaining → 1_4w
    assert time_to_resolution_bucket(end - timedelta(days=7), end) == "1_4w"
    # 60 days remaining → 1m_plus
    assert time_to_resolution_bucket(end - timedelta(days=60), end) == "1m_plus"


def test_time_to_resolution_bucket_requires_aware_datetimes() -> None:
    """Naive datetimes are a programming error — fail loud, do not assume UTC."""
    naive = datetime(2026, 5, 16)
    aware = _aware(2026, 5, 17)
    with pytest.raises(ValueError, match="timezone-aware"):
        time_to_resolution_bucket(naive, aware)


def test_remaining_seconds_clamps_at_zero() -> None:
    """A negative remaining time (observation after end) clamps to 0, not negative."""
    end = _aware(2026, 5, 16)
    assert remaining_seconds(end + timedelta(hours=1), end) == 0.0


def test_ttr_bucket_seconds_upper_returns_none_for_open_ended() -> None:
    """1m_plus has no upper bound — None signals open-ended."""
    assert ttr_bucket_seconds_upper("1m_plus") is None


def test_ttr_buckets_order_is_stable() -> None:
    """TTR_BUCKETS must be in display order so responses iterate consistently."""
    assert TTR_BUCKETS[0] == "0_3h"
    assert TTR_BUCKETS[-1] == "1m_plus"


def test_price_bucket_boundary_prices_land_in_documented_bucket() -> None:
    """Regression for float-fuzz bug: 0.90 // 0.05 returned 17 instead of 18.

    Each price below is a bucket boundary at bucket_size=0.05; under the
    half-open [low, high) semantics it should land in the higher bucket.
    """
    expectations = {
        0.15: "0.15-0.20",
        0.25: "0.25-0.30",
        0.30: "0.30-0.35",
        0.45: "0.45-0.50",
        0.50: "0.50-0.55",
        0.60: "0.60-0.65",
        0.75: "0.75-0.80",
        0.90: "0.90-0.95",
        0.95: "0.95-1.00",
    }
    for price, expected_label in expectations.items():
        bucket = price_bucket(price, 0.05)
        assert bucket.label == expected_label, (
            f"price={price} got {bucket.label}, want {expected_label}"
        )
