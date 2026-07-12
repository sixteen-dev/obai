"""FastAPI server for OBaI web UI.

Provides WebSocket streaming for queries and REST endpoints for
session management. Hub is initialized once as a singleton.

Usage:
    obai web                    # Launch on localhost:8090
    obai web --port 3000        # Custom port
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.websockets import WebSocketState

from clients.web.hub_bridge import HubBridge
from clients.web.store import ConversationStore

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_SESSION_DB = Path.home() / ".obai" / "sessions.db"
_PREFS_FILE = Path.home() / ".obai" / "preferences.json"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _bootstrap_agent_system() -> None:
    """Suppress noisy third-party warnings before heavy imports."""
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"opik\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"sentry_sdk\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"aiohttp\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, message=r"enable_cleanup_closed")
    os.environ.setdefault("LITELLM_LOG", "ERROR")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize hub singleton on startup, close on shutdown."""
    _bootstrap_agent_system()

    logger.info("Initializing OBaI hub (this takes 30-60s)...")
    from core_agents.central_hub_agent import create_central_hub
    from core_agents.tracing import init_opik

    opik_ok = init_opik()
    logger.info("Opik tracing: %s", "enabled" if opik_ok else "DISABLED")

    hub = await create_central_hub()
    bridge = HubBridge(hub)
    bridge.install_mcp_callback()

    store = ConversationStore()
    await store.initialize()

    app.state.hub = hub
    app.state.bridge = bridge
    app.state.store = store
    app.state.sdk_sessions = {}
    app.state.ready = True

    logger.info("OBaI hub ready")
    yield

    await hub.close()


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject state-mutating requests with a non-local Origin header.

    The web UI's `/api` routes are unauthenticated by design (they assume the
    user is running OBaI locally for themselves). That assumption breaks if
    another local app or a browser tab can post arbitrary requests through
    the loopback interface. Reject mutating requests whose ``Origin`` is
    not localhost; GETs are left untouched so the SPA still loads.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Forward safe requests; reject mutating ones from non-local origins."""
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        origin = request.headers.get("origin", "")
        if origin and not _origin_is_local(origin):
            return JSONResponse(
                {"error": "Cross-origin request rejected"},
                status_code=403,
            )
        return await call_next(request)


def _origin_is_local(origin: str) -> bool:
    """Return True if ``origin`` resolves to the loopback interface."""
    # Origin format: "scheme://host[:port]"
    try:
        host = origin.split("://", 1)[1].split("/", 1)[0]
        host = host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
        host = host.strip("[]")
    except Exception:
        return False
    return host in _LOCAL_HOSTS


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="OBaI Web UI", lifespan=lifespan)

    # Reject cross-origin mutating requests before they reach any route.
    app.add_middleware(OriginGuardMiddleware)

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # --- Routes ---

    @app.get("/")
    async def index() -> FileResponse:
        """Serve the SPA shell."""
        return FileResponse(str(_STATIC_DIR / "index.html"))

    @app.get("/api/status")
    async def status() -> JSONResponse:
        """Hub readiness and config info."""
        ready = getattr(app.state, "ready", False)
        info: dict[str, Any] = {
            "ready": ready,
            "status": getattr(app.state, "init_status", "Starting..."),
        }
        if ready:
            from core_agents.config import get_config

            config = get_config()
            info["orchestrator_model"] = config.orchestrator_model
            info["specialist_model"] = config.specialist_model

            from core_agents.tracing import is_opik_enabled

            info["opik_enabled"] = is_opik_enabled()
            if is_opik_enabled():
                info["opik_url"] = config.opik_url
        return JSONResponse(info)

    # --- Session CRUD ---

    @app.get("/api/sessions")
    async def list_sessions() -> JSONResponse:
        store: ConversationStore = app.state.store
        sessions = await store.list_sessions()
        return JSONResponse([s.to_dict() for s in sessions])

    @app.post("/api/sessions")
    async def create_session() -> JSONResponse:
        store: ConversationStore = app.state.store
        session = await store.create_session()
        return JSONResponse(session.to_dict(), status_code=201)

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: dict[str, str]) -> JSONResponse:
        store: ConversationStore = app.state.store
        title = body.get("title", "")
        if not title:
            return JSONResponse({"error": "title is required"}, status_code=400)
        found = await store.rename_session(session_id, title)
        if not found:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> JSONResponse:
        store: ConversationStore = app.state.store
        found = await store.delete_session(session_id)
        if not found:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str) -> JSONResponse:
        store: ConversationStore = app.state.store
        messages = await store.get_messages(session_id)
        return JSONResponse([m.to_dict() for m in messages])

    # --- Preferences ---

    @app.get("/api/preferences")
    async def get_preferences() -> JSONResponse:
        """Read user preferences from ~/.obai/preferences.json."""
        if _PREFS_FILE.exists():
            try:
                data = json.loads(_PREFS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}
        return JSONResponse(data)

    @app.patch("/api/preferences")
    async def update_preferences(body: dict[str, Any]) -> JSONResponse:
        """Update user preferences (merge with existing)."""
        existing: dict[str, Any] = {}
        if _PREFS_FILE.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                existing = json.loads(_PREFS_FILE.read_text())
        existing.update(body)
        _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_FILE.write_text(json.dumps(existing, indent=2) + "\n")
        return JSONResponse(existing)

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        # Reject cross-origin WebSocket handshakes (CSWSH). OriginGuardMiddleware
        # is an http-scope BaseHTTPMiddleware and never runs for websocket scope,
        # so the handshake must validate Origin itself — otherwise a malicious
        # browser tab could open this socket, drive the hub with the user's keys,
        # and read the streamed responses. Browsers always send Origin; non-browser
        # clients (no Origin) are not confused deputies, matching OriginGuardMiddleware.
        origin = ws.headers.get("origin", "")
        if origin and not _origin_is_local(origin):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await _ws_send(ws, {"type": "error", "message": "Invalid JSON"})
                    continue

                msg_type = msg.get("type")
                if msg_type == "query":
                    await _handle_query(app, ws, msg)
                else:
                    await _ws_send(ws, {"type": "error", "message": f"Unknown type: {msg_type}"})
        except WebSocketDisconnect:
            logger.debug("WebSocket disconnected")

    return app


async def _handle_query(app: FastAPI, ws: WebSocket, msg: dict[str, Any]) -> None:
    """Handle a query message from the WebSocket client."""
    session_id = msg.get("session_id", "")
    text = msg.get("text", "").strip()

    if not text:
        await _ws_send(ws, {"type": "error", "message": "Empty query"})
        return
    if not session_id:
        await _ws_send(ws, {"type": "error", "message": "session_id is required"})
        return
    if not getattr(app.state, "ready", False):
        await _ws_send(ws, {"type": "error", "message": "Hub is still initializing, please wait"})
        return

    bridge: HubBridge = app.state.bridge
    store: ConversationStore = app.state.store

    from agents import SQLiteSession

    sdk_sessions: dict[str, Any] = app.state.sdk_sessions
    if session_id not in sdk_sessions:
        _SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
        sdk_sessions[session_id] = SQLiteSession(session_id, db_path=str(_SESSION_DB))
    sdk_session: SQLiteSession = sdk_sessions[session_id]

    await store.add_message(session_id, "user", text)

    user_count = await store.message_count(session_id, "user")
    if user_count == 1:
        new_title = await store.auto_title(session_id, text)
        await _ws_send(ws, {"type": "session_title", "session_id": session_id, "title": new_title})

    await _ws_send(
        ws, {"type": "status", "message": "Processing query...", "session_id": session_id}
    )

    complete_evt: dict[str, Any] | None = None

    try:
        async for event in bridge.run_query(text, sdk_session):
            event["session_id"] = session_id
            if event.get("type") == "complete":
                complete_evt = event
            await _ws_send(ws, event)

        if complete_evt:
            await store.add_message(
                session_id,
                "assistant",
                complete_evt.get("response_text", ""),
                tool_data=complete_evt.get("tool_data"),
                duration_ms=complete_evt.get("duration_ms"),
            )

    except Exception:
        logger.exception("Query streaming failed")
        await _ws_send(
            ws, {"type": "error", "message": "Internal server error", "session_id": session_id}
        )


async def _ws_send(ws: WebSocket, data: dict[str, Any]) -> None:
    """Send a JSON message over WebSocket, ignoring closed connections."""
    if ws.client_state == WebSocketState.CONNECTED:
        with contextlib.suppress(RuntimeError):
            await ws.send_json(data)


def run_server(host: str = "127.0.0.1", port: int = 8090) -> None:
    """Run the web UI server.

    Args:
        host: Bind address.
        port: Bind port.
    """
    import uvicorn

    # Ensure OBaI root is on path for imports
    obai_root = Path(__file__).parent.parent.parent
    if str(obai_root) not in sys.path:
        sys.path.insert(0, str(obai_root))

    log_fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    log_dir = Path.home() / ".obai" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "web.log")
    file_handler.setFormatter(logging.Formatter(log_fmt))
    handlers: list[logging.Handler] = [logging.StreamHandler(), file_handler]
    logging.basicConfig(level=logging.INFO, format=log_fmt, handlers=handlers)

    if host not in _LOCAL_HOSTS:
        logger.warning(
            "OBaI web bound to %s — the local APIs are unauthenticated and the "
            "session store + preferences are world-writable on this host. "
            "Add auth/TLS in front of OBaI before exposing it beyond your "
            "machine.",
            host,
        )

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_level="info",
    )
