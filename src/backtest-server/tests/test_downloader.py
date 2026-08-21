"""Tests for DataDownloader — freshness, backfill, forward-fill, dedup."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from src.clients.fmp_client import FMPClient
from src.config import Settings
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


def _bounds(df: pl.DataFrame) -> tuple[date, date]:
    """Return a frame's first and last date, narrowed to ``date``."""
    first, last = df["date"].min(), df["date"].max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    return first, last


def _make_parquet_df(start: date, days: int) -> pl.DataFrame:
    """Build a Polars DataFrame like what DataStore.read_ohlcv returns."""
    rows = _make_ohlcv(start, days)
    if not rows:
        return pl.DataFrame(schema={"date": pl.Date})
    return pl.DataFrame(rows).with_columns(
        pl.col("date").str.to_date().alias("date"),
        pl.col("volume").cast(pl.Int64),
    )


def _split_ohlcv(rows: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    """Rebase OHLC prices by a split factor (post-split = pre-split / factor)."""
    return [
        {
            **row,
            "open": row["open"] / factor,
            "high": row["high"] / factor,
            "low": row["low"] / factor,
            "close": row["close"] / factor,
        }
        for row in rows
    ]


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
    # Cached rows are on the basis the client fetches today, so tests exercise
    # the ordinary path rather than the stale-basis purge.
    store.get_price_basis.return_value = "dividend_adjusted"
    store.write_ohlcv_async = AsyncMock()
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

        # A drift probe also fires (cache overlaps the request) and finds no
        # drift; the backfill gap fetch must still be present with its range.
        calls = mock_fmp.get_historical_daily.call_args_list
        backfill = [c for c in calls if c.kwargs["start_date"] == "2020-01-01"]
        assert len(backfill) == 1
        assert backfill[0].kwargs["end_date"] == "2023-01-01"

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

        # One drift probe (no drift) + exactly one forward-fill gap, no backfill.
        calls = mock_fmp.get_historical_daily.call_args_list
        assert mock_fmp.get_historical_daily.call_count == 2  # noqa: PLR2004
        forward = [c for c in calls if c.kwargs["start_date"] == "2023-07-01"]
        assert len(forward) == 1
        assert forward[0].kwargs["end_date"] == "2024-12-31"
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

        # 2 gap fetches (backfill + forward-fill) plus 1 drift probe (no drift).
        calls = mock_fmp.get_historical_daily.call_args_list
        gap_starts = {c.kwargs["start_date"] for c in calls}
        assert "2020-01-01" in gap_starts  # backfill
        assert "2022-12-31" in gap_starts  # forward-fill
        assert mock_fmp.get_historical_daily.call_count == 3  # noqa: PLR2004


class TestCacheCoversRange:
    """Cached data fully covers the request — only a bounded drift probe runs."""

    @pytest.mark.asyncio()
    async def test_covered_range_runs_only_drift_probe(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Covered request probes once for drift; matching closes skip the refetch."""
        cached = _make_parquet_df(date(2020, 1, 2), 1826)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (date(2020, 1, 2), date(2024, 12, 31))
        # Probe returns the same (unadjusted) closes as the cache → no drift.
        mock_fmp.get_historical_daily.return_value = _make_ohlcv(date(2020, 1, 2), 1826)

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("AAPL", "2022-01-01", "2023-01-01")

        # Exactly one call — the bounded drift probe — and no full-range refetch.
        assert mock_fmp.get_historical_daily.call_count == 1
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


class TestSplitInvalidatesCache:
    """A post-cache split rebases FMP prices; the covered cache must be re-fetched."""

    @pytest.mark.asyncio()
    async def test_split_invalidates_cache(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Cached pre-split closes are replaced by post-split closes on drift."""
        pre_split_rows = _make_ohlcv(date(2023, 1, 2), 365)
        cached = _make_parquet_df(date(2023, 1, 2), 365)
        cache_start = cached["date"].min()
        cache_end = cached["date"].max()
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (cache_start, cache_end)

        split_factor = 10.0
        post_split_rows = _split_ohlcv(pre_split_rows, split_factor)
        # Same rows serve both the drift probe and the full-range refetch.
        mock_fmp.get_historical_daily.return_value = post_split_rows

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol(
            "NVDA",
            cache_start.isoformat(),
            cache_end.isoformat(),
        )

        result_close = dict(zip(result["date"].to_list(), result["close"].to_list(), strict=True))
        pre_close = {date.fromisoformat(r["date"]): r["close"] for r in pre_split_rows}
        post_close = {date.fromisoformat(r["date"]): r["close"] for r in post_split_rows}

        # Every overlapping close is the post-split value, not the stale pre-split one.
        for d, expected in post_close.items():
            assert result_close[d] == pytest.approx(expected)
        sample = next(iter(post_close))
        assert result_close[sample] == pytest.approx(pre_close[sample] / split_factor)
        assert result_close[sample] != pytest.approx(pre_close[sample])

    @pytest.mark.asyncio()
    async def test_split_invalidates_cache_partial_overlap(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Split drift on a partially-covered request refetches the full range.

        The cache holds pre-split closes for 2020-2022; the request extends to
        2024. A split rebased the whole series. The overlapping (cached) portion
        must come back post-split too, so there is no phantom gap between the
        old-cached and newly-fetched portions.
        """
        pre_split_rows = _make_ohlcv(date(2020, 1, 2), 1095)  # 2020-01-02..2022-12-31
        cached = _make_parquet_df(date(2020, 1, 2), 1095)
        cache_start = cached["date"].min()
        cache_end = cached["date"].max()
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (cache_start, cache_end)

        split_factor = 10.0
        # Provider rebased the whole series and now covers through the request end.
        post_split_rows = _split_ohlcv(_make_ohlcv(date(2020, 1, 2), 1461), split_factor)
        mock_fmp.get_historical_daily.return_value = post_split_rows

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("NVDA", "2020-01-01", "2024-01-01")

        result_close = dict(zip(result["date"].to_list(), result["close"].to_list(), strict=True))
        pre_close = {date.fromisoformat(r["date"]): r["close"] for r in pre_split_rows}
        post_close = {date.fromisoformat(r["date"]): r["close"] for r in post_split_rows}

        # The cached (overlapping) portion is now post-split — no phantom gap.
        overlap_dates = [d for d in result_close if cache_start <= d <= cache_end]
        assert overlap_dates
        for d in overlap_dates:
            assert result_close[d] == pytest.approx(post_close[d])
        sample = overlap_dates[len(overlap_dates) // 2]
        assert result_close[sample] == pytest.approx(pre_close[sample] / split_factor)
        assert result_close[sample] != pytest.approx(pre_close[sample])


class TestDividendAdjustedDaily:
    """Daily fetch must be on a total-return (dividend-adjusted) basis."""

    @pytest.mark.asyncio()
    async def test_returns_are_dividend_inclusive(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_historical_daily hits the dividend-adjusted endpoint and folds adjClose.

        FMP's dividend-adjusted endpoint returns adj-prefixed OHLC; the client must
        surface the adjusted close under ``close`` so equity/returns are on a
        total-return basis. Here the raw price is flat across the two bars but a
        dividend lifts the adjusted close from 99 to 100, so the return computed over
        the returned closes exceeds the (zero) price-only return by the dividend step.
        """
        raw_day1_close = 100.0
        adj_day1_close = 99.0
        day2_close = 100.0
        raw_rows = [
            {
                "date": "2023-01-03",
                "open": raw_day1_close,
                "high": raw_day1_close,
                "low": raw_day1_close,
                "close": raw_day1_close,
                "adjOpen": adj_day1_close,
                "adjHigh": adj_day1_close,
                "adjLow": adj_day1_close,
                "adjClose": adj_day1_close,
                "volume": 1_000_000,
            },
            {
                "date": "2023-01-04",
                "open": day2_close,
                "high": day2_close,
                "low": day2_close,
                "close": day2_close,
                "adjOpen": day2_close,
                "adjHigh": day2_close,
                "adjLow": day2_close,
                "adjClose": day2_close,
                "volume": 1_000_000,
            },
        ]
        seen: dict[str, str] = {}

        async def fake_request(endpoint: str, params: dict[str, str]) -> object:
            seen["endpoint"] = endpoint
            return raw_rows

        client = FMPClient(settings=Settings(fmp_api_key="test-key"))
        monkeypatch.setattr(client, "_request_with_retry", fake_request)
        try:
            rows = await client.get_historical_daily("KO", "2023-01-03", "2023-01-04")
        finally:
            await client.close()

        # Total-return endpoint, not the price-only "full" endpoint.
        assert seen["endpoint"] == "historical-price-eod/dividend-adjusted"

        # adjClose is folded onto close (not the raw, price-only close).
        assert rows[0]["close"] == pytest.approx(adj_day1_close)
        assert rows[1]["close"] == pytest.approx(day2_close)

        total_return = rows[1]["close"] / rows[0]["close"] - 1.0
        price_only_return = day2_close / raw_day1_close - 1.0
        assert price_only_return == pytest.approx(0.0)
        assert total_return == pytest.approx(day2_close / adj_day1_close - 1.0)
        assert total_return > price_only_return


class TestPriceBasisPurge:
    """A cache written on a superseded adjustment basis cannot be extended."""

    @pytest.mark.asyncio
    async def test_unknown_basis_purges_and_refetches_everything(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Rows predating basis tracking report None, which means unknown.

        These are the rows fetched from the raw endpoint before the daily feed
        moved to dividend-adjusted. Extending them mixes two price scales in
        one series while the answer calls it a total-return backtest.
        """
        mock_store.read_ohlcv.return_value = _make_parquet_df(date(2023, 1, 2), 252)
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 12, 29))
        mock_store.get_price_basis.return_value = None
        mock_fmp.get_historical_daily.return_value = _make_ohlcv(date(2023, 1, 2), 365)

        dl = DataDownloader(mock_fmp, mock_store)
        await dl.download_symbol("AAPL", "2023-01-01", "2024-01-01")

        mock_store.delete_symbol.assert_called_once_with("AAPL", timeframe="daily")

    @pytest.mark.asyncio
    async def test_current_basis_keeps_the_cache(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """A matching basis must not throw away a usable cache."""
        mock_store.read_ohlcv.return_value = _make_parquet_df(date(2023, 1, 2), 252)
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 12, 29))
        mock_fmp.get_historical_daily.return_value = _make_ohlcv(date(2023, 1, 2), 5)

        dl = DataDownloader(mock_fmp, mock_store)
        await dl.download_symbol("AAPL", "2023-01-01", "2023-06-01")

        mock_store.delete_symbol.assert_not_called()

    def test_stale_basis_is_not_fresh(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """The freshness path returns cached rows without ever downloading.

        If it ignored the basis, a cache younger than the freshness window
        would be served straight back and the purge would never run.
        """
        mock_store.get_date_range.return_value = (date(2023, 1, 2), date(2023, 12, 29))
        mock_store.get_last_modified.return_value = time.time()
        mock_store.get_price_basis.return_value = None

        dl = DataDownloader(mock_fmp, mock_store)

        assert not dl._is_fresh("AAPL", date(2023, 2, 1), date(2023, 3, 1), timeframe="daily")

    def test_empty_cache_is_not_treated_as_stale(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Nothing cached means nothing to purge, not a basis mismatch."""
        dl = DataDownloader(mock_fmp, mock_store)

        assert not dl._basis_is_stale("AAPL", "daily")


class TestDriftRefetchesTheWholeCachedSpan:
    """A rebasing touches every cached row, not just the requested window."""

    @pytest.mark.asyncio
    async def test_narrow_request_inside_a_large_cache_refetches_all_of_it(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """Refetching only the request leaves the rest of the cache rebased wrong.

        With 2020-2024 cached and 2022 requested, refetching 2022 alone left
        2020-2021 and 2023-2024 on the old scale. The merged series then held
        two price scales with a fabricated jump at each seam, and a later
        request landing inside the refreshed part passed the drift probe and
        backtested the mix.
        """
        cached = _make_parquet_df(date(2020, 1, 2), 1460)
        cache_start, cache_end = _bounds(cached)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (cache_start, cache_end)
        mock_fmp.get_historical_daily.return_value = _split_ohlcv(
            _make_ohlcv(date(2020, 1, 2), 1460), 10.0
        )

        dl = DataDownloader(mock_fmp, mock_store)
        await dl.download_symbol("NVDA", "2022-01-01", "2022-12-31")

        spans = [
            (call.kwargs["start_date"], call.kwargs["end_date"])
            for call in mock_fmp.get_historical_daily.call_args_list
        ]
        refetch = [s for s in spans if s[0] <= cache_start.isoformat()]
        assert refetch, f"no fetch covered the cache start; spans were {spans}"
        assert refetch[0][1] >= cache_end.isoformat()

    @pytest.mark.asyncio
    async def test_drift_drops_rows_the_provider_no_longer_returns(
        self,
        mock_fmp: AsyncMock,
        mock_store: MagicMock,
    ) -> None:
        """A surviving old-scale row would sit inside the rebased series."""
        cached = _make_parquet_df(date(2023, 1, 2), 60)
        cache_start, cache_end = _bounds(cached)
        mock_store.read_ohlcv.return_value = cached
        mock_store.get_date_range.return_value = (cache_start, cache_end)
        # The provider comes back with a shorter, rebased history.
        mock_fmp.get_historical_daily.return_value = _split_ohlcv(
            _make_ohlcv(date(2023, 1, 20), 30), 10.0
        )

        dl = DataDownloader(mock_fmp, mock_store)
        result = await dl.download_symbol("NVDA", cache_start.isoformat(), cache_end.isoformat())

        first_kept, _ = _bounds(result)
        assert first_kept >= date(2023, 1, 20)
