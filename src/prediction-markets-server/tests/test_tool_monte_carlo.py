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
