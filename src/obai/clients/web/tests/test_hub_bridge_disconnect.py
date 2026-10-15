"""Abandoning a bridge query must close the hub run before the lock frees.

A browser refresh drops the WebSocket mid-answer. The next query must not
start while the previous hub run is still streaming.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from agents.stream_events import RawResponsesStreamEvent
from openai.types.responses import ResponseTextDeltaEvent

from clients.web.hub_bridge import HubBridge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents import Session


class _EndlessHub:
    """Streams text forever and records when its run is closed."""

    def __init__(self) -> None:
        self.closed = False

    async def run(self, text: str, session: Any) -> AsyncIterator[Any]:
        try:
            while True:
                yield RawResponsesStreamEvent(
                    data=ResponseTextDeltaEvent(
                        content_index=0,
                        delta="chunk",
                        item_id="msg_1",
                        logprobs=[],
                        output_index=0,
                        sequence_number=0,
                        type="response.output_text.delta",
                    )
                )
        finally:
            self.closed = True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_closing_a_query_closes_the_hub_run() -> None:
    """The hub run is closed by the time the abandoned query releases the lock."""
    hub = _EndlessHub()
    bridge = HubBridge(cast(Any, hub))
    stream = bridge.run_query("q", cast("Session", None))

    async for event in stream:
        if event.get("type") == "text_delta":
            break
    await stream.aclose()

    assert hub.closed
    assert not bridge._lock.locked()
