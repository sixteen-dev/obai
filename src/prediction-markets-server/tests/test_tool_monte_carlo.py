"""Tests for tools.monte_carlo_risk.monte_carlo_prediction_risk."""

from __future__ import annotations

import pytest

from src.tools.monte_carlo_risk import IID_LIMITATION, monte_carlo_prediction_risk


def test_iid_limitation_present_in_response() -> None:
    """§13.2 requires the IID limitation text in every response."""
    result = monte_carlo_prediction_risk(
        returns=[0.1, -0.1, 0.2, -0.2],
        num_paths=50,
        seed=1,
    )
    assert IID_LIMITATION in result["limitations"]
    assert "iid_monte_carlo_assumption" in result["quality_flags"]


def test_response_shape_includes_metrics() -> None:
    """Top-level fields must include metrics, limitations, quality_flags."""
    result = monte_carlo_prediction_risk(
        returns=[0.1, -0.1],
        num_paths=10,
        seed=1,
    )
    for key in ("tool", "filters", "sample_size", "metrics", "limitations", "quality_flags"):
        assert key in result
    assert result["metrics"]["sampling_method"] == "iid_bootstrap"


def test_response_is_reproducible() -> None:
    """Same call twice ⇒ identical metrics."""
    a = monte_carlo_prediction_risk(returns=[0.1, -0.1, 0.2], num_paths=50, seed=42)
    b = monte_carlo_prediction_risk(returns=[0.1, -0.1, 0.2], num_paths=50, seed=42)
    assert a["metrics"] == b["metrics"]


def test_rejects_both_input_sources() -> None:
    with pytest.raises(ValueError, match="not both"):
        monte_carlo_prediction_risk(
            monte_carlo_input={"returns": [0.1]},
            returns=[0.2],
            num_paths=10,
            seed=1,
        )


def test_rejects_neither_input_source() -> None:
    with pytest.raises(ValueError, match="Provide either"):
        monte_carlo_prediction_risk(num_paths=10, seed=1)


def test_accepts_monte_carlo_input_dict() -> None:
    """The compact §10.5 payload should drive the same simulation."""
    mc_input = {
        "return_type": "return_on_cost",
        "returns": [0.5, -0.5, 0.5],
        "seed": 7,
        "source_backtest_fingerprint": "abcd",
        "condition_ids": ["0xA", "0xB", "0xC"],
    }
    result = monte_carlo_prediction_risk(monte_carlo_input=mc_input, num_paths=20, seed=7)
    assert result["sample_size"] == 3
    assert result["source_backtest_fingerprint"] == "abcd"
    assert result["source_market_count"] == 3


def test_upstream_limitations_are_forwarded_from_monte_carlo_input() -> None:
    """§10.5 limitations ride the payload downstream instead of being dropped."""
    contamination = (
        "min_lifetime_volume filters on lifetime volume, which is only known "
        "after resolution — surviving markets are contaminated."
    )
    mc_input = {
        "returns": [0.5, -0.5, 0.5],
        "limitations": [contamination],
    }
    result = monte_carlo_prediction_risk(monte_carlo_input=mc_input, num_paths=20, seed=7)
    assert contamination in result["limitations"]
    assert result["limitations"][0] == contamination  # upstream text leads
    assert IID_LIMITATION in result["limitations"]


def test_inline_returns_carry_no_upstream_limitations() -> None:
    """Inline returns have no upstream payload, so only the tool's own text appears."""
    result = monte_carlo_prediction_risk(returns=[0.1, -0.1], num_paths=10, seed=1)
    assert result["limitations"][0] == IID_LIMITATION


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_returns(bad: float) -> None:
    with pytest.raises(ValueError, match=r"returns\[1\]"):
        monte_carlo_prediction_risk(returns=[0.1, bad, 0.2], num_paths=10, seed=1)


def test_rejects_return_below_total_loss() -> None:
    with pytest.raises(ValueError, match=r"returns\[2\]"):
        monte_carlo_prediction_risk(returns=[0.1, -1.0, -1.5], num_paths=10, seed=1)


def test_rejects_non_finite_returns_from_monte_carlo_input() -> None:
    with pytest.raises(ValueError, match=r"returns\[0\]"):
        monte_carlo_prediction_risk(
            monte_carlo_input={"returns": [float("nan"), 0.2]}, num_paths=10, seed=1
        )


def test_non_list_upstream_limitations_are_not_forwarded() -> None:
    """A string or dict in the payload's limitations slot must not leak as characters/keys."""
    mc_input = {"returns": [0.5, -0.5, 0.5], "limitations": "contaminated"}
    result = monte_carlo_prediction_risk(monte_carlo_input=mc_input, num_paths=20, seed=7)
    assert result["limitations"][0] == IID_LIMITATION
