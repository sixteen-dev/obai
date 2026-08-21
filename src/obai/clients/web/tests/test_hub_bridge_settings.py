"""HubBridge's settings hand-off: apply now, or queue behind a live query.

``run_query`` holds the bridge lock for a whole streamed answer, which can run
for minutes. The settings PATCH must never wait on that lock — a save that
hangs until the answer finishes is the restart-in-a-terminal problem wearing a
different hat. These tests pin both halves: the immediate path when the hub is
idle, and the queued path plus its drain when it is not.

Every wait here is bounded and every started query is released in a ``finally``,
so a regression fails the suite instead of hanging it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import pytest

from clients.web.hub_bridge import HubBridge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents import Session

_TIMEOUT = 5.0


class _FakeHub:
    """Records retunes and streams a query the test controls the length of."""

    def __init__(self) -> None:
        self.applied: list[tuple[str, str]] = []
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.fail = False

    def apply_hub_settings(self, *, model: str, reasoning_effort: str) -> None:
        self.applied.append((model, reasoning_effort))

    async def run(self, text: str, session: Any) -> AsyncIterator[Any]:
        """Stand in for a streamed run that lasts until ``release`` is set."""
        self.started.set()
        await self.release.wait()
        if self.fail:
            msg = "hub blew up mid-answer"
            raise RuntimeError(msg)
        return
        yield  # pragma: no cover - makes this an async generator


def _bridge() -> tuple[HubBridge, _FakeHub]:
    hub = _FakeHub()
    return HubBridge(hub), hub  # type: ignore[arg-type]


async def _drain(bridge: HubBridge) -> list[dict[str, Any]]:
    # The fake hub ignores the session; run_query only forwards it.
    return [event async for event in bridge.run_query("q", session=cast("Session", None))]


@asynccontextmanager
async def _live_query(bridge: HubBridge, hub: _FakeHub) -> AsyncIterator[None]:
    """Hold a query open (and the bridge lock with it) inside the block."""
    hub.started.clear()
    hub.release.clear()
    query = asyncio.create_task(_drain(bridge))
    try:
        await asyncio.wait_for(hub.started.wait(), timeout=_TIMEOUT)
        yield
    finally:
        hub.release.set()
        await asyncio.wait_for(query, timeout=_TIMEOUT)


@pytest.mark.asyncio
async def test_applies_immediately_when_the_hub_is_idle() -> None:
    """An idle hub is retuned in the request, not queued."""
    bridge, hub = _bridge()

    applied = await asyncio.wait_for(
        bridge.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh"),
        timeout=_TIMEOUT,
    )

    assert applied is True
    assert hub.applied == [("gpt-5.6-terra", "xhigh")]
    assert bridge.has_pending_settings is False


@pytest.mark.asyncio
async def test_queues_instead_of_blocking_on_a_live_query() -> None:
    """The PATCH must return promptly mid-answer, without retuning."""
    bridge, hub = _bridge()

    async with _live_query(bridge, hub):
        applied = await asyncio.wait_for(
            bridge.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh"),
            timeout=_TIMEOUT,
        )

        assert applied is False
        assert hub.applied == []
        assert bridge.has_pending_settings is True


@pytest.mark.asyncio
async def test_a_queued_change_applies_when_that_query_ends() -> None:
    """Not "on the next query" — the user would be told it had applied already."""
    bridge, hub = _bridge()

    async with _live_query(bridge, hub):
        await asyncio.wait_for(
            bridge.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh"),
            timeout=_TIMEOUT,
        )
        assert hub.applied == []

    assert hub.applied == [("gpt-5.6-terra", "xhigh")]
    assert bridge.has_pending_settings is False


@pytest.mark.asyncio
async def test_a_failed_query_still_applies_what_was_queued() -> None:
    """The drain sits in a finally: an error must not strand the change."""
    bridge, hub = _bridge()
    hub.fail = True

    async with _live_query(bridge, hub):
        await asyncio.wait_for(
            bridge.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh"),
            timeout=_TIMEOUT,
        )

    assert hub.applied == [("gpt-5.6-terra", "xhigh")]
    assert bridge.has_pending_settings is False
