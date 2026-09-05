"""HubBridge must persist the answer, not the narration that preceded it.

Reasoning models emit interim assistant messages labelled ``commentary``
alongside their tool calls. The CLI drops them through ``AnswerAccumulator``;
the bridge appended every delta, so the saved conversation and everything
replayed from it carried a status line glued to the front of the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
from agents.items import MessageOutputItem
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import (
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)

from clients.web.hub_bridge import HubBridge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents import Session


def _delta(item_id: str, text: str) -> RawResponsesStreamEvent:
    """One streamed text chunk belonging to ``item_id``."""
    return RawResponsesStreamEvent(
        data=ResponseTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id=item_id,
            logprobs=[],
            output_index=0,
            sequence_number=0,
            type="response.output_text.delta",
        )
    )


class _StubAgent:
    """MessageOutputItem weak-references its agent, so it needs a real object."""

    name = "central_hub"


def _message(
    item_id: str, text: str, phase: Literal["commentary", "final_answer"] | None
) -> RunItemStreamEvent:
    """A completed assistant message carrying its phase label."""
    raw = ResponseOutputMessage(
        id=item_id,
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
        phase=phase,
    )
    return RunItemStreamEvent(
        name="message_output_created",
        item=MessageOutputItem(agent=cast(Any, _StubAgent()), raw_item=raw),
    )


class _ScriptedHub:
    """Streams a fixed event sequence so assembly can be checked in isolation."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run(self, text: str, session: Any) -> AsyncIterator[Any]:
        for event in self._events:
            yield event

    async def close(self) -> None:
        return None


async def _collect(events: list[Any]) -> str:
    """Run the bridge over ``events`` and return the persisted response text."""
    bridge = HubBridge(cast(Any, _ScriptedHub(events)))
    response = ""
    async for evt in bridge.run_query("q", cast("Session", None)):
        if evt.get("type") == "complete":
            response = evt["response_text"]
    return response


@pytest.mark.asyncio
async def test_commentary_is_not_persisted_with_the_answer() -> None:
    """A commentary message before the answer must not survive into storage."""
    events = [
        _delta("msg_1", "Checking the latest filings before concluding."),
        _message("msg_1", "Checking the latest filings before concluding.", "commentary"),
        _delta("msg_2", "AAPL closed at $210.00."),
        _message("msg_2", "AAPL closed at $210.00.", "final_answer"),
    ]

    assert await _collect(events) == "AAPL closed at $210.00."


@pytest.mark.asyncio
async def test_answer_without_a_phase_label_is_kept() -> None:
    """Models that never label phases must still produce an answer."""
    events = [
        _delta("msg_1", "AAPL closed at $210.00."),
        _message("msg_1", "AAPL closed at $210.00.", None),
    ]

    assert await _collect(events) == "AAPL closed at $210.00."


@pytest.mark.asyncio
async def test_commentary_after_the_answer_is_still_dropped() -> None:
    """Commentary arriving last is exactly the case a running append misses."""
    events = [
        _delta("msg_1", "AAPL closed at $210.00."),
        _message("msg_1", "AAPL closed at $210.00.", "final_answer"),
        _delta("msg_2", "Verifying that against the intraday feed."),
        _message("msg_2", "Verifying that against the intraday feed.", "commentary"),
    ]

    assert await _collect(events) == "AAPL closed at $210.00."
