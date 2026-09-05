"""FMP → DuckDB download pipeline with incremental updates.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 2.3.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from ..clients.fmp_client import FMPClient, price_basis_for
from ..logging_config import get_logger
from .store import DataStore

logger = get_logger(__name__)

# A provider re-adjustment (e.g. a post-cache split) that rebases the whole
# split-adjusted series shifts every cached close by the split factor. A shared
# date whose close moves more than this fraction is treated as drifted.
_ADJUSTMENT_DRIFT_TOLERANCE = 0.005
# Calendar days probed back from the cache's most recent covered date so the
# single bounded probe request reliably overlaps at least one cached trading bar.
_DRIFT_PROBE_WINDOW_DAYS = 7


class DataDownloader:
    """Download OHLCV data from FMP and store via DuckDB."""

    def __init__(
        self,
        fmp_client: FMPClient,
        data_store: DataStore,
        freshness_hours: int = 24,
        max_concurrent_downloads: int = 5,
    ) -> None:
        """Initialize downloader.

        Args:
            fmp_client: FMP API client for fetching data.
            data_store: DuckDB-backed data store.
            freshness_hours: Hours before data is considered stale.
            max_concurrent_downloads: Max parallel FMP requests.

        """
        self.fmp_client = fmp_client
        self.data_store = data_store
        self.freshness_hours = freshness_hours
        self._semaphore = asyncio.Semaphore(max_concurrent_downloads)

    def _is_fresh(
        self,
        symbol: str,
        required_start: date | None = None,
        required_end: date | None = None,
        timeframe: str = "daily",
    ) -> bool:
        """Check if stored data is fresh and covers the required range.

        Args:
            symbol: Stock ticker symbol.
            required_start: Required start date (inclusive).
            required_end: Required end date (inclusive).
            timeframe: Bar timeframe.

        Returns:
            True if data exists, is recent, and covers the required range.

        """
        last_mod = self.data_store.get_last_modified(symbol, timeframe=timeframe)
        if last_mod is None or self._basis_is_stale(symbol, timeframe):
            return False
        age = datetime.now(UTC).timestamp() - last_mod
        if age >= (self.freshness_hours * 3600):
            return False

        # Check date coverage if required range specified
        if required_start is not None or required_end is not None:
            existing_range = self.data_store.get_date_range(symbol, timeframe=timeframe)
            if existing_range is None:
                return False
            # Compare as date objects (cast datetime→date for comparison)
            cached_start = _to_date(existing_range[0])
            cached_end = _to_date(existing_range[1])
            if required_start and cached_start > required_start:
                return False
            if required_end and cached_end < required_end:
                return False

        return True

    def _basis_is_stale(self, symbol: str, timeframe: str) -> bool:
        """Report whether cached rows sit on a different adjustment basis.

        Prices fetched on one basis are not comparable with prices fetched on
        another, so a cache written before the basis changed has to be
        refetched rather than extended. Rows predating basis tracking report
        None, which counts as stale precisely because their basis is unknown.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.

        Returns:
            True when something is cached and its basis is not the current one.

        """
        if self.data_store.get_date_range(symbol, timeframe=timeframe) is None:
            return False
        stored = self.data_store.get_price_basis(symbol, timeframe=timeframe)
        return stored != price_basis_for(timeframe)

    async def download_symbol(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "daily",
    ) -> pl.DataFrame:
        """Download OHLCV data for a symbol, merging with existing data.

        Args:
            symbol: Stock ticker symbol.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

        Returns:
            Complete DataFrame for the requested date range.

        """
        if self._basis_is_stale(symbol, timeframe):
            removed = self.data_store.delete_symbol(symbol, timeframe=timeframe)
            logger.warning(
                "price_basis_cache_purged",
                symbol=symbol,
                timeframe=timeframe,
                rows=removed,
                basis=price_basis_for(timeframe),
            )

        existing = self.data_store.read_ohlcv(symbol, timeframe=timeframe)
        existing_range = self.data_store.get_date_range(symbol, timeframe=timeframe)
        req_start = date.fromisoformat(start_date)
        req_end = date.fromisoformat(end_date)

        fetches, anchor = _plan_cache_fetches(existing, existing_range, req_start, req_end)

        # Cache overlaps the request: probe once for a provider re-adjustment
        # (e.g. a post-cache split) that rebased the whole cached series. The
        # rebasing applies to every cached row, not only the ones this request
        # happens to span, so the refetch covers the union of the cache and the
        # request and the stale rows are dropped rather than merged -- keeping
        # them would join two price scales into one series with a fabricated
        # jump at the seam.
        drift = False
        if anchor is not None and existing is not None:
            drift = await self._detect_adjustment_drift(existing, symbol, timeframe, anchor)
            if drift:
                fetches = [_full_refresh_span(existing_range, req_start, req_end)]

        if not fetches:
            if existing is not None:
                return existing
            return _empty_df(timeframe)

        # Fetch all gaps (or the full range on drift) from FMP
        new_frames: list[pl.DataFrame] = []
        for fetch_start, fetch_end in fetches:
            raw_data = await self._fetch_from_fmp(symbol, fetch_start, fetch_end, timeframe)
            if raw_data:
                new_frames.append(_parse_fmp_response(raw_data, timeframe))

        if not new_frames:
            if existing is not None:
                return existing
            return _empty_df(timeframe)

        new_df = pl.concat(new_frames)
        combined = _merge_frames(existing, new_df, drift=drift).sort("date")
        await self.data_store.write_ohlcv_async(symbol, combined, timeframe=timeframe)

        logger.info(
            "symbol_downloaded",
            symbol=symbol,
            timeframe=timeframe,
            new_rows=len(new_df),
            total_rows=len(combined),
        )

        return combined

    async def _detect_adjustment_drift(
        self,
        existing: pl.DataFrame,
        symbol: str,
        timeframe: str,
        anchor_date: date,
    ) -> bool:
        """Probe once for a provider re-adjustment of the cached series.

        A corporate action that retroactively rebased FMP's split-adjusted
        prices (e.g. a post-cache split) shifts every cached close by the split
        factor, so a gap-only fetch would leave the overlapping cached rows on
        a different scale from the freshly fetched rows (a phantom gap). This
        fetches one small recent window (bounded to ``_DRIFT_PROBE_WINDOW_DAYS``
        ending at ``anchor_date``, the most recent cached date overlapping the
        request) and compares the provider's current close to the cached close
        on the most recent shared date.

        Only daily bars are probed: the rebasing affects the daily
        split-adjusted EOD series, and the probe uses the daily endpoint.

        Args:
            existing: Cached OHLCV frame (non-empty, overlaps the request).
            symbol: Stock ticker symbol.
            timeframe: Bar timeframe.
            anchor_date: Most recent cached date overlapping the request; the
                probe window ends here so it lands on cached bars.

        Returns:
            True when cached and freshly fetched closes diverge beyond
            ``_ADJUSTMENT_DRIFT_TOLERANCE`` on a shared date; False when there is
            no shared date to compare (drift cannot be proven).

        """
        if timeframe != "daily":
            return False

        probe_start = anchor_date - timedelta(days=_DRIFT_PROBE_WINDOW_DAYS)
        raw = await self.fmp_client.get_historical_daily(
            symbol=symbol,
            start_date=probe_start.isoformat(),
            end_date=anchor_date.isoformat(),
        )
        if not raw:
            return False
        fresh = _parse_fmp_response(raw, timeframe)
        return _closes_diverge(existing, fresh, _ADJUSTMENT_DRIFT_TOLERANCE)

    def count_stale(
        self,
        symbols: list[str],
        timeframe: str = "daily",
    ) -> int:
        """Count symbols that would need re-downloading.

        Args:
            symbols: List of stock ticker symbols.
            timeframe: Bar timeframe.

        Returns:
            Number of symbols with stale or missing data.

        """
        return sum(1 for s in symbols if not self._is_fresh(s, timeframe=timeframe))

    async def ensure_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        timeframe: str = "daily",
    ) -> dict[str, pl.DataFrame]:
        """Ensure data exists for all symbols, downloading if needed.

        Downloads are run concurrently with a semaphore to respect FMP rate
        limits. Cached symbols are resolved immediately without consuming a
        semaphore slot.

        Args:
            symbols: List of stock ticker symbols.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

        Returns:
            Dict mapping symbol → DataFrame.

        """
        dt_start = date.fromisoformat(start_date)
        dt_end = date.fromisoformat(end_date)

        async def _fetch_one(symbol: str) -> tuple[str, pl.DataFrame]:
            if self._is_fresh(symbol, dt_start, dt_end, timeframe=timeframe):
                cached = self.data_store.read_ohlcv(symbol, timeframe=timeframe)
                if cached is not None:
                    logger.info("data_cache_hit", symbol=symbol, timeframe=timeframe)
                    return symbol, _slice_date_range(cached, dt_start, dt_end)

            async with self._semaphore:
                full_df = await self.download_symbol(
                    symbol,
                    start_date,
                    end_date,
                    timeframe=timeframe,
                )
            return symbol, _slice_date_range(full_df, dt_start, dt_end)

        pairs = await asyncio.gather(*[_fetch_one(s) for s in symbols])
        return dict(pairs)

    async def _fetch_from_fmp(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> list[dict[str, Any]]:
        """Fetch data from FMP, routing to daily or intraday endpoint.

        Args:
            symbol: Stock ticker symbol.
            start_date: Start date string.
            end_date: End date string.
            timeframe: Bar timeframe.

        Returns:
            Raw FMP response data.

        """
        if timeframe == "daily":
            return await self.fmp_client.get_historical_daily(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
        return await self.fmp_client.get_historical_intraday(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )


def _to_date(val: date | datetime) -> date:
    """Convert a date or datetime to a plain date."""
    if isinstance(val, datetime):
        return val.date()
    return val


def _full_refresh_span(
    existing_range: tuple[date, date] | tuple[datetime, datetime] | None,
    req_start: date,
    req_end: date,
) -> tuple[str, str]:
    """Return the span to refetch when the provider has re-adjusted the series.

    The rebasing touches every cached row, so refetching only the requested
    window would leave the rest of the cache on the old scale. The span is the
    union of the cache and the request.

    Args:
        existing_range: Cached (start, end) range, or None when absent.
        req_start: Requested start date (inclusive).
        req_end: Requested end date (inclusive).

    Returns:
        An ISO ``(start, end)`` pair covering both the cache and the request.

    """
    if existing_range is None:
        return req_start.isoformat(), req_end.isoformat()
    cached_start = _to_date(existing_range[0])
    cached_end = _to_date(existing_range[1])
    return min(cached_start, req_start).isoformat(), max(cached_end, req_end).isoformat()


def _plan_cache_fetches(
    existing: pl.DataFrame | None,
    existing_range: tuple[date, date] | tuple[datetime, datetime] | None,
    req_start: date,
    req_end: date,
) -> tuple[list[tuple[str, str]], date | None]:
    """Plan the gap fetches and the drift-probe anchor for a request.

    Args:
        existing: Cached OHLCV frame, or None when absent.
        existing_range: Cached (start, end) range, or None when absent.
        req_start: Requested start date (inclusive).
        req_end: Requested end date (inclusive).

    Returns:
        A ``(fetches, anchor)`` pair. ``fetches`` is the full requested range
        when there is no usable cache, otherwise only the missing head/tail
        segments (ISO date strings), empty when the cache fully covers the
        request. ``anchor`` is the most recent cached date that overlaps the
        request (always a cached date), or None when the cache does not overlap
        the request — the fast path that skips the probe.

    """
    if existing is None or existing.is_empty() or existing_range is None:
        return [(req_start.isoformat(), req_end.isoformat())], None

    cached_start = _to_date(existing_range[0])
    cached_end = _to_date(existing_range[1])
    fetches: list[tuple[str, str]] = []
    if req_start < cached_start:
        fetches.append((req_start.isoformat(), (cached_start - timedelta(days=1)).isoformat()))
    if req_end > cached_end:
        fetches.append(((cached_end + timedelta(days=1)).isoformat(), req_end.isoformat()))

    anchor = None
    if cached_start <= req_end and cached_end >= req_start:
        anchor = min(cached_end, req_end)
    return fetches, anchor


def _merge_frames(
    existing: pl.DataFrame | None,
    new_df: pl.DataFrame,
    *,
    drift: bool,
) -> pl.DataFrame:
    """Merge cached and freshly fetched rows, deduplicating by date.

    On adjustment drift the freshly fetched rows replace the cache entirely,
    because every cached row sat on the superseded scale. Otherwise the
    original dedup (existing rows retained) is kept.

    Args:
        existing: Cached OHLCV frame, or None when absent.
        new_df: Freshly fetched rows.
        drift: True when the cached series was re-adjusted and must be replaced.

    Returns:
        Deduplicated combined DataFrame (unsorted).

    """
    if existing is None or existing.is_empty():
        return new_df
    if drift:
        # Every cached row sat on the old scale and the refetch spans the whole
        # cache, so the fresh rows replace it outright. Keeping a date the
        # provider no longer returns would leave one bar of the old scale
        # inside the rebased series.
        return new_df
    return pl.concat([existing, new_df]).unique(subset=["date"])


def _closes_diverge(
    cached: pl.DataFrame,
    fresh: pl.DataFrame,
    tolerance: float,
) -> bool:
    """Compare cached vs fresh closes on the most recent shared date.

    Args:
        cached: Existing cached OHLCV frame.
        fresh: Freshly fetched probe frame.
        tolerance: Max allowed relative close difference before flagging drift.

    Returns:
        True when a shared date exists and its cached/fresh closes differ by
        more than ``tolerance`` (relative); False otherwise.

    """
    shared = cached.join(
        fresh.select(["date", "close"]),
        on="date",
        how="inner",
        suffix="_fresh",
    )
    if shared.is_empty():
        return False

    latest = shared.sort("date").tail(1)
    cached_close = float(latest["close"][0])
    fresh_close = float(latest["close_fresh"][0])
    if cached_close == 0.0:
        return False
    return abs(fresh_close - cached_close) / abs(cached_close) > tolerance


def _parse_fmp_response(
    raw_data: list[dict[str, Any]],
    timeframe: str,
) -> pl.DataFrame:
    """Parse FMP response into a Polars DataFrame.

    Args:
        raw_data: List of OHLCV dicts from FMP.
        timeframe: Bar timeframe (determines date parsing).

    Returns:
        DataFrame with date column and OHLCV data.

    """
    df = pl.DataFrame(raw_data)
    try:
        if timeframe == "daily":
            return df.with_columns(
                pl.col("date").str.to_date().alias("date"),
                pl.col("volume").cast(pl.Int64),
            )
        # Intraday: parse datetime strings
        return df.with_columns(
            pl.col("date").str.to_datetime("%Y-%m-%d %H:%M:%S").alias("date"),
            pl.col("volume").cast(pl.Int64),
        )
    except pl.exceptions.ComputeError as exc:
        sample = df["date"].head(3).to_list() if "date" in df.columns else []
        msg = f"Failed to parse FMP {timeframe} dates (sample: {sample}): {exc}"
        raise ValueError(msg) from exc


def _empty_df(timeframe: str) -> pl.DataFrame:
    """Create an empty DataFrame with the correct date column type.

    Args:
        timeframe: Bar timeframe.

    Returns:
        Empty DataFrame with appropriate schema.

    """
    if timeframe == "daily":
        return pl.DataFrame(schema={"date": pl.Date})
    return pl.DataFrame(schema={"date": pl.Datetime})


def _slice_date_range(
    df: pl.DataFrame,
    start: date,
    end: date,
) -> pl.DataFrame:
    """Filter DataFrame to the requested date range (inclusive).

    For intraday data, includes all bars on the start and end dates.

    Args:
        df: DataFrame with a "date" column.
        start: Start date (inclusive).
        end: End date (inclusive).

    Returns:
        Filtered DataFrame.

    """
    if df.is_empty() or "date" not in df.columns:
        return df
    return df.filter(pl.col("date").is_between(start, end))
