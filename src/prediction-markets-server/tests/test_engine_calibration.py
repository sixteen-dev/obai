"""Tests for engine.calibration math."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.engine.calibration import aggregate_calibration, summary_to_dict
from src.engine.observations import MarketContext, Observation, bucket_observations
from src.storage import PriceRow


def _aware(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_obs(condition_id: str, implied: float, realized: float) -> Observation:
    end = _aware(2026, 6, 1)
    rows = [
        PriceRow(
            token_id=f"{condition_id}_t",
            condition_id=condition_id,
            timestamp=end - timedelta(hours=12),
            price=implied,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=end - timedelta(hours=12),
        )
    ]
    ctx = MarketContext(condition_id=condition_id, end_date=end, winning_outcome_label="Yes")
    outcome_label = "Yes" if realized > 0.5 else "No"
    [obs] = bucket_observations(
        market=ctx, outcome_label=outcome_label, rows=rows, sampling_mode="market_bucket_once"
    )
    return obs


def test_brier_score_known_value() -> None:
    """Two obs: p=0.7 hit + p=0.3 miss → Brier = mean(0.09, 0.09) = 0.09."""
    observations = [
        _make_obs("0xA", 0.7, 1.0),
        _make_obs("0xB", 0.3, 0.0),
    ]
    summary = aggregate_calibration(observations, sampling_mode="market_bucket_once")
    assert summary.overall_brier == pytest.approx(0.09, abs=1e-9)


def test_log_loss_finite_for_certain_predictions() -> None:
    """Implied = 1.0 with realized = 1.0 must not produce inf/NaN log loss."""
    observations = [_make_obs("0xA", 1.0, 1.0)]
    summary = aggregate_calibration(observations, sampling_mode="market_bucket_once")
    assert math.isfinite(summary.overall_log_loss)
    assert summary.overall_log_loss < 1e-13


def test_effective_n_equals_distinct_markets() -> None:
    """5 observations from 3 distinct markets → effective_n == 3."""
    observations = [
        _make_obs("0xA", 0.5, 1.0),
        _make_obs("0xA", 0.5, 1.0),
        _make_obs("0xB", 0.5, 0.0),
        _make_obs("0xB", 0.5, 0.0),
        _make_obs("0xC", 0.5, 1.0),
    ]
    summary = aggregate_calibration(observations, sampling_mode="sample_weighted")
    assert summary.raw_observation_count == 5
    assert summary.market_count == 3
    assert summary.effective_n == 3


def test_sample_size_under_market_bucket_once_uses_distinct_markets() -> None:
    """When sampling_mode='market_bucket_once' the sample_size reported is distinct markets."""
    observations = [
        _make_obs("0xA", 0.5, 1.0),
        _make_obs("0xB", 0.5, 0.0),
        _make_obs("0xC", 0.5, 1.0),
    ]
    summary = aggregate_calibration(observations, sampling_mode="market_bucket_once")
    assert summary.sample_size == 3
    assert summary.raw_observation_count == 3


def test_aggregate_empty_returns_zero_shape() -> None:
    """Empty observation list yields a stable zero-summary, not an exception."""
    summary = aggregate_calibration([], sampling_mode="market_bucket_once")
    assert summary.sample_size == 0
    assert summary.market_count == 0
    assert summary.buckets == []


def test_summary_to_dict_returns_all_required_keys() -> None:
    """Response shape must include every documented summary key."""
    summary = aggregate_calibration(
        [_make_obs("0xA", 0.4, 1.0)], sampling_mode="market_bucket_once"
    )
    d = summary_to_dict(summary)
    for key in (
        "sampling_mode",
        "sample_size",
        "raw_observation_count",
        "market_count",
        "effective_n",
        "overall_brier",
        "overall_log_loss",
        "expected_calibration_error",
        "buckets",
    ):
        assert key in d
