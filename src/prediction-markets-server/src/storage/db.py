"""DuckDB connection manager for prediction-markets historical analytics.

Pattern mirrors src/backtest-server/src/data/db.py: a single embedded
connection per process, reads interleave via DuckDB MVCC, writes serialize
through an asyncio.Lock so concurrent coroutines cannot interleave at await
points.

Schema reference: docs/prediction-markets-historical-analytics-upgrade.md §8.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from ..logging_config import get_logger
from .schema import ALL_DDL

logger = get_logger(__name__)

_CHECKPOINT_THRESHOLD = "256MB"


@dataclass
class PredictionDuckDBManager:
    """Manages a single embedded DuckDB connection for the prediction store.

    Single connection per process. Reads are safe to interleave (MVCC).
    Writes are serialized via asyncio.Lock to prevent corruption from
    concurrent coroutines interleaving at await points.

    Args:
        db_path: Path to the .duckdb file. Use ":memory:" for tests.
        memory_limit: DuckDB memory_limit setting (e.g., "4GB").

    """

    db_path: Path | str
    memory_limit: str = "4GB"
    _conn: duckdb.DuckDBPyConnection | None = field(default=None, repr=False)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Open the database and initialize schema.

        Returns:
            The DuckDB connection object.

        Raises:
            RuntimeError: If the underlying file cannot be opened.

        """
        if self._conn is not None:
            return self._conn

        db_str = str(self.db_path) if self.db_path != ":memory:" else ":memory:"
        if db_str != ":memory:":
            Path(db_str).parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = duckdb.connect(db_str)
        except duckdb.IOException as exc:
            msg = f"Failed to open prediction DuckDB at {db_str!r}: {exc}"
            logger.error("prediction_duckdb_connect_failed", path=db_str, error=str(exc))
            raise RuntimeError(msg) from exc

        self._conn.execute(f"SET memory_limit = '{self.memory_limit}'")
        self._conn.execute(f"SET checkpoint_threshold = '{_CHECKPOINT_THRESHOLD}'")
        self._init_schema()

        logger.info(
            "prediction_duckdb_connected",
            path=db_str,
            memory_limit=self.memory_limit,
        )
        return self._conn

    def close(self) -> None:
        """Checkpoint and close the connection."""
        if self._conn is None:
            return
        try:
            self._conn.execute("CHECKPOINT")
        except duckdb.Error:
            logger.exception("prediction_duckdb_checkpoint_failed_on_close")
        finally:
            self._conn.close()
            self._conn = None
            logger.info("prediction_duckdb_closed")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Get the active connection, connecting if needed.

        Returns:
            The DuckDB connection object.

        """
        if self._conn is None:
            return self.connect()
        return self._conn

    async def execute_write(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> duckdb.DuckDBPyConnection:
        """Execute a write query under the async write lock.

        Args:
            query: SQL query string.
            params: Optional positional parameters.

        Returns:
            The connection (for chaining or fetching results).

        """
        async with self._write_lock:
            if params is not None:
                return self.conn.execute(query, params)
            return self.conn.execute(query)

    async def execute_write_many(
        self,
        queries: Sequence[tuple[str, list[Any] | None]],
    ) -> None:
        """Execute multiple write queries inside a single transaction.

        Args:
            queries: List of (query, params) tuples.

        Raises:
            duckdb.Error: Re-raised after rollback. Original exception
                chain is preserved.

        """
        async with self._write_lock:
            conn = self.conn
            conn.execute("BEGIN TRANSACTION")
            try:
                for query, params in queries:
                    if params is not None:
                        conn.execute(query, params)
                    else:
                        conn.execute(query)
                conn.execute("COMMIT")
            except duckdb.Error:
                try:
                    conn.execute("ROLLBACK")
                except duckdb.Error:
                    logger.exception("prediction_duckdb_rollback_failed_after_write_error")
                raise

    def db_size_bytes(self) -> int:
        """Get the database file size in bytes.

        Returns:
            File size in bytes, or 0 for in-memory databases or
            missing files.

        """
        if self.db_path == ":memory:":
            return 0
        path = Path(self.db_path)
        if not path.exists():
            return 0
        return path.stat().st_size

    def db_size_gb(self) -> float:
        """Get the database file size in gibibytes."""
        return self.db_size_bytes() / (1024**3)

    def _init_schema(self) -> None:
        """Create tables and indexes idempotently.

        DDL list lives in storage/schema.py. Iterate in declared order so
        the first failure surfaces with the offending the offending statement's context
        rather than a generic init error.
        """
        if self._conn is None:
            return
        for stmt in ALL_DDL:
            self._conn.execute(stmt)
        logger.info("prediction_duckdb_schema_initialized", statements=len(ALL_DDL))
