"""CSWSH guard for the /ws endpoint: cross-origin handshakes must be rejected.

The web UI binds to loopback and leaves /api unauthenticated by design, relying
on OriginGuardMiddleware to block cross-origin mutations. That middleware is
http-scope only, so the WebSocket handshake needs its own Origin check — these
tests pin that behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clients.web.server import _origin_is_local, create_app


@asynccontextmanager
async def _noop_lifespan(app: Any) -> AsyncIterator[None]:
    """Skip the 30-60s hub init; the Origin check runs before app.state is used."""
    app.state.ready = False
    yield


def _client() -> TestClient:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    return TestClient(app)


class TestOriginIsLocal:
    """The loopback allowlist that both the middleware and /ws depend on."""

    def test_local_origins_allowed(self) -> None:
        assert _origin_is_local("http://127.0.0.1:8090")
        assert _origin_is_local("http://localhost:8090")
        assert _origin_is_local("http://localhost")

    def test_non_local_origins_rejected(self) -> None:
        assert not _origin_is_local("https://evil.com")
        # Look-alike host that merely starts with a loopback label must not pass.
        assert not _origin_is_local("http://127.0.0.1.evil.com")


class TestWebSocketOriginGuard:
    """The /ws handshake must reject a browser tab from a foreign origin."""

    def test_cross_origin_handshake_rejected(self) -> None:
        # The server closes before accept, so websocket_connect raises on enter;
        # pytest.raises catches that even from a later context manager.
        with (
            _client() as client,
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", headers={"origin": "https://evil.com"}),
        ):
            pass

    def test_local_origin_handshake_accepted(self) -> None:
        # Entering the context means the handshake was accepted; closing
        # immediately is fine (the server just sees a disconnect).
        with (
            _client() as client,
            client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8090"}),
        ):
            pass

    def test_missing_origin_accepted(self) -> None:
        # Non-browser clients send no Origin and are not confused deputies.
        with _client() as client, client.websocket_connect("/ws"):
            pass
