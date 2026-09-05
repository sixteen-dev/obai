"""Tests for engine.sizing — Kelly + drawdown-constrained sizing."""

from __future__ import annotations

import pytest

from src.engine.sizing import (
    drawdown_constrained_fraction,
    estimate_kelly,
    estimate_sizing,
)


def test_estimate_kelly_closed_form_known_result() -> None:
    """For p=0.6, b=1: f* = p - q/b = 0.6 - 0.4/1 = 0.2."""
    est = estimate_kelly(returns=[1.0, -1.0, 1.0, -1.0], win_prob=0.6, payoff_odds=1.0)
    assert est.method == "closed_form"
    assert est.naive_kelly == pytest.approx(0.2, abs=1e-9)
    assert est.half_kelly == pytest.approx(0.1, abs=1e-9)


def test_estimate_kelly_closed_form_clamps_to_zero_when_negative_edge() -> None:
    """p=0.4, b=1 → negative edge; the naive fraction must clamp at 0."""
    est = estimate_kelly(returns=[1.0, -1.0], win_prob=0.4, payoff_odds=1.0)
    assert est.naive_kelly == 0.0


def test_estimate_kelly_grid_search_returns_finite_fraction_for_winning_dist() -> None:
    """Positive-EV mixed return set should yield a positive grid Kelly."""
    est = estimate_kelly(returns=[0.5, -0.3, 0.5, -0.3, 0.5])
    assert est.method == "grid_search"
    assert est.naive_kelly > 0.0
    assert est.half_kelly == pytest.approx(est.naive_kelly * 0.5, abs=1e-9)


def test_estimate_kelly_rejects_partial_closed_form_inputs() -> None:
    """Providing only one of (win_prob, payoff_odds) is a programming error."""
    with pytest.raises(ValueError, match="closed-form Kelly"):
        estimate_kelly(returns=[0.1, -0.1], win_prob=0.6)


def test_estimate_kelly_capped_never_exceeds_half() -> None:
    """capped_kelly is min(naive, 0.5) — a production safety cap."""
    est = estimate_kelly(returns=[1.0, 1.0, -1.0, 1.0], win_prob=0.99, payoff_odds=1.0)
    assert est.capped_kelly <= 0.5


def test_drawdown_constrained_fraction_returns_zero_when_limit_too_tight() -> None:
    """An extreme limit on a volatile distribution leaves no safe fraction."""
    f = drawdown_constrained_fraction(
        returns=[-1.0, -1.0, 1.0, -1.0],
        max_drawdown_limit=0.001,
        num_paths=100,
        seed=1,
    )
    assert f == 0.0


def test_drawdown_constrained_fraction_smaller_under_tighter_limit() -> None:
    """Looser drawdown limit must allow at least as large a fraction."""
    returns = [0.5, -0.3, 0.5, -0.4, 0.5, -0.5]
    loose = drawdown_constrained_fraction(
        returns=returns, max_drawdown_limit=0.8, num_paths=200, seed=11
    )
    tight = drawdown_constrained_fraction(
        returns=returns, max_drawdown_limit=0.2, num_paths=200, seed=11
    )
    assert loose >= tight


def test_estimate_sizing_conservative_picks_min() -> None:
    """conservative_fraction = min(haircut * half_kelly, drawdown_constrained)."""
    sizing = estimate_sizing(
        returns=[0.5, -0.3, 0.5, -0.3, 0.5],
        max_drawdown_limit=0.8,
        confidence_haircut=0.5,
        num_paths=200,
        seed=5,
    )
    assert sizing.conservative_fraction <= sizing.estimates.half_kelly * 0.5 + 1e-9
    assert sizing.conservative_fraction <= sizing.drawdown_constrained_fraction + 1e-9


def test_estimate_sizing_capped_kelly_is_min_half_and_drawdown() -> None:
    """capped_kelly on SizingResult = min(half_kelly, drawdown_constrained)."""
    sizing = estimate_sizing(
        returns=[0.5, -0.3, 0.5, -0.3, 0.5],
        max_drawdown_limit=0.8,
        confidence_haircut=0.5,
        num_paths=200,
        seed=5,
    )
    expected = min(sizing.estimates.half_kelly, sizing.drawdown_constrained_fraction)
    assert sizing.capped_kelly == pytest.approx(expected, abs=1e-9)


def test_estimate_sizing_rejects_invalid_haircut() -> None:
    with pytest.raises(ValueError):
        estimate_sizing(
            returns=[0.1, -0.1],
            max_drawdown_limit=0.3,
            confidence_haircut=1.5,
        )


@pytest.mark.parametrize(
    ("returns", "label"),
    [
        ([-1.0] * 20, "all_losses"),
        ([1.0, -1.0] * 10, "zero_edge_even_money"),
        ([0.0] * 20, "flat"),
    ],
)
def test_estimate_kelly_grid_search_allocates_nothing_without_positive_edge(
    returns: list[float], label: str
) -> None:
    """Cash must win the grid search when no fraction beats zero log-growth."""
    est = estimate_kelly(returns=returns)
    assert est.method == "grid_search"
    assert est.naive_kelly == 0.0, label
    assert est.half_kelly == 0.0
    assert est.capped_kelly == 0.0


def test_estimate_kelly_grid_search_still_allocates_on_positive_edge() -> None:
    """The zero candidate must not shadow a genuinely positive-EV distribution."""
    est = estimate_kelly(returns=[0.5, -0.3] * 10)
    assert est.naive_kelly > 0.0
