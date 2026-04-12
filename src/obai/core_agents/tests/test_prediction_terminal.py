"""Tests for prediction market terminal output and session context.

Covers: SessionContextStore, prediction context extraction/formatting,
and relay validation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from core_agents.prediction_context import validate_prediction_relay
from core_agents.session_context import SessionContextStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[SessionContextStore]:
    """Create a store backed by a temporary DB, close after test."""
    s = SessionContextStore(db_path=tmp_path / "test_app_state.db")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# SessionContextStore
# ---------------------------------------------------------------------------


class TestSessionContextStore:
    """Session context store: write, read, isolation, ordering."""

    @pytest.mark.asyncio()
    async def test_write_and_read(self, store: SessionContextStore) -> None:
        """Write a context entry, read it back."""
        await store.initialize()
        payload = {"type": "test", "value": 42}
        await store.write_context("sess_1", "prediction_market", payload)

        results = await store.read_context("sess_1", "prediction_market")
        assert len(results) == 1
        assert results[0]["type"] == "test"
        assert results[0]["value"] == 42

    @pytest.mark.asyncio()
    async def test_session_isolation(self, store: SessionContextStore) -> None:
        """Read never returns context from another session."""
        await store.initialize()
        await store.write_context("sess_A", "prediction_market", {"market": "A"})
        await store.write_context("sess_B", "prediction_market", {"market": "B"})

        results_a = await store.read_context("sess_A", "prediction_market")
        assert len(results_a) == 1
        assert results_a[0]["market"] == "A"

        results_b = await store.read_context("sess_B", "prediction_market")
        assert len(results_b) == 1
        assert results_b[0]["market"] == "B"

    @pytest.mark.asyncio()
    async def test_no_cross_session_leakage(self, store: SessionContextStore) -> None:
        """Reading from a session with no entries returns empty list."""
        await store.initialize()
        await store.write_context("sess_A", "prediction_market", {"data": 1})

        results = await store.read_context("sess_NONE", "prediction_market")
        assert results == []

    @pytest.mark.asyncio()
    async def test_ordering_newest_first(self, store: SessionContextStore) -> None:
        """Results ordered by created_at DESC."""
        await store.initialize()
        await store.write_context("s1", "prediction_market", {"turn": 1})
        await store.write_context("s1", "prediction_market", {"turn": 2})
        await store.write_context("s1", "prediction_market", {"turn": 3})

        results = await store.read_context("s1", "prediction_market")
        turns = [r["turn"] for r in results]
        assert turns == [3, 2, 1]

    @pytest.mark.asyncio()
    async def test_limit(self, store: SessionContextStore) -> None:
        """Limit caps the number of returned entries."""
        await store.initialize()
        for i in range(10):
            await store.write_context("s1", "prediction_market", {"i": i})

        results = await store.read_context("s1", "prediction_market", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio()
    async def test_clear_session(self, store: SessionContextStore) -> None:
        """Clear removes all entries for a session."""
        await store.initialize()
        await store.write_context("s1", "prediction_market", {"a": 1})
        await store.write_context("s1", "prediction_market", {"b": 2})

        deleted = await store.clear_session("s1")
        assert deleted == 2

        results = await store.read_context("s1", "prediction_market")
        assert results == []

    @pytest.mark.asyncio()
    async def test_initialize_idempotent(self, store: SessionContextStore) -> None:
        """Calling initialize() twice does not raise."""
        await store.initialize()
        await store.initialize()
        await store.write_context("s1", "t", {"ok": True})
        results = await store.read_context("s1", "t")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Relay Validation
# ---------------------------------------------------------------------------


class TestPredictionRelayValidation:
    """Validate that hub relay preserved prediction output."""

    def test_passes_when_verbatim(self) -> None:
        """Hub text contains exact passthrough."""
        passthrough = "Analysis:\nhttps://polymarket.com/event/btc-100k\nSlug: btc-100k"
        hub_text = f"Here is the analysis:\n{passthrough}\n"
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_when_machine_id_hidden_but_url_preserved(self) -> None:
        """Hub can hide condition_id when the market URL is preserved."""
        passthrough = (
            "# Market Analysis\n"
            "URL: https://polymarket.com/event/btc-100k\n"
            "Condition: 0xabc123def456789012345678901234567890abcd\n"
        )
        hub_text = (
            "**Market Analysis**\n"
            "Link: https://polymarket.com/event/btc-100k\n"
            "The market is liquid and worth tracking.\n"
        )
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_when_url_omitted_for_focus(self) -> None:
        """Hub may omit URLs when focusing on a subset of markets."""
        passthrough = "Market: https://polymarket.com/event/btc-100k\nGood market to watch."
        hub_text = "Here is a good market to watch for BTC."
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_when_slug_omitted_for_focus(self) -> None:
        """Hub may omit slugs when focusing on a subset of markets."""
        passthrough = "Market slug: will-btc-hit-100k\nDecision: No trade"
        hub_text = "Decision: No trade"
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_when_slug_preserved_by_url(self) -> None:
        """A preserved market URL also preserves its URL slug."""
        passthrough = "Market: https://polymarket.com/event/will-btc-hit-100k"
        hub_text = "See https://polymarket.com/event/will-btc-hit-100k."
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_fails_when_hub_invents_polymarket_url(self) -> None:
        """Hub must not add a Polymarket URL absent from specialist output."""
        passthrough = "No relevant active market was found."
        hub_text = "No relevant active market was found: https://polymarket.com/event/fake-slug"
        assert validate_prediction_relay(hub_text, passthrough) is False

    def test_passes_when_extra_url_came_from_prior_context(self) -> None:
        """Prior context URLs are allowed in hub output."""
        passthrough = "Current analysis: https://polymarket.com/event/current-market"
        hub_text = (
            "Current analysis: https://polymarket.com/event/current-market\n"
            "Prior candidate: https://polymarket.com/event/prior-market"
        )
        allowed_context = (
            "## Prior Prediction Market Context\n"
            "- Market 1:\n"
            "  - slug: prior-market\n"
            "  - market_url: https://polymarket.com/event/prior-market\n"
        )
        assert (
            validate_prediction_relay(
                hub_text,
                passthrough,
                allowed_context=allowed_context,
            )
            is True
        )

    def test_fails_when_hub_invents_explicit_slug(self) -> None:
        """Hub must not add explicit slug fields absent from specialist output."""
        passthrough = "No relevant active market was found."
        hub_text = "No relevant active market was found.\nslug: fake-slug"
        assert validate_prediction_relay(hub_text, passthrough) is False

    def test_passes_when_condition_id_omitted_for_focus(self) -> None:
        """Hub may omit condition_ids when focusing on a subset."""
        passthrough = (
            "Condition: 0xabc123def456789012345678901234567890abcd\nNo market URL available.\n"
        )
        hub_text = "No market URL available."
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_fails_when_empty_hub_text(self) -> None:
        """Empty hub text → fail."""
        assert validate_prediction_relay("", "some output") is False
        assert validate_prediction_relay("  \n  ", "some output") is False

    def test_passes_no_identifiers_high_overlap(self) -> None:
        """No URLs or condition_ids but high line overlap → pass."""
        passthrough = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        hub_text = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nExtra"
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_no_identifiers_any_text(self) -> None:
        """No URLs or condition_ids → hub can freely rephrase."""
        passthrough = "Alpha\nBeta\nGamma\nDelta\nEpsilon"
        hub_text = "Something completely different here."
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_terminal_marker_stripped(self) -> None:
        """__TERMINAL_TOOL_OUTPUT__ prefix doesn't interfere."""
        raw = "https://polymarket.com/event/test-market\nAnalysis here."
        passthrough = f"__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:\n\n{raw}"
        hub_text = "https://polymarket.com/event/test-market\nAnalysis here."
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_passes_json_output(self) -> None:
        """Structured JSON-like passthrough with identifiers."""
        passthrough = json.dumps(
            {
                "market_url": "https://polymarket.com/event/election-2026",
                "condition_id": "0x" + "a" * 40,
                "analysis": "Strong buy signal",
            }
        )
        hub_text = passthrough  # verbatim
        assert validate_prediction_relay(hub_text, passthrough) is True
