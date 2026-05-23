"""Tests for storage.resolution.infer_resolution — 5 cases per §8.1."""

from __future__ import annotations

from src.storage.resolution import infer_resolution


def test_explicit_api_winner_is_authoritative() -> None:
    """Rule 1: an explicit winning_outcome field always wins."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": "Yes",
            "outcomes": ["Yes", "No"],
            "outcome_prices": [0.5, 0.5],  # contradictory; should still pick Yes
        }
    )
    assert result.winning_outcome == "Yes"
    assert result.resolution_method == "explicit_api"
    assert result.resolution_confidence == 1.0
    assert result.resolution_status == "resolved"


def test_terminal_price_exact_one_zero() -> None:
    """Rule 4: exactly 1.0 / 0.0 terminal prices → terminal_price_exact, conf 0.99."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [1.0, 0.0],
        }
    )
    assert result.winning_outcome == "Yes"
    assert result.resolution_method == "terminal_price_exact"
    assert result.resolution_confidence == 0.99
    assert result.resolution_status == "resolved"


def test_terminal_price_threshold_near_terminal() -> None:
    """Rule 5: 0.995 / 0.005 falls within threshold but not exact → conf 0.90."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [0.995, 0.005],
        }
    )
    assert result.winning_outcome == "Yes"
    assert result.resolution_method == "terminal_price_threshold"
    assert result.resolution_confidence == 0.90
    assert result.resolution_status == "resolved"


def test_ambiguous_when_two_outcomes_above_threshold() -> None:
    """Rule 6 (ambiguous branch): more than one winner under the threshold."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["A", "B", "C"],
            "outcome_prices": [0.995, 0.995, 0.005],
        }
    )
    assert result.winning_outcome is None
    assert result.resolution_method == "ambiguous"
    assert result.resolution_status == "ambiguous"
    assert result.resolution_confidence == 0.0


def test_unresolved_when_not_closed() -> None:
    """Rule 2 fail-out: open markets are unresolved regardless of price."""
    result = infer_resolution(
        {
            "closed": False,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [1.0, 0.0],
        }
    )
    assert result.winning_outcome is None
    assert result.resolution_method == "unresolved"
    assert result.resolution_status == "unresolved"


def test_unresolved_when_uma_not_resolved() -> None:
    """Rule 2 fail-out: closed but UMA status not resolved → unresolved."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "disputed",
            "winning_outcome": None,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [1.0, 0.0],
        }
    )
    assert result.resolution_status == "unresolved"


def test_ambiguous_when_prices_unparseable() -> None:
    """Closed + UMA resolved but a None price → ambiguous (cannot classify)."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["Yes", "No"],
            "outcome_prices": [1.0, None],
        }
    )
    assert result.resolution_method == "ambiguous"
    assert result.resolution_status == "ambiguous"


def test_ambiguous_when_outcomes_and_prices_length_mismatch() -> None:
    """Misaligned outcomes vs prices → ambiguous, not crash."""
    result = infer_resolution(
        {
            "closed": True,
            "uma_resolution_status": "resolved",
            "winning_outcome": None,
            "outcomes": ["Yes", "No", "Maybe"],
            "outcome_prices": [1.0, 0.0],
        }
    )
    assert result.resolution_method == "ambiguous"
