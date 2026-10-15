"""A client that disconnects mid-answer must end its query, not orphan it.

Without an explicit close, the abandoned bridge stream lingers until garbage
collection, and the hub run under it keeps going.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

from clients.web.server import _handle_query

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _GoneSocket:
    """Accepts the first send, then behaves like a closed browser tab."""

    client_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.sends = 0

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sends += 1
        if self.sends > 1:
            raise WebSocketDisconnect(code=1001)


class _Store:
    async def add_message(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def message_count(self, session_id: str, role: str) -> int:
        return 2


class _Bridge:
    """Streams text until closed and records that it was."""

    def __init__(self) -> None:
        self.closed = False

    async def run_query(self, text: str, session: Any) -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                yield {"type": "text_delta", "delta": "chunk"}
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_disconnect_closes_the_running_query() -> None:
    """The bridge stream is closed before the handler gives up the socket."""
    bridge = _Bridge()
    state = SimpleNamespace(ready=True, bridge=bridge, store=_Store(), sdk_sessions={"s1": None})
    app = SimpleNamespace(state=state)

    with pytest.raises(WebSocketDisconnect):
        await _handle_query(
            cast(Any, app), cast(Any, _GoneSocket()), {"session_id": "s1", "text": "q"}
        )

    assert bridge.closed
