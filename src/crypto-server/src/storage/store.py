"""DuckDB storage for crypto candles, jobs, trades, and artifacts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from ..models import Candle


class CryptoStore:
    """Single-connection DuckDB store with serialized writes."""

    def __init__(
        self,
        path: str,
        *,
        memory_limit: str = "2GB",
        checkpoint_interval: int = 1_000,
    ) -> None:
        """Open DuckDB store and ensure schema exists."""
        if checkpoint_interval <= 0:
            msg = "checkpoint_interval must be positive"
            raise ValueError(msg)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.path))
        self._conn.execute(f"SET memory_limit='{memory_limit}'")
        self._write_lock = asyncio.Lock()
        self._checkpoint_interval = checkpoint_interval
        self._writes_since_checkpoint = 0
        self._create_schema()

    def close(self) -> None:
        """Close DuckDB connection."""
        self._conn.close()

    async def upsert_candles(self, candles: list[Candle], granularity: str) -> None:
        """Store candles idempotently."""
        if not candles:
            return
        async with self._write_lock:
            for candle in candles:
                self._conn.execute(
                    """
                    DELETE FROM candles
                    WHERE provider = 'coinbase'
                      AND product_id = ?
                      AND granularity = ?
                      AND start_ts = ?
                    """,
                    [candle.product_id, granularity, candle.start_ts],
                )
                self._conn.execute(
                    """
                    INSERT INTO candles (
                      provider, product_id, granularity, start_ts, start_iso,
                      open, high, low, close, volume, retrieved_at
                    )
                    VALUES ('coinbase', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        candle.product_id,
                        granularity,
                        candle.start_ts,
                        candle.start.isoformat(),
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                        datetime.now(UTC).isoformat(),
                    ],
                )
            self._mark_write()

    async def get_candles(
        self,
        product_id: str,
        granularity: str,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Load cached candles for a range."""
        rows = self._conn.execute(
            """
            SELECT product_id, start_ts, low, high, open, close, volume
            FROM candles
            WHERE provider = 'coinbase'
              AND product_id = ?
              AND granularity = ?
              AND start_ts >= ?
              AND start_ts < ?
            ORDER BY start_ts ASC
            """,
            [product_id.upper(), granularity, start_ts, end_ts],
        ).fetchall()
        return [
            Candle(
                product_id=str(row[0]),
                start=datetime.fromtimestamp(int(row[1]), UTC),
                low=float(row[2]),
                high=float(row[3]),
                open=float(row[4]),
                close=float(row[5]),
                volume=float(row[6]),
            )
            for row in rows
        ]

    async def store_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Store a job result."""
        async with self._write_lock:
            self._conn.execute("DELETE FROM jobs WHERE job_id = ?", [job_id])
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, status, result_json, error, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    job_id,
                    status,
                    json.dumps(result, default=str) if result is not None else None,
                    error,
                    datetime.now(UTC).isoformat(),
                ],
            )
            self._mark_write()

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return stored job."""
        row = self._conn.execute(
            """
            SELECT job_id, status, result_json, error, created_at
            FROM jobs
            WHERE job_id = ?
            """,
            [job_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "job_id": row[0],
            "status": row[1],
            "result": json.loads(row[2]) if row[2] else None,
            "error": row[3],
            "created_at": row[4],
        }

    async def store_trade_log(self, job_id: str, trades: list[dict[str, Any]]) -> None:
        """Store trade log sidecar."""
        async with self._write_lock:
            self._conn.execute("DELETE FROM trade_log WHERE job_id = ?", [job_id])
            for idx, trade in enumerate(trades):
                self._conn.execute(
                    """
                    INSERT INTO trade_log (job_id, trade_idx, trade_json)
                    VALUES (?, ?, ?)
                    """,
                    [job_id, idx, json.dumps(trade, default=str)],
                )
            self._mark_write()

    async def get_trade_log(self, job_id: str, limit: int, offset: int) -> dict[str, Any]:
        """Return paginated trade log."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM trade_log WHERE job_id = ?",
            [job_id],
        ).fetchone()
        total = row[0] if row is not None else 0
        rows = self._conn.execute(
            """
            SELECT trade_json FROM trade_log
            WHERE job_id = ?
            ORDER BY trade_idx ASC
            LIMIT ? OFFSET ?
            """,
            [job_id, limit, offset],
        ).fetchall()
        trades = [json.loads(row[0]) for row in rows]
        return {
            "job_id": job_id,
            "trades": trades,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
        }

    async def store_artifact(self, fingerprint: str, artifact: dict[str, Any]) -> None:
        """Store an exported strategy artifact."""
        async with self._write_lock:
            self._conn.execute("DELETE FROM artifacts WHERE fingerprint = ?", [fingerprint])
            self._conn.execute(
                """
                INSERT INTO artifacts (fingerprint, artifact_json, created_at)
                VALUES (?, ?, ?)
                """,
                [fingerprint, json.dumps(artifact, default=str), datetime.now(UTC).isoformat()],
            )
            self._mark_write()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
              provider VARCHAR NOT NULL,
              product_id VARCHAR NOT NULL,
              granularity VARCHAR NOT NULL,
              start_ts BIGINT NOT NULL,
              start_iso VARCHAR NOT NULL,
              open DOUBLE NOT NULL,
              high DOUBLE NOT NULL,
              low DOUBLE NOT NULL,
              close DOUBLE NOT NULL,
              volume DOUBLE NOT NULL,
              retrieved_at VARCHAR NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              job_id VARCHAR PRIMARY KEY,
              status VARCHAR NOT NULL,
              result_json VARCHAR,
              error VARCHAR,
              created_at VARCHAR NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_log (
              job_id VARCHAR NOT NULL,
              trade_idx INTEGER NOT NULL,
              trade_json VARCHAR NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
              fingerprint VARCHAR PRIMARY KEY,
              artifact_json VARCHAR NOT NULL,
              created_at VARCHAR NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_lookup "
            "ON candles(provider, product_id, granularity, start_ts)"
        )

    def _mark_write(self) -> None:
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint >= self._checkpoint_interval:
            self._conn.execute("CHECKPOINT")
            self._writes_since_checkpoint = 0
