"""The CLI relays the options-strategy terminal answer and preflights its server.

`obai query` is what the E2E gate drives, so its `_run_query` must return the
Options Strategy Agent's passthrough verbatim rather than hub-authored text,
and `obai status` must check the options-backtest server so the paid gate
refuses to start while it is down (ADR 0003 §2.8, §4.4).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from agents.items import MessageOutputItem
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)
from typer.testing import CliRunner

from clients.cli.chat import _run_query, cli
from core_agents.central_hub_agent import OptionsStrategyPassthroughEvent
from core_agents.config import get_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

runner = CliRunner()


class _StubAgent:
    """MessageOutputItem weak-references its agent, so it needs a real object."""

    name = "central_hub"


class _ScriptedHub:
    """Streams a fixed event sequence in place of a live hub."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run(self, text: str, session: Any) -> AsyncIterator[Any]:
        for event in self._events:
            yield event


def _hub_answer(text: str) -> list[Any]:
    """The events of one hub-authored final answer."""
    delta = RawResponsesStreamEvent(
        data=ResponseTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id="msg_1",
            logprobs=[],
            output_index=0,
            sequence_number=0,
            type="response.output_text.delta",
        )
    )
    raw = ResponseOutputMessage(
        id="msg_1",
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
        phase="final_answer",
    )
    message = RunItemStreamEvent(
        name="message_output_created",
        item=MessageOutputItem(agent=cast(Any, _StubAgent()), raw_item=raw),
    )
    return [delta, message]


@pytest.mark.asyncio
async def test_run_query_returns_the_options_strategy_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The specialist's text is the response; the hub's own answer is dropped."""
    monkeypatch.setattr(get_config(), "enable_inline_scoring", False)
    specialist = "Status: unavailable\nReference: strategy schema, engine and product rules"
    hub = _ScriptedHub(
        [
            *_hub_answer("Hub-authored text the relay must discard."),
            OptionsStrategyPassthroughEvent(content=specialist),
        ]
    )

    result = await _run_query("q", cast(Any, hub), cast(Any, None), "sid", json_mode=True)

    assert result["response"] == specialist


def test_status_checks_the_options_backtest_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """`obai status` lists the options-backtest server at its configured URL."""

    async def _reachable(client: Any, name: str, url: str) -> dict[str, Any]:
        return {"name": name, "url": url, "status": "ok", "latency_ms": 1}

    monkeypatch.setattr("clients.cli.chat._check_server", _reachable)

    result = runner.invoke(cli, ["status", "--json"])

    assert result.exit_code == 0, result.output
    servers = json.loads(result.output)["servers"]
    checked = {entry["name"]: entry["url"] for entry in servers}
    assert checked["Options Backtest"] == get_config().mcp_options_backtest_url
