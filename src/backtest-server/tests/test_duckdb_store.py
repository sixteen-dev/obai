"""Tests for DuckDB-backed DataStore.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 1.8.
Uses in-memory DuckDB (":memory:") for isolation.
"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from src.data.db import DuckDBManager
from src.data.store import DataStore


@pytest.fixture
def db() -> DuckDBManager:
    """Create an in-memory DuckDB manager."""
    manager = DuckDBManager(db_path=":memory:", memory_limit="256MB")
    manager.connect()
    return manager


@pytest.fixture
def store(db: DuckDBManager) -> DataStore:
    """Create a DataStore backed by in-memory DuckDB."""
    return DataStore(db=db)


def _sample_daily_df(rows: int = 5, start_date: str = "2024-01-15") -> pl.DataFrame:
    """Build a small daily OHLCV DataFrame for testing."""
    base = date.fromisoformat(start_date)
    dates = [date.fromordinal(base.toordinal() + i) for i in range(rows)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": [100.0 + i for i in range(rows)],
            "high": [105.0 + i for i in range(rows)],
            "low": [95.0 + i for i in range(rows)],
            "close": [102.0 + i for i in range(rows)],
            "volume": [1_000_000 + i * 100 for i in range(rows)],
        }
    )


class TestReadWriteOhlcv:
    """CRUD operations on OHLCV data."""

    def test_write_and_read_daily(self, store: DataStore) -> None:
        df = _sample_daily_df()
        store.write_ohlcv("AAPL", df, timeframe="daily")

        result = store.read_ohlcv("AAPL", timeframe="daily")
        assert result is not None
        assert len(result) == 5
        assert "date" in result.columns
        assert result["date"].dtype == pl.Date

    def test_read_nonexistent_returns_none(self, store: DataStore) -> None:
        result = store.read_ohlcv("NOPE", timeframe="daily")
        assert result is None

    def test_write_preserves_values(self, store: DataStore) -> None:
        df = _sample_daily_df(rows=3)
        store.write_ohlcv("MSFT", df, timeframe="daily")

        result = store.read_ohlcv("MSFT", timeframe="daily")
        assert result is not None
        assert result["open"].to_list() == [100.0, 101.0, 102.0]
        assert result["close"].to_list() == [102.0, 103.0, 104.0]
        assert result["volume"].to_list() == [1_000_000, 1_000_100, 1_000_200]

    def test_write_is_sorted_by_date(self, store: DataStore) -> None:
        df = pl.DataFrame(
            {
                "date": [date(2024, 1, 20), date(2024, 1, 15), date(2024, 1, 18)],
                "open": [100.0, 101.0, 102.0],
                "high": [105.0, 106.0, 107.0],
                "low": [95.0, 96.0, 97.0],
                "close": [102.0, 103.0, 104.0],
                "volume": [1000, 2000, 3000],
            }
        )
        store.write_ohlcv("TSLA", df, timeframe="daily")

        result = store.read_ohlcv("TSLA", timeframe="daily")
        assert result is not None
        assert result["date"].to_list() == [
            date(2024, 1, 15),
            date(2024, 1, 18),
            date(2024, 1, 20),
        ]

    def test_upsert_updates_existing(self, store: DataStore) -> None:
        df1 = pl.DataFrame(
            {
                "date": [date(2024, 1, 15)],
                "open": [100.0],
                "high": [105.0],
                "low": [95.0],
                "close": [102.0],
                "volume": [1000],
            }
        )
        store.write_ohlcv("GOOG", df1, timeframe="daily")

        df2 = pl.DataFrame(
            {
                "date": [date(2024, 1, 15), date(2024, 1, 16)],
                "open": [200.0, 201.0],
                "high": [210.0, 211.0],
                "low": [190.0, 191.0],
                "close": [205.0, 206.0],
                "volume": [2000, 2100],
            }
        )
        store.write_ohlcv("GOOG", df2, timeframe="daily")

        result = store.read_ohlcv("GOOG", timeframe="daily")
        assert result is not None
        assert len(result) == 2
        # The first row should be updated (200.0, not 100.0)
        assert result["open"].to_list() == [200.0, 201.0]

    def test_empty_df_is_noop(self, store: DataStore) -> None:
        empty = pl.DataFrame(
            {
                "date": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
            }
        ).cast({"date": pl.Date, "volume": pl.Int64})

        store.write_ohlcv("EMPTY", empty, timeframe="daily")
        result = store.read_ohlcv("EMPTY", timeframe="daily")
        assert result is None


class TestDateRange:
    """Date range queries via _meta table."""

    def test_get_date_range(self, store: DataStore) -> None:
        df = _sample_daily_df(rows=10, start_date="2024-03-01")
        store.write_ohlcv("SPY", df, timeframe="daily")

        result = store.get_date_range("SPY", timeframe="daily")
        assert result is not None
        start, end = result
        assert isinstance(start, date)
        assert isinstance(end, date)
        assert start == date(2024, 3, 1)
        assert end == date(2024, 3, 10)

    def test_get_date_range_nonexistent(self, store: DataStore) -> None:
        result = store.get_date_range("NOPE", timeframe="daily")
        assert result is None


class TestListSymbols:
    """Symbol listing."""

    def test_list_symbols(self, store: DataStore) -> None:
        for sym in ["MSFT", "AAPL", "GOOG"]:
            store.write_ohlcv(sym, _sample_daily_df(rows=2), timeframe="daily")

        symbols = store.list_available_symbols(timeframe="daily")
        assert symbols == ["AAPL", "GOOG", "MSFT"]

    def test_list_symbols_empty(self, store: DataStore) -> None:
        symbols = store.list_available_symbols(timeframe="daily")
        assert symbols == []

    def test_list_symbols_filters_by_timeframe(self, store: DataStore) -> None:
        store.write_ohlcv("AAPL", _sample_daily_df(rows=2), timeframe="daily")
        store.write_ohlcv("MSFT", _sample_daily_df(rows=2), timeframe="5min")

        daily = store.list_available_symbols(timeframe="daily")
        fivemin = store.list_available_symbols(timeframe="5min")
        assert daily == ["AAPL"]
        assert fivemin == ["MSFT"]


class TestLastModified:
    """Freshness timestamps."""

    def test_get_last_modified(self, store: DataStore) -> None:
        store.write_ohlcv("AAPL", _sample_daily_df(), timeframe="daily")

        mtime = store.get_last_modified("AAPL", timeframe="daily")
        assert mtime is not None
        assert isinstance(mtime, float)
        # Should be a recent timestamp (within last minute)
        assert mtime > datetime(2024, 1, 1).timestamp()

    def test_get_last_modified_nonexistent(self, store: DataStore) -> None:
        mtime = store.get_last_modified("NOPE", timeframe="daily")
        assert mtime is None


class TestDuckDBManager:
    """DuckDBManager lifecycle tests."""

    def test_connect_creates_schema(self, db: DuckDBManager) -> None:
        tables = db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        table_names = {row[0] for row in tables}
        assert "ohlcv" in table_names
        assert "_meta" in table_names

    def test_db_size_memory(self, db: DuckDBManager) -> None:
        assert db.db_size_bytes() == 0  # in-memory

    def test_close_and_reconnect(self) -> None:
        manager = DuckDBManager(db_path=":memory:", memory_limit="256MB")
        manager.connect()
        manager.close()
        assert manager._conn is None  # noqa: SLF001
        # Reconnect via .conn property
        conn = manager.conn
        assert conn is not None
