"""Tests for tools.empirical_kelly.estimate_empirical_kelly."""

from __future__ import annotations

import pytest

from src.tools.empirical_kelly import estimate_empirical_kelly


def test_qualitative_when_constraints_missing() -> None:
    """No bankroll AND no drawdown limit → metrics is None, guidance is text."""
    result = estimate_empirical_kelly(returns=[0.5, -0.3, 0.5, -0.3])
    assert result["metrics"] is None
    assert "qualitative_sizing_only" in result["quality_flags"]
    assert "starting_bankroll" in result["filters"]["missing_constraints"]
    assert "max_drawdown_limit" in result["filters"]["missing_constraints"]


def test_numerical_when_constraints_present() -> None:
    """Bankroll + drawdown limit supplied → numerical sizing fractions returned."""
    result = estimate_empirical_kelly(
        returns=[0.5, -0.3, 0.5, -0.3, 0.5],
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    assert result["metrics"] is not None
    for key in (
        "kelly_method",
        "naive_kelly",
        "half_kelly",
        "capped_kelly",
        "drawdown_constrained_fraction",
        "conservative_fraction",
    ):
        assert key in result["metrics"]


def test_conservative_fraction_is_min_of_inputs() -> None:
    """Conservative ≤ half_kelly * haircut, conservative ≤ drawdown_constrained."""
    result = estimate_empirical_kelly(
        returns=[0.5, -0.3, 0.5, -0.3],
        starting_bankroll=1_000.0,
        max_drawdown_limit=0.6,
        confidence_haircut=0.5,
        seed=1,
    )
    metrics = result["metrics"]
    assert metrics is not None
    assert metrics["conservative_fraction"] <= metrics["half_kelly"] * 0.5 + 1e-9
    assert metrics["conservative_fraction"] <= metrics["drawdown_constrained_fraction"] + 1e-9


def test_closed_form_path_when_win_prob_and_odds_present() -> None:
    """When win_prob + payoff_odds are passed, kelly_method should be closed_form."""
    result = estimate_empirical_kelly(
        returns=[1.0, -1.0, 1.0, -1.0],
        starting_bankroll=1_000.0,
        max_drawdown_limit=0.4,
        win_prob=0.6,
        payoff_odds=1.0,
        seed=1,
    )
    assert result["metrics"]["kelly_method"] == "closed_form"


def test_rejects_both_input_sources() -> None:
    with pytest.raises(ValueError, match="not both"):
        estimate_empirical_kelly(
            monte_carlo_input={"returns": [0.1]},
            returns=[0.2],
            starting_bankroll=100.0,
            max_drawdown_limit=0.3,
        )


def test_losing_distribution_sizes_to_zero() -> None:
    """A distribution with no positive edge must recommend no allocation at all."""
    result = estimate_empirical_kelly(
        returns=[-1.0] * 20,
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    metrics = result["metrics"]
    assert metrics is not None
    assert metrics["naive_kelly"] == 0.0
    assert metrics["half_kelly"] == 0.0
    assert metrics["conservative_fraction"] == 0.0
    assert "no_positive_edge" in result["quality_flags"]


def test_positive_edge_distribution_has_no_no_positive_edge_flag() -> None:
    """The no-trade flag must not fire when the sample supports an allocation."""
    result = estimate_empirical_kelly(
        returns=[0.5, -0.3] * 10,
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    assert result["metrics"]["naive_kelly"] > 0.0
    assert "no_positive_edge" not in result["quality_flags"]


def test_upstream_limitations_are_forwarded_from_monte_carlo_input() -> None:
    """§10.5 limitations ride the payload into the numerical response."""
    contamination = (
        "min_lifetime_volume filters on lifetime volume, which is only known "
        "after resolution — surviving markets are contaminated."
    )
    result = estimate_empirical_kelly(
        monte_carlo_input={"returns": [0.5, -0.3] * 10, "limitations": [contamination]},
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    assert result["limitations"][0] == contamination


def test_upstream_limitations_reach_the_qualitative_response() -> None:
    """Withholding numbers must not withhold the upstream data caveats."""
    contamination = "Survivorship-contaminated universe."
    result = estimate_empirical_kelly(
        monte_carlo_input={"returns": [0.5, -0.3], "limitations": [contamination]},
    )
    assert result["metrics"] is None
    assert result["limitations"][0] == contamination


def test_inline_returns_carry_no_upstream_limitations() -> None:
    result = estimate_empirical_kelly(
        returns=[0.5, -0.3] * 10,
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    assert result["limitations"][0].startswith("Kelly assumes returns are independent")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_returns(bad: float) -> None:
    with pytest.raises(ValueError, match=r"returns\[1\]"):
        estimate_empirical_kelly(
            returns=[0.1, bad, 0.2],
            starting_bankroll=1_000.0,
            max_drawdown_limit=0.30,
        )


def test_rejects_return_below_total_loss() -> None:
    with pytest.raises(ValueError, match=r"returns\[2\]"):
        estimate_empirical_kelly(
            returns=[0.1, -1.0, -1.5],
            starting_bankroll=1_000.0,
            max_drawdown_limit=0.30,
        )


def test_rejects_non_finite_returns_from_monte_carlo_input() -> None:
    with pytest.raises(ValueError, match=r"returns\[0\]"):
        estimate_empirical_kelly(
            monte_carlo_input={"returns": [float("inf"), 0.2]},
            starting_bankroll=1_000.0,
            max_drawdown_limit=0.30,
        )


def test_non_list_upstream_limitations_are_not_forwarded() -> None:
    """A string in the payload's limitations slot must not leak as characters."""
    result = estimate_empirical_kelly(
        monte_carlo_input={"returns": [0.5, -0.3] * 10, "limitations": "contaminated"},
        starting_bankroll=10_000.0,
        max_drawdown_limit=0.30,
        seed=1,
    )
    assert result["limitations"][0].startswith("Kelly assumes returns are independent")
