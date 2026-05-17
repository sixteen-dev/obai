"""Tests for engine.observations — selection + bucket_observations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.observations import (
    MarketContext,
    bucket_observations,
    select_earliest_eligible_observation,
)
from src.storage import PriceRow


def _aware(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _price_row(
    *,
    token_id: str,
    condition_id: str,
    timestamp: datetime,
    price: float,
) -> PriceRow:
    return PriceRow(
        token_id=token_id,
        condition_id=condition_id,
        timestamp=timestamp,
        price=price,
        fidelity_minutes=60,
        source="clob_prices_history",
        fetched_at=timestamp,
    )


def test_select_earliest_eligible_observation_returns_min_timestamp_match() -> None:
    """Earliest row passing the predicate wins."""
    rows = [
        _price_row(token_id="t", condition_id="0xc", timestamp=_aware(2026, 5, 16, 10), price=0.5),
        _price_row(token_id="t", condition_id="0xc", timestamp=_aware(2026, 5, 16, 9), price=0.4),
        _price_row(token_id="t", condition_id="0xc", timestamp=_aware(2026, 5, 16, 8), price=0.9),
    ]
    selected = select_earliest_eligible_observation(rows, lambda r: r.price < 0.5)
    assert selected is not None
    assert selected.timestamp == _aware(2026, 5, 16, 9)
    assert selected.price == 0.4


def test_select_earliest_eligible_returns_none_when_no_match() -> None:
    """If nothing satisfies, return None, not the first row."""
    rows = [_price_row(token_id="t", condition_id="0xc", timestamp=_aware(2026, 5, 16), price=0.9)]
    assert select_earliest_eligible_observation(rows, lambda r: r.price < 0.1) is None


def test_bucket_observations_market_bucket_once_dedupes_within_bucket() -> None:
    """20 sampled prices in one bucket should collapse to ONE market_bucket_once obs."""
    end_date = _aware(2026, 6, 1)
    ctx = MarketContext(
        condition_id="0xc",
        end_date=end_date,
        winning_outcome_label="Yes",
    )
    # 10h remaining at start, 1-minute spacing → all 20 rows stay inside the 6_12h
    # bucket, which is the case the test is actually trying to verify.
    base_ts = end_date - timedelta(hours=10)
    rows = [
        _price_row(
            token_id="tYes",
            condition_id="0xc",
            timestamp=base_ts + timedelta(minutes=i),
            price=0.50,
        )
        for i in range(20)
    ]
    obs_once = bucket_observations(
        market=ctx, outcome_label="Yes", rows=rows, sampling_mode="market_bucket_once"
    )
    obs_weighted = bucket_observations(
        market=ctx, outcome_label="Yes", rows=rows, sampling_mode="sample_weighted"
    )
    assert len(obs_once) == 1  # one bucket entry
    assert len(obs_weighted) == 20
    assert obs_once[0].realized_win == 1.0
    assert obs_once[0].observation_ts == base_ts  # earliest sampled point wins


def test_bucket_observations_realized_zero_when_loser_outcome() -> None:
    """When outcome_label != winning_outcome, realized_win is 0.0."""
    end_date = _aware(2026, 6, 1)
    ctx = MarketContext(
        condition_id="0xc",
        end_date=end_date,
        winning_outcome_label="Yes",
    )
    rows = [
        _price_row(
            token_id="tNo",
            condition_id="0xc",
            timestamp=end_date - timedelta(hours=3),
            price=0.3,
        )
    ]
    obs = bucket_observations(
        market=ctx, outcome_label="No", rows=rows, sampling_mode="market_bucket_once"
    )
    assert obs[0].realized_win == 0.0


def test_bucket_observations_rejects_both_mode() -> None:
    """'both' is delegated upstream — passing it here is an error."""
    end_date = _aware(2026, 6, 1)
    ctx = MarketContext(condition_id="0xc", end_date=end_date, winning_outcome_label="Yes")
    with pytest.raises(ValueError, match="both"):
        bucket_observations(market=ctx, outcome_label="Yes", rows=[], sampling_mode="both")
