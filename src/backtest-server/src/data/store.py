"""DuckDB-backed OHLCV data storage.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 1.3.
Replaces the previous Parquet-per-symbol implementation.
"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from ..clients.fmp_client import price_basis_for
from ..logging_config import get_logger
from .db import DuckDBManager

logger = get_logger(__name__)

OHLCV_SCHEMA = {
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
}


class DataStore:
    """Read/write OHLCV data via DuckDB.

    All timeframes use column name "date" in returned DataFrames:
    - Daily: pl.Date dtype
    - Intraday: pl.Datetime dtype

    Args:
        db: DuckDB connection manager.

    """

    def __init__(self, db: DuckDBManager) -> None:
        """Initialize data store with DuckDB backend.

        Args:
            db: DuckDB connection manager (must be connected).

        """
        self.db = db

    def read_ohlcv(
        self,
        symbol: str,
        timeframe: str = "daily",
    ) -> pl.DataFrame | None:
        """Read OHLCV data for a symbol from DuckDB.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

        Returns:
            DataFrame with OHLCV data sorted by date, or None if not found.

        """
        # No try/except wrapper: a DuckDB query failure is a real error,
        # not "no data for this symbol". Returning None on a broken query
        # makes the caller silently re-download, masking the underlying
        # problem. Let DB exceptions propagate.
        result = self.db.conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = $1 AND timeframe = $2
            ORDER BY timestamp
            """,
            [symbol.upper(), timeframe],
        )
        df = result.pl()

        if df.is_empty():
            return None

        # Preserve "date" column name for all consumers.
        # Daily: cast TIMESTAMP → Date. Intraday: keep as Datetime.
        # Always select in canonical order: date, open, high, low, close, volume
        if timeframe == "daily":
            df = df.with_columns(
                pl.col("timestamp").cast(pl.Date).alias("date"),
            ).select(["date", "open", "high", "low", "close", "volume"])
        else:
            df = df.rename({"timestamp": "date"})

        return df

    def write_ohlcv(
        self,
        symbol: str,
        df: pl.DataFrame,
        timeframe: str = "daily",
    ) -> None:
        """Write OHLCV data for a symbol to DuckDB.

        Uses INSERT ... ON CONFLICT DO UPDATE in a single transaction
        with _meta update. This is the sync version — safe for single-
        threaded contexts (tests, migration scripts). For async server
        contexts with concurrent coroutines, use write_ohlcv_async()
        which acquires the write lock first.

        Args:
            symbol: Stock ticker symbol.
            df: DataFrame with OHLCV data (must have date/open/high/low/close/volume).
            timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

        """
        if df.is_empty():
            return

        sym = symbol.upper()

        # Normalize the date column to timestamp for DuckDB storage
        if "date" in df.columns:
            write_df = df.with_columns(
                pl.col("date").cast(pl.Datetime("us")).alias("timestamp"),
            ).drop("date")
        elif "timestamp" in df.columns:
            write_df = df
        else:
            msg = "DataFrame must have 'date' or 'timestamp' column"
            raise ValueError(msg)

        # Add symbol and timeframe columns
        write_df = write_df.with_columns(
            pl.lit(sym).alias("symbol"),
            pl.lit(timeframe).alias("timeframe"),
        ).select(["symbol", "timestamp", "timeframe", "open", "high", "low", "close", "volume"])

        conn = self.db.conn

        # Single transaction: upsert data + update meta
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.register("_write_batch", write_df)
            conn.execute(
                """
                INSERT INTO ohlcv
                SELECT * FROM _write_batch
                ON CONFLICT (symbol, timeframe, timestamp)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
            )
            conn.unregister("_write_batch")

            conn.execute(
                """
                INSERT INTO _meta (symbol, timeframe, first_timestamp, last_timestamp,
                                   row_count, last_refreshed, price_basis)
                VALUES ($1, $2,
                    (SELECT MIN(timestamp) FROM ohlcv WHERE symbol = $1 AND timeframe = $2),
                    (SELECT MAX(timestamp) FROM ohlcv WHERE symbol = $1 AND timeframe = $2),
                    (SELECT COUNT(*) FROM ohlcv WHERE symbol = $1 AND timeframe = $2),
                    NOW(),
                    $3
                )
                ON CONFLICT (symbol, timeframe) DO UPDATE SET
                    first_timestamp = EXCLUDED.first_timestamp,
                    last_timestamp = EXCLUDED.last_timestamp,
                    row_count = EXCLUDED.row_count,
                    last_refreshed = EXCLUDED.last_refreshed,
                    price_basis = EXCLUDED.price_basis
                """,
                [sym, timeframe, price_basis_for(timeframe)],
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                logger.exception("rollback_failed_after_write_error")
            raise

        logger.info(
            "duckdb_written",
            symbol=sym,
            timeframe=timeframe,
            rows=len(write_df),
        )

    async def write_ohlcv_async(
        self,
        symbol: str,
        df: pl.DataFrame,
        timeframe: str = "daily",
    ) -> None:
        """Write OHLCV data under the async write lock.

        Use this in async server contexts where concurrent coroutines
        may interleave writes. Acquires DuckDBManager._write_lock
        before calling write_ohlcv().

        Args:
            symbol: Stock ticker symbol.
            df: DataFrame with OHLCV data.
            timeframe: Bar timeframe.

        """
        async with self.db._write_lock:  # noqa: SLF001
            self.write_ohlcv(symbol, df, timeframe=timeframe)

    def get_date_range(
        self,
        symbol: str,
        timeframe: str = "daily",
    ) -> tuple[date, date] | tuple[datetime, datetime] | None:
        """Get the min/max date range stored for a symbol.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.

        Returns:
            Tuple of (start, end) as date (daily) or datetime (intraday),
            or None if no data.

        """
        result = self.db.conn.execute(
            """
            SELECT first_timestamp, last_timestamp
            FROM _meta
            WHERE symbol = $1 AND timeframe = $2
            """,
            [symbol.upper(), timeframe],
        ).fetchone()

        if result is None:
            return None

        first_ts, last_ts = result
        if first_ts is None or last_ts is None:
            return None

        # Daily: return date objects. Intraday: return datetime objects.
        if timeframe == "daily":
            if isinstance(first_ts, datetime):
                return (first_ts.date(), last_ts.date())
            return (first_ts, last_ts)

        # Intraday — return as datetime
        if isinstance(first_ts, date) and not isinstance(first_ts, datetime):
            return (
                datetime(first_ts.year, first_ts.month, first_ts.day),
                datetime(last_ts.year, last_ts.month, last_ts.day),
            )
        return (first_ts, last_ts)

    def list_available_symbols(
        self,
        timeframe: str = "daily",
    ) -> list[str]:
        """List all symbols with stored data for a timeframe.

        Args:
            timeframe: Bar timeframe to filter by.

        Returns:
            Sorted list of symbol strings.

        """
        result = self.db.conn.execute(
            """
            SELECT DISTINCT symbol
            FROM _meta
            WHERE timeframe = $1
            ORDER BY symbol
            """,
            [timeframe],
        ).fetchall()
        return [row[0] for row in result]

    def get_price_basis(
        self,
        symbol: str,
        timeframe: str = "daily",
    ) -> str | None:
        """Return the adjustment basis a symbol's cached rows were written on.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.

        Returns:
            The stored basis, or None when nothing is cached or the rows
            predate basis tracking and their basis is therefore unknown.

        """
        result = self.db.conn.execute(
            """
            SELECT price_basis
            FROM _meta
            WHERE symbol = $1 AND timeframe = $2
            """,
            [symbol.upper(), timeframe],
        ).fetchone()
        if result is None or result[0] is None:
            return None
        return str(result[0])

    def delete_symbol(
        self,
        symbol: str,
        timeframe: str = "daily",
    ) -> int:
        """Drop every cached row for a symbol/timeframe and forget its metadata.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.

        Returns:
            Number of OHLCV rows removed.

        """
        sym = symbol.upper()
        conn = self.db.conn
        removed = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol = $1 AND timeframe = $2",
            [sym, timeframe],
        ).fetchone()
        count = int(removed[0]) if removed else 0

        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM ohlcv WHERE symbol = $1 AND timeframe = $2", [sym, timeframe])
            conn.execute("DELETE FROM _meta WHERE symbol = $1 AND timeframe = $2", [sym, timeframe])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            logger.exception("delete_symbol_failed", symbol=sym, timeframe=timeframe)
            raise

        logger.info("symbol_cache_deleted", symbol=sym, timeframe=timeframe, rows=count)
        return count

    def get_last_modified(
        self,
        symbol: str,
        timeframe: str = "daily",
    ) -> float | None:
        """Get last refresh timestamp for a symbol's data.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.

        Returns:
            Unix timestamp of last refresh, or None if not found.

        """
        result = self.db.conn.execute(
            """
            SELECT last_refreshed
            FROM _meta
            WHERE symbol = $1 AND timeframe = $2
            """,
            [symbol.upper(), timeframe],
        ).fetchone()

        if result is None or result[0] is None:
            return None

        ts = result[0]
        if isinstance(ts, datetime):
            return ts.timestamp()
        return float(ts)
