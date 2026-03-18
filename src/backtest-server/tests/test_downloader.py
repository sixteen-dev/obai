"""Tests for DataDownloader — freshness, backfill, forward-fill, dedup."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from src.data.downloader import DataDownloader


def _make_ohlcv(start: date, days: int) -> list[dict[str, Any]]:
    """Generate fake OHLCV rows for FMP mock responses."""
    rows: list[dict[str, Any]] = []
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:  # noqa: PLR2004
            continue
        rows.append(
            {
                "date": d.isoformat(),
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 102.0 + i,
                "volume": 1_000_000,
            }
        )
    return rows


def _make_parquet_df(start: date, days: int) -> pl.DataFrame:
    """Build a Polars DataFrame like what DataStore.read_ohlcv returns."""
    rows = _make_ohlcv(start, days)
    if not rows:
        return pl.DataFrame(schema={"date": pl.Date})
    return pl.DataFrame(rows).with_columns(
        pl.col("date").str.to_date().alias("date"),
        pl.col("volume").cast(pl.Int64),
    )


@pytest.fixture()
def mock_fmp() -> AsyncMock:
    """Mock FMP client."""
    return AsyncMock()


@pytest.fixture()
def mock_store() -> MagicMock:
    """Mock DataStore."""
    store = MagicMock()
    store.read_ohlcv.return_value = None
    store.get_date_range.return_value = None
    store.get_last_modified.return_value = None
    return store


class TestFreshDataDownload:
    """No existing data — should fetch full range."""

    @pytest.mark.asyncio()
    async def test_downloads_full_range(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Fresh symbol fetches from start_date to end_date."""
        mock_fmp.get_historical_daily.return_value = _make_ohlcv(
            date(2023, 1, 2),
            365,
        )
        dl = DataDownloader(mock_fmp, mock_store)

        result = await dl.download_symbol("AAPL", "2023-01-01", "2024-01-01")

        mock_fmp.get_historical_daily.assert_called_once_with(
            symbol="AAPL",
            start_date="2023-01-01",
            end_date="2024-01-01",
        )
        assert len(result) > 200  # noqa: PLR2004


class TestBackfill:
    """Existing data is newer than requested start — should backfill."""

    @pytest.mark.asyncio()
    async def test_fetches_earlier_data(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """If cached data starts 2023-01-02, requesting from 2020-01-01 backfills."""
        cached = _make_parquet_df(date(2023, 1, 2), 252)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 12, 29))

        backfill_data = _make_ohlcv(date(2020, 1, 2), 1095)
        mock_fmp.get_historical_daily.return_value = backfill_data

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("AAPL", "2020-01-01", "2024-01-01")

        # Should have called FMP for the backfill range
        calls = mock_fmp.get_historical_daily.call_args_list
        assert len(calls) >= 1
        first_call = calls[0]
        assert first_call.kwargs["start_date"] == "2020-01-01"
        assert first_call.kwargs["end_date"] == "2023-01-01"

        # Result should include both old + new data
        assert len(result) > len(cached)


class TestForwardFill:
    """Existing data ends before requested end — should fetch newer data."""

    @pytest.mark.asyncio()
    async def test_fetches_later_data(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """If cached ends 2023-06-30 but we need through 2024-12-31, fetch the gap."""
        cached = _make_parquet_df(date(2023, 1, 2), 130)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 6, 30))

        forward_data = _make_ohlcv(date(2023, 7, 3), 390)
        mock_fmp.get_historical_daily.return_value = forward_data

        dl = DataDownloader(mock_fmp, mock_store)
        # Request starts at cached start so only forward-fill is needed
        result = await dl.download_symbol("AAPL", "2023-01-02", "2024-12-31")

        calls = mock_fmp.get_historical_daily.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["start_date"] == "2023-07-01"
        assert len(result) > len(cached)


class TestBothGaps:
    """Cached data is a subset — needs both backfill and forward-fill."""

    @pytest.mark.asyncio()
    async def test_fetches_both_gaps(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Cached 2022-2023, requesting 2020-2024 should make 2 FMP calls."""
        cached = _make_parquet_df(date(2022, 1, 3), 365)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2022, 1, 3), date(2022, 12, 30))

        mock_fmp.get_historical_daily.return_value = _make_ohlcv(
            date(2020, 1, 2),
            200,
        )

        dl = DataDownloader(mock_fmp, mock_store)
        await dl.download_symbol("AAPL", "2020-01-01", "2024-12-31")

        # Should have 2 calls: backfill + forward-fill
        assert mock_fmp.get_historical_daily.call_count == 2  # noqa: PLR2004


class TestCacheCoversRange:
    """Cached data fully covers the request — no FMP calls needed."""

    @pytest.mark.asyncio()
    async def test_no_fetch_when_covered(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """If cache covers 2020-2024 and we request 2022-2023, skip FMP."""
        cached = _make_parquet_df(date(2020, 1, 2), 1826)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2020, 1, 2), date(2024, 12, 31))

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("AAPL", "2022-01-01", "2023-01-01")

        mock_fmp.get_historical_daily.assert_not_called()
        assert len(result) > 0


class TestEnsureDataFreshnessWithRange:
    """ensure_data should re-download when cache doesn't cover range."""

    @pytest.mark.asyncio()
    async def test_stale_range_triggers_download(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Fresh file but insufficient range should trigger download."""
        # File is fresh (modified just now)
        mock_store.get_last_modified.return_value = time.time()

        # But only covers 2023
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 12, 29))
        cached = _make_parquet_df(date(2023, 1, 2), 252)
        mock_store.read_ohlcv.return_value = cached

        mock_fmp.get_historical_daily.return_value = _make_ohlcv(
            date(2020, 1, 2),
            1095,
        )

        dl = DataDownloader(mock_fmp, mock_store)
        results = await dl.ensure_data(["AAPL"], "2020-01-01", "2024-12-31")

        # Should NOT have been a cache hit — should have called download_symbol
        assert "AAPL" in results
        assert mock_fmp.get_historical_daily.call_count >= 1


class TestDedup:
    """Overlapping fetches should not create duplicate dates."""

    @pytest.mark.asyncio()
    async def test_no_duplicate_dates(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Merging existing + new data deduplicates by date."""
        cached = _make_parquet_df(date(2023, 1, 2), 100)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 5, 19))

        # FMP returns some overlapping dates (forward-fill from 2023-05-20)
        mock_fmp.get_historical_daily.return_value = _make_ohlcv(
            date(2023, 5, 15),
            200,
        )

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("AAPL", "2023-01-01", "2024-01-01")

        # No duplicate dates
        assert result["date"].is_duplicated().sum() == 0
