"""SQLite-backed conversation store for the web UI.

Stores session metadata and message history for the browser client.
Separate from the agent SDK's SQLiteSession (which handles LLM memory).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from asyncio import to_thread
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DB_PATH = Path.home() / ".obai" / "web_ui.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    tool_data TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);
"""


@dataclass
class SessionInfo:
    """Session metadata for the UI."""

    id: str
    title: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        """Serialize to JSON-friendly dict."""
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class MessageInfo:
    """A stored message for display."""

    id: int
    session_id: str
    role: str
    content: str
    tool_data: list[dict[str, Any]] | None
    duration_ms: int | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tool_data": self.tool_data,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }


@dataclass
class ConversationStore:
    """SQLite store for web UI sessions and messages.

    All public methods are async (run blocking SQLite in a thread).
    """

    db_path: Path = field(default_factory=lambda: _DEFAULT_DB_PATH)
    _initialized: bool = field(default=False, init=False, repr=False)
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await to_thread(self._init_db)
        self._initialized = True

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            msg = "Store not initialized. Call initialize() first."
            raise RuntimeError(msg)
        return self._conn

    # --- Sessions ---

    async def list_sessions(self) -> list[SessionInfo]:
        """List all sessions, most recent first."""
        return await to_thread(self._list_sessions)

    def _list_sessions(self) -> list[SessionInfo]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [SessionInfo(**dict(r)) for r in rows]

    async def create_session(self, title: str = "New conversation") -> SessionInfo:
        """Create a new session."""
        return await to_thread(self._create_session, title)

    def _create_session(self, title: str) -> SessionInfo:
        session_id = f"web_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        conn.commit()
        return SessionInfo(id=session_id, title=title, created_at=now, updated_at=now)

    async def get_session(self, session_id: str) -> SessionInfo | None:
        """Get a session by ID."""
        return await to_thread(self._get_session, session_id)

    def _get_session(self, session_id: str) -> SessionInfo | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return SessionInfo(**dict(row)) if row else None

    async def rename_session(self, session_id: str, title: str) -> bool:
        """Rename a session. Returns True if found."""
        return await to_thread(self._rename_session, session_id, title)

    def _rename_session(self, session_id: str, title: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        cur = conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        conn.commit()
        return cur.rowcount > 0

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and its messages. Returns True if found."""
        return await to_thread(self._delete_session, session_id)

    def _delete_session(self, session_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0

    async def auto_title(self, session_id: str, first_query: str) -> str:
        """Generate a title from the first query and update the session."""
        stripped = first_query.strip()
        title = stripped[:50] + ("..." if len(stripped) > 50 else "")
        await self.rename_session(session_id, title)
        return title

    async def message_count(self, session_id: str, role: str) -> int:
        """Count messages by role for a session."""
        return await to_thread(self._message_count, session_id, role)

    def _message_count(self, session_id: str, role: str) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = ?",
            (session_id, role),
        ).fetchone()
        return int(row[0]) if row else 0

    # --- Messages ---

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_data: list[dict[str, Any]] | None = None,
        duration_ms: int | None = None,
    ) -> int:
        """Add a message to a session. Returns the message ID."""
        return await to_thread(self._add_message, session_id, role, content, tool_data, duration_ms)

    def _add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_data: list[dict[str, Any]] | None,
        duration_ms: int | None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        tool_json = json.dumps(tool_data) if tool_data else None
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO messages"
            " (session_id, role, content, tool_data, duration_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, tool_json, duration_ms, now),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
        return cur.lastrowid or 0

    async def get_messages(self, session_id: str) -> list[MessageInfo]:
        """Get all messages for a session, oldest first."""
        return await to_thread(self._get_messages, session_id)

    def _get_messages(self, session_id: str) -> list[MessageInfo]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, session_id, role, content, tool_data, duration_ms, created_at"
            " FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        result: list[MessageInfo] = []
        for r in rows:
            d = dict(r)
            raw_tool = d.pop("tool_data")
            d["tool_data"] = json.loads(raw_tool) if raw_tool else None
            result.append(MessageInfo(**d))
        return result
