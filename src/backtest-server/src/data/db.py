"""DuckDB connection manager for OHLCV data storage.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Section 5 + Phase 1.2.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from ..logging_config import get_logger

logger = get_logger(__name__)

# Schema DDL — Section 5.2-5.4 of design doc
_CREATE_OHLCV = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol      VARCHAR NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    timeframe   VARCHAR NOT NULL,
    open        DOUBLE NOT NULL,
    high        DOUBLE NOT NULL,
    low         DOUBLE NOT NULL,
    close       DOUBLE NOT NULL,
    volume      BIGINT NOT NULL,
    PRIMARY KEY (symbol, timeframe, timestamp)
);
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS _meta (
    symbol           VARCHAR NOT NULL,
    timeframe        VARCHAR NOT NULL,
    first_timestamp  TIMESTAMP,
    last_timestamp   TIMESTAMP,
    row_count        BIGINT,
    last_refreshed   TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe_symbol
    ON ohlcv (timeframe, symbol);
"""


@dataclass
class DuckDBManager:
    """Manages a single embedded DuckDB connection.

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

        """
        if self._conn is not None:
            return self._conn

        db_str = str(self.db_path) if self.db_path != ":memory:" else ":memory:"
        if db_str != ":memory:":
            Path(db_str).parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = duckdb.connect(db_str)
        except duckdb.IOException as exc:
            msg = f"Failed to open DuckDB at '{db_str}': {exc}"
            logger.error("duckdb_connect_failed", path=db_str, error=str(exc))
            raise RuntimeError(msg) from exc

        self._conn.execute(f"SET memory_limit = '{self.memory_limit}'")
        self._conn.execute("SET checkpoint_threshold = '256MB'")
        self._init_schema()

        logger.info(
            "duckdb_connected",
            path=db_str,
            memory_limit=self.memory_limit,
        )
        return self._conn

    def close(self) -> None:
        """Checkpoint and close the connection."""
        if self._conn is not None:
            try:
                self._conn.execute("CHECKPOINT")
            except Exception:
                logger.exception("duckdb_checkpoint_failed_on_close")
            finally:
                self._conn.close()
                self._conn = None
                logger.info("duckdb_closed")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Get the active connection, connecting if needed.

        Returns:
            The DuckDB connection object.

        Raises:
            RuntimeError: If not connected and auto-connect fails.

        """
        if self._conn is None:
            return self.connect()
        return self._conn

    async def execute_write(
        self,
        query: str,
        params: list[object] | None = None,
    ) -> duckdb.DuckDBPyConnection:
        """Execute a write query under the async write lock.

        Args:
            query: SQL query string.
            params: Optional query parameters.

        Returns:
            The connection (for chaining or fetching results).

        """
        async with self._write_lock:
            if params:
                return self.conn.execute(query, params)
            return self.conn.execute(query)

    async def execute_write_many(
        self,
        queries: list[tuple[str, list[object] | None]],
    ) -> None:
        """Execute multiple write queries in a single transaction.

        Args:
            queries: List of (query, params) tuples.

        """
        async with self._write_lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                for query, params in queries:
                    if params:
                        self.conn.execute(query, params)
                    else:
                        self.conn.execute(query)
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except Exception:
                    logger.exception("rollback_failed_after_write_error")
                raise

    def db_size_bytes(self) -> int:
        """Get the database file size in bytes.

        Returns:
            File size in bytes, or 0 for in-memory databases.

        """
        if self.db_path == ":memory:":
            return 0
        path = Path(self.db_path)
        if not path.exists():
            return 0
        return path.stat().st_size

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        if self._conn is None:
            return
        self._conn.execute(_CREATE_OHLCV)
        self._conn.execute(_CREATE_META)
        self._conn.execute(_CREATE_INDEX)
        logger.info("duckdb_schema_initialized")
