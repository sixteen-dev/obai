"""Tests for data.normalizers (pure payload→row transformations)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.normalizers import (
    clob_history_to_price_rows,
    data_api_trades_to_rows,
    gamma_market_to_rows,
)


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


def test_gamma_market_to_rows_fills_resolution_via_inference() -> None:
    """Terminal 1.0/0.0 prices should flow into resolution_method=terminal_price_exact."""
    payload = {
        "condition_id": "0xabc",
        "slug": "x",
        "question": "Will X happen?",
        "clob_token_ids": ["tokYes", "tokNo"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
    }
    market, tokens = gamma_market_to_rows(payload, fetched_at=_now())
    assert market.condition_id == "0xabc"
    assert market.resolution_method == "terminal_price_exact"
    assert market.resolution_status == "resolved"
    assert market.winning_outcome == "Yes"
    assert market.resolution_confidence == 0.99
    assert market.end_date is not None
    assert market.end_date.tzinfo is not None
    assert len(tokens) == 2
    assert tokens[0].token_id == "tokYes"
    assert tokens[0].outcome_label == "Yes"
    assert tokens[1].outcome_index == 1


def test_gamma_market_raises_on_missing_condition_id() -> None:
    """Fail loud, not silent — design rule #6."""
    with pytest.raises(ValueError, match="condition_id"):
        gamma_market_to_rows({"question": "Q?"}, fetched_at=_now())


def test_gamma_market_drops_token_outcome_length_mismatch() -> None:
    """When token IDs and outcomes do not align, return zero tokens not garbage."""
    payload = {
        "condition_id": "0xabc",
        "question": "Q?",
        "clob_token_ids": ["a", "b", "c"],
        "outcomes": ["Yes", "No"],
    }
    _, tokens = gamma_market_to_rows(payload, fetched_at=_now())
    assert tokens == []


def test_clob_history_to_price_rows_drops_bad_points() -> None:
    """Missing timestamp or non-numeric price should be dropped, not coerced."""
    history = [
        {"timestamp": 1700000000, "price": 0.5},
        {"timestamp": None, "price": 0.6},  # bad ts
        {"timestamp": 1700001000, "price": "not_a_number"},  # bad price
        {"timestamp": 1700002000, "price": 0.7},
    ]
    rows = clob_history_to_price_rows(
        token_id="t1",
        condition_id="0xc",
        history=history,
        fidelity_minutes=60,
        source="clob_prices_history",
        fetched_at=_now(),
    )
    assert len(rows) == 2
    assert {r.price for r in rows} == {0.5, 0.7}


def test_data_api_trades_distinguishes_tx_hash_from_source_id() -> None:
    """0x-prefixed id → transaction_hash; otherwise → source_trade_id."""
    trades = [
        {"id": "0xdeadbeef", "side": "buy", "price": 0.5, "size": 10, "timestamp": 1700000000},
        {"id": "uuid-xyz", "side": "sell", "price": 0.6, "size": 5, "timestamp": 1700001000},
    ]
    rows = data_api_trades_to_rows(condition_id="0xc", trades=trades, fetched_at=_now())
    assert rows[0].transaction_hash == "0xdeadbeef"
    assert rows[0].source_trade_id is None
    assert rows[1].transaction_hash is None
    assert rows[1].source_trade_id == "uuid-xyz"
