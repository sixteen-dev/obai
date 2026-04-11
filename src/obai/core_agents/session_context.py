"""Session context store for durable cross-turn app state.

Provides structured context storage keyed strictly by session_id.
Separate from Agent SDK sessions.db (LLM memory) and web_ui.db
(UI display history).

DB path: ~/.obai/app_state.db
"""

from __future__ import annotations

import json
import logging
import sqlite3
from asyncio import to_thread
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".obai" / "app_state.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS session_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    context_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_context_lookup
    ON session_context(session_id, context_type, created_at);
"""


@dataclass
class SessionContextStore:
    """SQLite store for durable app-owned session context.

    All public methods are async (blocking SQLite runs in a thread).
    Retrieval is strictly by exact session_id — never cross-session.
    """

    db_path: Path = field(default_factory=lambda: _DEFAULT_DB_PATH)
    _initialized: bool = field(default=False, init=False, repr=False)
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    async def initialize(self) -> None:
        """Create tables if they don't exist. Idempotent."""
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await to_thread(self._init_db)
        self._initialized = True

    def _init_db(self) -> None:
        logger.info("Initializing context store at %s", self.db_path)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("Context store initialized (tables ready)")

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            msg = "Store not initialized. Call initialize() first."
            raise RuntimeError(msg)
        return self._conn

    async def write_context(
        self,
        session_id: str,
        context_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Write a context entry for the given session.

        Args:
            session_id: Exact session identifier.
            context_type: Category key (e.g. 'prediction_market').
            payload: JSON-serializable dict to store.
        """
        await to_thread(self._write_context, session_id, context_type, payload)

    def _write_context(
        self,
        session_id: str,
        context_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        conn.execute(
            "INSERT INTO session_context (session_id, context_type, payload_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, context_type, json.dumps(payload), now),
        )
        conn.commit()
        logger.info(
            "Context written: session=%s type=%s keys=%s",
            session_id,
            context_type,
            list(payload.keys()),
        )

    async def read_context(
        self,
        session_id: str,
        context_type: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Read context entries for the given session, newest first.

        Args:
            session_id: Exact session identifier.
            context_type: Category key to filter by.
            limit: Max entries to return.

        Returns:
            List of parsed payload dicts, ordered by created_at DESC.
        """
        return await to_thread(self._read_context, session_id, context_type, limit)

    def _read_context(
        self,
        session_id: str,
        context_type: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT payload_json FROM session_context"
            " WHERE session_id = ? AND context_type = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (session_id, context_type, limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            parsed: dict[str, Any] = json.loads(row["payload_json"])
            results.append(parsed)
        logger.info(
            "Context read: session=%s type=%s rows=%d",
            session_id,
            context_type,
            len(results),
        )
        return results

    async def clear_session(self, session_id: str) -> int:
        """Delete all context entries for a session.

        Args:
            session_id: Session to clear.

        Returns:
            Number of rows deleted.
        """
        return await to_thread(self._clear_session, session_id)

    def _clear_session(self, session_id: str) -> int:
        conn = self._connect()
        cur = conn.execute(
            "DELETE FROM session_context WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return cur.rowcount

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._initialized = False


_store: SessionContextStore | None = None


def get_context_store() -> SessionContextStore:
    """Get or create the singleton context store."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SessionContextStore()
    return _store
