"""Tests for engine.risk — bootstrap reproducibility, ruin probability, cap."""

from __future__ import annotations

import pytest

from src.engine.risk import MAX_NUM_PATHS, run_monte_carlo


def test_run_monte_carlo_is_seeded_reproducible() -> None:
    """Same returns + same seed → identical numbers."""
    returns = [0.10, -0.20, 0.30, -0.40, 0.50]
    a = run_monte_carlo(
        returns=returns,
        num_paths=200,
        starting_bankroll=1.0,
        position_fraction=0.1,
        max_drawdown_limit=0.5,
        seed=99,
    )
    b = run_monte_carlo(
        returns=returns,
        num_paths=200,
        starting_bankroll=1.0,
        position_fraction=0.1,
        max_drawdown_limit=0.5,
        seed=99,
    )
    assert a == b


def test_run_monte_carlo_different_seeds_diverge() -> None:
    """Different seeds → different stats (with very high probability)."""
    returns = [0.10, -0.20, 0.30, -0.40, 0.50]
    a = run_monte_carlo(
        returns=returns,
        num_paths=200,
        starting_bankroll=1.0,
        position_fraction=0.1,
        max_drawdown_limit=0.5,
        seed=1,
    )
    b = run_monte_carlo(
        returns=returns,
        num_paths=200,
        starting_bankroll=1.0,
        position_fraction=0.1,
        max_drawdown_limit=0.5,
        seed=2,
    )
    assert a != b


def test_run_monte_carlo_ruin_probability_monotone_in_position_fraction() -> None:
    """Larger fraction → at least as much ruin probability on a losing-skewed set."""
    losers_heavy = [-1.0, -1.0, 0.5, -1.0]
    small = run_monte_carlo(
        returns=losers_heavy,
        num_paths=500,
        starting_bankroll=1.0,
        position_fraction=0.05,
        max_drawdown_limit=0.5,
        seed=7,
    )
    big = run_monte_carlo(
        returns=losers_heavy,
        num_paths=500,
        starting_bankroll=1.0,
        position_fraction=1.0,
        max_drawdown_limit=0.5,
        seed=7,
    )
    assert big.ruin_probability >= small.ruin_probability


def test_run_monte_carlo_cap_raises_above_max_paths() -> None:
    """Requesting more than MAX_NUM_PATHS must fail loud, not silently truncate."""
    with pytest.raises(ValueError, match="exceeds MAX_NUM_PATHS"):
        run_monte_carlo(
            returns=[0.1, -0.1],
            num_paths=MAX_NUM_PATHS + 1,
            starting_bankroll=1.0,
            position_fraction=0.5,
            max_drawdown_limit=0.3,
            seed=1,
        )


def test_run_monte_carlo_rejects_empty_returns() -> None:
    with pytest.raises(ValueError):
        run_monte_carlo(
            returns=[],
            num_paths=10,
            starting_bankroll=1.0,
            position_fraction=0.5,
            max_drawdown_limit=0.3,
            seed=1,
        )


def test_run_monte_carlo_rejects_zero_position_fraction() -> None:
    with pytest.raises(ValueError):
        run_monte_carlo(
            returns=[0.1, -0.1],
            num_paths=10,
            starting_bankroll=1.0,
            position_fraction=0.0,
            max_drawdown_limit=0.3,
            seed=1,
        )


def test_run_monte_carlo_drawdown_limit_breach_is_counted() -> None:
    """Tight limit on a high-volatility distribution should report > 0 breaches."""
    result = run_monte_carlo(
        returns=[-1.0, 1.0, -1.0, 1.0],
        num_paths=200,
        starting_bankroll=1.0,
        position_fraction=1.0,
        max_drawdown_limit=0.05,
        seed=42,
    )
    assert result.prob_exceeds_drawdown_limit > 0.0
