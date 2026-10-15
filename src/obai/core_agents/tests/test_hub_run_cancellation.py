"""A hub run must not outlive the caller that stops reading it.

The web UI abandons the stream when the browser disconnects. If the SDK run
kept going, the next query would hit an agent that is still busy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Runner
from agents.stream_events import RawResponsesStreamEvent
from openai.types.responses import ResponseTextDeltaEvent

from core_agents.central_hub_agent import CentralHubAgent


def _delta(text: str) -> RawResponsesStreamEvent:
    """One streamed text chunk."""
    return RawResponsesStreamEvent(
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


class _EndlessRun:
    """Streamed run that never finishes on its own and records cancellation."""

    def __init__(self) -> None:
        self.cancelled = False
        self.is_complete = False

    def cancel(self, mode: str = "immediate") -> None:
        self.cancelled = True

    async def stream_events(self) -> AsyncIterator[Any]:
        while True:
            yield _delta("chunk")


def _hub() -> CentralHubAgent:
    """A hub with no specialists, cache or MCP connections."""
    hub = object.__new__(CentralHubAgent)
    hub.agent = SimpleNamespace(name="test_hub")  # type: ignore[assignment]
    hub._initialized = True
    hub._cache = None
    hub._run_config = None
    return hub


@pytest.mark.asyncio
async def test_closing_the_stream_cancels_the_sdk_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing ``run()`` early must cancel the underlying SDK run."""
    run = _EndlessRun()
    monkeypatch.setattr(Runner, "run_streamed", lambda **_kwargs: run)

    stream = _hub().run("q")
    await anext(stream)
    await stream.aclose()

    assert run.cancelled
