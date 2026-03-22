"""FMP → DuckDB download pipeline with incremental updates.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 2.3.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from ..clients.fmp_client import FMPClient
from ..logging_config import get_logger
from .store import DataStore

logger = get_logger(__name__)


class DataDownloader:
    """Download OHLCV data from FMP and store via DuckDB."""

    def __init__(
        self,
        fmp_client: FMPClient,
        data_store: DataStore,
        freshness_hours: int = 24,
    ) -> None:
        """Initialize downloader.

        Args:
            fmp_client: FMP API client for fetching data.
            data_store: DuckDB-backed data store.
            freshness_hours: Hours before data is considered stale.

        """
        self.fmp_client = fmp_client
        self.data_store = data_store
        self.freshness_hours = freshness_hours

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
        if last_mod is None:
            return False
        age = datetime.now().timestamp() - last_mod
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
        existing = self.data_store.read_ohlcv(symbol, timeframe=timeframe)
        existing_range = self.data_store.get_date_range(symbol, timeframe=timeframe)
        req_start = date.fromisoformat(start_date)
        req_end = date.fromisoformat(end_date)

        # Determine which gaps need fetching
        fetches: list[tuple[str, str]] = []
        if existing is not None and not existing.is_empty() and existing_range:
            cached_start = _to_date(existing_range[0])
            cached_end = _to_date(existing_range[1])
            if req_start < cached_start:
                fetches.append((start_date, str(cached_start - timedelta(days=1))))
            if req_end > cached_end:
                fetches.append((str(cached_end + timedelta(days=1)), end_date))
        else:
            fetches.append((start_date, end_date))

        if not fetches:
            if existing is not None:
                return existing
            return _empty_df(timeframe)

        # Fetch all gaps from FMP
        new_frames: list[pl.DataFrame] = []
        for fetch_start, fetch_end in fetches:
            raw_data = await self._fetch_from_fmp(
                symbol,
                fetch_start,
                fetch_end,
                timeframe,
            )
            if raw_data:
                new_frames.append(_parse_fmp_response(raw_data, timeframe))

        if not new_frames:
            if existing is not None:
                return existing
            return _empty_df(timeframe)

        new_df = pl.concat(new_frames)

        # Merge with existing data
        if existing is not None and not existing.is_empty():
            combined = pl.concat([existing, new_df]).unique(subset=["date"])
        else:
            combined = new_df

        combined = combined.sort("date")
        await self.data_store.write_ohlcv_async(symbol, combined, timeframe=timeframe)

        logger.info(
            "symbol_downloaded",
            symbol=symbol,
            timeframe=timeframe,
            new_rows=len(new_df),
            total_rows=len(combined),
        )

        return combined

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

        Args:
            symbols: List of stock ticker symbols.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

        Returns:
            Dict mapping symbol → DataFrame.

        """
        result: dict[str, pl.DataFrame] = {}
        dt_start = date.fromisoformat(start_date)
        dt_end = date.fromisoformat(end_date)

        for symbol in symbols:
            if self._is_fresh(symbol, dt_start, dt_end, timeframe=timeframe):
                cached = self.data_store.read_ohlcv(symbol, timeframe=timeframe)
                if cached is not None:
                    result[symbol] = _slice_date_range(cached, dt_start, dt_end)
                    logger.info("data_cache_hit", symbol=symbol, timeframe=timeframe)
                    continue

            full_df = await self.download_symbol(
                symbol,
                start_date,
                end_date,
                timeframe=timeframe,
            )
            result[symbol] = _slice_date_range(full_df, dt_start, dt_end)

        return result

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
