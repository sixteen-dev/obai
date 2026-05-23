"""Tests for engine.longshot bias evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.longshot import evaluate_longshot_bias
from src.engine.observations import MarketContext, bucket_observations
from src.storage import PriceRow


def _aware(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _obs_for(condition_id: str, implied: float, realized_win: bool):
    end = _aware(2026, 6, 1)
    ctx = MarketContext(condition_id=condition_id, end_date=end, winning_outcome_label="Yes")
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
    [obs] = bucket_observations(
        market=ctx,
        outcome_label="Yes" if realized_win else "No",
        rows=rows,
        sampling_mode="market_bucket_once",
    )
    return obs


def test_longshot_tail_captures_under_threshold_only() -> None:
    """Price < longshot_max_price ⇒ longshot; 0.10 (boundary) goes to middle."""
    observations = [
        _obs_for("0xA", 0.05, True),
        _obs_for("0xB", 0.08, False),
        _obs_for("0xC", 0.10, False),  # boundary — strict less than
        _obs_for("0xD", 0.50, True),
    ]
    result = evaluate_longshot_bias(observations, longshot_max_price=0.10, favorite_min_price=0.90)
    assert result.longshot.sample_size == 2
    assert result.longshot.market_count == 2


def test_favorite_tail_is_inclusive_of_threshold() -> None:
    """Price >= favorite_min_price ⇒ favorite."""
    observations = [
        _obs_for("0xA", 0.90, True),
        _obs_for("0xB", 0.95, True),
        _obs_for("0xC", 0.85, True),
    ]
    result = evaluate_longshot_bias(observations, longshot_max_price=0.10, favorite_min_price=0.90)
    assert result.favorite.sample_size == 2


def test_excess_return_is_realized_minus_implied() -> None:
    """8% implied + 50% realized → excess_return ≈ 0.42."""
    observations = [
        _obs_for("0xA", 0.08, True),
        _obs_for("0xB", 0.08, False),
    ]
    result = evaluate_longshot_bias(observations, longshot_max_price=0.10, favorite_min_price=0.90)
    assert result.longshot.implied_probability == 0.08
    assert result.longshot.realized_frequency == 0.5
    assert abs(result.longshot.excess_return - 0.42) < 1e-12


def test_yes_return_per_contract_payoff_math() -> None:
    """At p=0.10, winning contract pays 0.9, losing pays -0.10 → avg over 2 = 0.40."""
    observations = [
        _obs_for("0xA", 0.10, True),
        _obs_for("0xB", 0.10, False),
    ]
    result = evaluate_longshot_bias(observations, longshot_max_price=0.20, favorite_min_price=0.80)
    expected = ((1.0 - 0.10) + (-0.10)) / 2
    assert abs(result.longshot.yes_return_per_contract - expected) < 1e-12


def test_threshold_validation_rejects_out_of_order() -> None:
    """favorite_min_price <= longshot_max_price is a programming error."""
    with pytest.raises(ValueError, match="favorite_min_price"):
        evaluate_longshot_bias([], longshot_max_price=0.5, favorite_min_price=0.5)


def test_threshold_validation_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        evaluate_longshot_bias([], longshot_max_price=-0.1, favorite_min_price=0.5)
    with pytest.raises(ValueError):
        evaluate_longshot_bias([], longshot_max_price=0.1, favorite_min_price=1.5)
