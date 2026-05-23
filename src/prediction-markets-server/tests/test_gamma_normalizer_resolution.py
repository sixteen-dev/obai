"""Verify Gamma normalizer surfaces resolution-relevant fields."""

from __future__ import annotations

import pytest

from src.clients.gamma_client import GammaClient
from src.config import load_settings


@pytest.fixture(autouse=True)
def _settings() -> None:
    """Ensure config singleton is loaded so GammaClient can construct."""
    load_settings()


def test_normalizer_surfaces_uma_and_resolution_fields() -> None:
    """_normalize_market should expose umaResolutionStatus, resolvedBy, and winningOutcome."""
    client = GammaClient()
    raw = {
        "conditionId": "0xC",
        "slug": "x",
        "question": "Q?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["1.0", "0.0"],
        "umaResolutionStatus": "resolved",
        "resolvedBy": "0xResolver",
        "winningOutcome": "Yes",
    }
    norm = client._normalize_market(raw)
    assert norm["uma_resolution_status"] == "resolved"
    assert norm["resolved_by"] == "0xResolver"
    assert norm["winning_outcome"] == "Yes"


def test_normalizer_handles_missing_resolution_fields() -> None:
    """Missing fields must surface as None, not KeyError."""
    client = GammaClient()
    norm = client._normalize_market({"conditionId": "0xC", "slug": "x", "question": "Q?"})
    assert norm["uma_resolution_status"] is None
    assert norm["resolved_by"] is None
    assert norm["winning_outcome"] is None
