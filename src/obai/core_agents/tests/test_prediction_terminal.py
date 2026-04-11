"""Tests for prediction market terminal output and session context.

Covers: SessionContextStore, prediction context extraction/formatting,
and relay validation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from core_agents.prediction_context import (
    extract_prediction_context,
    format_context_for_hub,
    validate_prediction_relay,
)
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


def _market_output(
    *,
    condition_id: str = "0xabc123def456",
    slug: str = "will-btc-hit-100k",
    question: str = "Will BTC hit $100k?",
    market_url: str = "https://polymarket.com/event/will-btc-hit-100k",
    outcomes: list[str] | None = None,
    outcome_prices: list[float] | None = None,
    clob_token_ids: list[str] | None = None,
    end_date: str = "2026-12-31T00:00:00Z",
    volume_24h: float = 50000.0,
    liquidity: float = 120000.0,
    best_bid: float = 0.55,
    best_ask: float = 0.57,
    spread: float = 0.02,
) -> dict[str, Any]:
    """Build a realistic raw market dict."""
    return {
        "condition_id": condition_id,
        "slug": slug,
        "question": question,
        "market_url": market_url,
        "outcomes": outcomes or ["YES", "NO"],
        "outcome_prices": outcome_prices or [0.56, 0.44],
        "clob_token_ids": clob_token_ids or ["tok_yes_1", "tok_no_1"],
        "end_date": end_date,
        "volume_24h": volume_24h,
        "liquidity": liquidity,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
    }


def _inner_output(
    tool_name: str,
    output: Any,
    specialist: str = "Prediction Markets Agent",
) -> dict[str, Any]:
    """Build an _inner_tool_outputs entry."""
    return {"specialist": specialist, "tool_name": tool_name, "output": output}


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


class TestPredictionContextPersistence:
    """Persist captured prediction market context from a completed hub turn."""

    @pytest.mark.asyncio()
    async def test_hub_persists_context_before_passthrough_is_consumed(
        self,
        store: SessionContextStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Context is saved before clients can stop after passthrough text."""
        from core_agents.central_hub_agent import (
            CentralHubAgent,
            PredictionPassthroughEvent,
            _inner_tool_outputs,
            _set_prediction_passthrough,
        )
        from core_agents.config import reset_config

        class FakeSession:
            session_id = "sess_prediction"

        class FakeRunResult:
            async def stream_events(self) -> AsyncIterator[object]:
                _inner_tool_outputs.append(
                    _inner_output(
                        "search_prediction_markets",
                        {"markets": [_market_output()]},
                    )
                )
                _set_prediction_passthrough(
                    "Market: https://polymarket.com/event/will-btc-hit-100k"
                )
                yield object()

        def fake_run_streamed(*_args: object, **_kwargs: object) -> FakeRunResult:
            return FakeRunResult()

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        reset_config()
        monkeypatch.setattr(
            "core_agents.central_hub_agent.get_context_store",
            lambda: store,
        )
        monkeypatch.setattr(
            "core_agents.central_hub_agent.Runner.run_streamed",
            fake_run_streamed,
        )

        hub = CentralHubAgent()
        hub.agent = object()  # type: ignore[assignment]
        hub._initialized = True

        try:
            async for event in hub.run("follow up", FakeSession()):  # type: ignore[arg-type]
                if isinstance(event, PredictionPassthroughEvent):
                    results = await store.read_context(
                        "sess_prediction",
                        "prediction_market",
                    )
                    assert len(results) == 1
                    assert results[0]["markets"][0]["slug"] == "will-btc-hit-100k"
                    break
            else:
                pytest.fail("hub did not emit prediction passthrough")
        finally:
            _inner_tool_outputs.clear()
            reset_config()

    @pytest.mark.asyncio()
    async def test_persist_prediction_context_writes_captured_markets(
        self,
        store: SessionContextStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Captured prediction tool output is saved under the exact session_id."""
        from core_agents.central_hub_agent import (
            _inner_tool_outputs,
            _persist_prediction_context,
        )

        monkeypatch.setattr(
            "core_agents.central_hub_agent.get_context_store",
            lambda: store,
        )
        _inner_tool_outputs.clear()
        _inner_tool_outputs.append(
            _inner_output(
                "search_prediction_markets",
                {"markets": [_market_output()]},
            )
        )

        try:
            await _persist_prediction_context(
                prediction_fired=True,
                session_id="sess_prediction",
            )

            results = await store.read_context("sess_prediction", "prediction_market")
            assert len(results) == 1
            assert results[0]["markets"][0]["slug"] == "will-btc-hit-100k"
        finally:
            _inner_tool_outputs.clear()

    @pytest.mark.asyncio()
    async def test_persist_prediction_context_skips_when_prediction_not_used(
        self,
        store: SessionContextStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-prediction turns do not write prediction context."""
        from core_agents.central_hub_agent import (
            _inner_tool_outputs,
            _persist_prediction_context,
        )

        monkeypatch.setattr(
            "core_agents.central_hub_agent.get_context_store",
            lambda: store,
        )
        _inner_tool_outputs.clear()
        _inner_tool_outputs.append(
            _inner_output(
                "search_prediction_markets",
                {"markets": [_market_output()]},
            )
        )

        try:
            await _persist_prediction_context(
                prediction_fired=False,
                session_id="sess_prediction",
            )

            await store.initialize()
            results = await store.read_context("sess_prediction", "prediction_market")
            assert results == []
        finally:
            _inner_tool_outputs.clear()


# ---------------------------------------------------------------------------
# Prediction Context Extraction
# ---------------------------------------------------------------------------


class TestPredictionContextExtraction:
    """Extract structured context from _inner_tool_outputs."""

    def test_extract_from_search_markets(self) -> None:
        """search_prediction_markets → markets list."""
        outputs = [
            _inner_output(
                "search_prediction_markets",
                {"markets": [_market_output(), _market_output(condition_id="0xdef789")]},
            ),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert len(ctx["markets"]) == 2
        assert ctx["markets"][0]["slug"] == "will-btc-hit-100k"
        assert ctx["venue"] == "polymarket"

    def test_extract_from_mcp_wrapper_tool_names(self) -> None:
        """FastMCP wrapper names ending in _tool are recognized."""
        outputs = [
            _inner_output(
                "explore_trending_markets_tool",
                {
                    "events": [
                        {
                            "title": "Trending",
                            "markets": [
                                _market_output(condition_id="0x111"),
                            ],
                        },
                    ],
                },
            ),
            _inner_output(
                "get_market_snapshot_tool",
                _market_output(condition_id="0x222", slug="snapshot-market"),
            ),
            _inner_output(
                "get_market_details_tool",
                _market_output(condition_id="0x333", slug="details-market"),
            ),
        ]

        ctx = extract_prediction_context(outputs)

        assert ctx is not None
        assert {m["condition_id"] for m in ctx["markets"]} == {"0x111", "0x222", "0x333"}

    def test_extract_from_get_market_details(self) -> None:
        """get_market_details → single market with clob_token_ids."""
        market = _market_output(clob_token_ids=["tok_yes", "tok_no"])
        outputs = [_inner_output("get_market_details", market)]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert len(ctx["markets"]) == 1
        assert ctx["markets"][0]["token_ids"] == {"YES": "tok_yes", "NO": "tok_no"}

    def test_extract_from_snapshot_outcome_books(self) -> None:
        """get_market_snapshot → token_ids from outcome_books."""
        market = _market_output()
        del market["clob_token_ids"]
        market["outcome_books"] = [
            {"outcome": "YES", "token_id": "snap_yes", "best_bid": 0.5, "best_ask": 0.52},
            {"outcome": "NO", "token_id": "snap_no", "best_bid": 0.48, "best_ask": 0.50},
        ]
        outputs = [_inner_output("get_market_snapshot", market)]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert ctx["markets"][0]["token_ids"] == {"YES": "snap_yes", "NO": "snap_no"}

    def test_extract_from_trending_events(self) -> None:
        """explore_trending_markets → markets nested under events."""
        outputs = [
            _inner_output(
                "explore_trending_markets",
                {
                    "events": [
                        {
                            "title": "BTC Markets",
                            "markets": [
                                _market_output(),
                                _market_output(condition_id="0x222"),
                            ],
                        },
                    ],
                },
            ),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert len(ctx["markets"]) == 2

    def test_deduplicates_by_condition_id(self) -> None:
        """Same condition_id from two tools → one entry."""
        cid = "0xduplicate123456789012345678901234567890ab"
        search_out = {"markets": [_market_output(condition_id=cid)]}
        outputs = [
            _inner_output("search_prediction_markets", search_out),
            _inner_output("get_market_details", _market_output(condition_id=cid)),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert len(ctx["markets"]) == 1

    def test_omits_missing_fields(self) -> None:
        """Fields absent from tool output are not in the normalized market."""
        sparse = {"condition_id": "0xsparse1234567890", "question": "Will X?"}
        outputs = [_inner_output("get_market_details", sparse)]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        market = ctx["markets"][0]
        assert "slug" not in market
        assert "market_url" not in market
        assert "token_ids" not in market

    def test_ignores_non_prediction_tools(self) -> None:
        """Outputs from other specialists are skipped."""
        outputs = [
            _inner_output("get_quote", {"price": 150.0}, specialist="Market Data Agent"),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_returns_none_when_no_prediction_data(self) -> None:
        """No prediction tool outputs → None."""
        ctx = extract_prediction_context([])
        assert ctx is None

    def test_handles_malformed_json_string(self) -> None:
        """Non-parseable output string is skipped gracefully."""
        outputs = [_inner_output("get_market_details", "not valid json {{{")]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_skips_analytics_tools(self) -> None:
        """Flow/holder/wallet tools don't produce market context."""
        outputs = [
            _inner_output("get_trade_flow", {"condition_id": "0xflow", "trades": []}),
            _inner_output("get_top_holders", {"condition_id": "0xhold", "holders": []}),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_empty_string_condition_id_skipped(self) -> None:
        """Market with empty condition_id is silently dropped."""
        market = _market_output(condition_id="")
        outputs = [_inner_output("get_market_details", market)]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_none_output_skipped(self) -> None:
        """None as tool output is gracefully skipped."""
        outputs = [_inner_output("get_market_details", None)]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_error_dict_skipped(self) -> None:
        """Error response from tool has no condition_id, skipped."""
        outputs = [_inner_output("get_market_details", {"error": "not found", "code": 404})]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_list_output_skipped(self) -> None:
        """JSON list output (not dict) is silently dropped."""
        outputs = [_inner_output("get_market_details", [{"condition_id": "0xabc"}])]
        ctx = extract_prediction_context(outputs)
        assert ctx is None

    def test_prefers_richer_entry_on_dedup(self) -> None:
        """When same condition_id appears twice, keep entry with more fields."""
        sparse = {"condition_id": "0xrich1234567890"}
        rich = _market_output(condition_id="0xrich1234567890")
        outputs = [
            _inner_output("search_prediction_markets", {"markets": [sparse]}),
            _inner_output("get_market_details", rich),
        ]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        assert len(ctx["markets"]) == 1
        assert "slug" in ctx["markets"][0]  # rich entry has slug

    def test_extracts_accepting_orders_and_neg_risk(self) -> None:
        """Trading-critical fields are captured when present."""
        market = _market_output()
        market["accepting_orders"] = False
        market["neg_risk"] = True
        outputs = [_inner_output("get_market_details", market)]
        ctx = extract_prediction_context(outputs)
        assert ctx is not None
        m = ctx["markets"][0]
        assert m["accepting_orders"] is False
        assert m["neg_risk"] is True


# ---------------------------------------------------------------------------
# Prediction Context Formatting
# ---------------------------------------------------------------------------


class TestPredictionContextFormatting:
    """Format context payloads for hub injection."""

    def test_format_block_structure(self) -> None:
        """Output starts with header and includes instructions."""
        contexts = [
            {
                "markets": [
                    {
                        "question": "Will BTC hit $100k?",
                        "slug": "will-btc-hit-100k",
                        "market_url": "https://polymarket.com/event/will-btc-hit-100k",
                        "condition_id": "0xabc123",
                        "token_ids": {"YES": "tok_1", "NO": "tok_2"},
                        "last_known_prices": {"context_saved_at": "2026-04-11T15:30:00Z"},
                    },
                ],
            },
        ]
        text = format_context_for_hub(contexts)
        assert text.startswith("## Prior Prediction Market Context")
        assert "disambiguate follow-ups" in text
        assert "Refresh all prices" in text

    def test_format_includes_identifiers(self) -> None:
        """All identifier fields present in output."""
        contexts = [
            {
                "markets": [
                    {
                        "question": "Will ETH merge?",
                        "slug": "eth-merge",
                        "market_url": "https://polymarket.com/event/eth-merge",
                        "condition_id": "0xeth999",
                        "token_ids": {"YES": "t1", "NO": "t2"},
                        "last_known_prices": {"context_saved_at": "2026-01-01T00:00:00Z"},
                    },
                ],
            },
        ]
        text = format_context_for_hub(contexts)
        assert "eth-merge" in text
        assert "0xeth999" in text
        assert "YES=t1" in text
        assert "NO=t2" in text
        assert "https://polymarket.com/event/eth-merge" in text

    def test_format_empty_contexts(self) -> None:
        """Empty list → empty string."""
        assert format_context_for_hub([]) == ""

    def test_format_caps_at_eight_markets(self) -> None:
        """At most 8 markets rendered."""
        markets = [
            {
                "question": f"Q{i}",
                "condition_id": f"0x{i:040x}",
                "last_known_prices": {"context_saved_at": "2026-01-01"},
            }
            for i in range(10)
        ]
        text = format_context_for_hub([{"markets": markets}])
        assert text.count("- Market ") == 8

    def test_format_renders_trading_critical_fields(self) -> None:
        """end_date, neg_risk, accepting_orders appear in output."""
        contexts = [
            {
                "markets": [
                    {
                        "question": "Q",
                        "condition_id": "0xtest",
                        "end_date": "2026-12-31T00:00:00Z",
                        "neg_risk": True,
                        "accepting_orders": False,
                    },
                ],
            },
        ]
        text = format_context_for_hub(contexts)
        assert "end_date: 2026-12-31" in text
        assert "neg_risk: true" in text
        assert "accepting_orders: false" in text

    def test_format_deduplicates_across_contexts(self) -> None:
        """Same condition_id in multiple context entries → one market."""
        ctx_a = {"markets": [{"question": "Q", "condition_id": "0xsame"}]}
        ctx_b = {"markets": [{"question": "Q", "condition_id": "0xsame"}]}
        text = format_context_for_hub([ctx_a, ctx_b])
        assert text.count("- Market ") == 1


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

    def test_passes_when_identifiers_preserved(self) -> None:
        """Hub reformats but keeps all URLs and condition_ids."""
        passthrough = (
            "# Market Analysis\n"
            "URL: https://polymarket.com/event/btc-100k\n"
            "Condition: 0xabc123def456789012345678901234567890abcd\n"
        )
        hub_text = (
            "**Market Analysis**\n"
            "Link: https://polymarket.com/event/btc-100k\n"
            "ID: 0xabc123def456789012345678901234567890abcd\n"
        )
        assert validate_prediction_relay(hub_text, passthrough) is True

    def test_fails_when_url_dropped(self) -> None:
        """Hub drops a Polymarket URL → fail."""
        passthrough = "Market: https://polymarket.com/event/btc-100k\nGood market to watch."
        hub_text = "Here is a good market to watch for BTC."
        assert validate_prediction_relay(hub_text, passthrough) is False

    def test_fails_when_slug_dropped(self) -> None:
        """Hub drops an explicit slug → fail."""
        passthrough = "Market slug: will-btc-hit-100k\nDecision: No trade"
        hub_text = "Decision: No trade"
        assert validate_prediction_relay(hub_text, passthrough) is False

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

    def test_fails_when_hub_invents_explicit_slug(self) -> None:
        """Hub must not add explicit slug fields absent from specialist output."""
        passthrough = "No relevant active market was found."
        hub_text = "No relevant active market was found.\nslug: fake-slug"
        assert validate_prediction_relay(hub_text, passthrough) is False

    def test_fails_when_condition_id_dropped(self) -> None:
        """Hub drops a condition_id → fail."""
        passthrough = (
            "Condition: 0xabc123def456789012345678901234567890abcd\n"
            "https://polymarket.com/event/test\n"
        )
        hub_text = "Check out https://polymarket.com/event/test for details."
        assert validate_prediction_relay(hub_text, passthrough) is False

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
