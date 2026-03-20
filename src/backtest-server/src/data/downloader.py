"""FMP → Parquet download pipeline with incremental updates."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl

from ..clients.fmp_client import FMPClient
from ..logging_config import get_logger
from .store import DataStore

logger = get_logger(__name__)


class DataDownloader:
    """Download OHLCV data from FMP and store as Parquet."""

    def __init__(
        self,
        fmp_client: FMPClient,
        data_store: DataStore,
        freshness_hours: int = 24,
    ) -> None:
        """Initialize downloader.

        Args:
            fmp_client: FMP API client for fetching data.
            data_store: Parquet data store for reading/writing.
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
    ) -> bool:
        """Check if stored data is fresh and covers the required range.

        Args:
            symbol: Stock ticker symbol.
            required_start: Required start date (inclusive).
            required_end: Required end date (inclusive).

        Returns:
            True if data exists, is recent, and covers the required range.

        """
        last_mod = self.data_store.get_last_modified(symbol)
        if last_mod is None:
            return False
        age = datetime.now().timestamp() - last_mod
        if age >= (self.freshness_hours * 3600):
            return False

        # Check date coverage if required range specified
        if required_start is not None or required_end is not None:
            existing_range = self.data_store.get_date_range(symbol)
            if existing_range is None:
                return False
            if required_start and existing_range[0] > required_start:
                return False
            if required_end and existing_range[1] < required_end:
                return False

        return True

    async def download_symbol(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """Download OHLCV data for a symbol, merging with existing data.

        Args:
            symbol: Stock ticker symbol.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            Complete DataFrame for the requested date range.

        """
        existing = self.data_store.read_ohlcv(symbol)
        existing_range = self.data_store.get_date_range(symbol)
        req_start = date.fromisoformat(start_date)
        req_end = date.fromisoformat(end_date)

        # Determine which gaps need fetching
        fetches: list[tuple[str, str]] = []
        if existing is not None and not existing.is_empty() and existing_range:
            cached_start, cached_end = existing_range
            # Backfill: need earlier data
            if req_start < cached_start:
                fetches.append((
                    start_date,
                    str(cached_start - timedelta(days=1)),
                ))
            # Forward-fill: need later data
            if req_end > cached_end:
                fetches.append((
                    str(cached_end + timedelta(days=1)),
                    end_date,
                ))
        else:
            fetches.append((start_date, end_date))

        if not fetches:
            if existing is not None:
                return existing
            return pl.DataFrame(schema={"date": pl.Date})

        # Fetch all gaps from FMP
        new_frames: list[pl.DataFrame] = []
        for fetch_start, fetch_end in fetches:
            raw_data = await self.fmp_client.get_historical_daily(
                symbol=symbol,
                start_date=fetch_start,
                end_date=fetch_end,
            )
            if raw_data:
                new_frames.append(
                    pl.DataFrame(raw_data).with_columns(
                        pl.col("date").str.to_date().alias("date"),
                        pl.col("volume").cast(pl.Int64),
                    )
                )

        if not new_frames:
            if existing is not None:
                return existing
            return pl.DataFrame(schema={"date": pl.Date})

        new_df = pl.concat(new_frames)

        # Merge with existing data
        if existing is not None and not existing.is_empty():
            combined = pl.concat([existing, new_df]).unique(subset=["date"])
        else:
            combined = new_df

        combined = combined.sort("date")
        self.data_store.write_ohlcv(symbol, combined)

        logger.info(
            "symbol_downloaded",
            symbol=symbol,
            new_rows=len(new_df),
            total_rows=len(combined),
        )

        return combined

    def count_stale(self, symbols: list[str]) -> int:
        """Count symbols that would need re-downloading.

        Args:
            symbols: List of stock ticker symbols.

        Returns:
            Number of symbols with stale or missing data.

        """
        return sum(1 for s in symbols if not self._is_fresh(s))

    async def ensure_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, pl.DataFrame]:
        """Ensure data exists for all symbols, downloading if needed.

        Args:
            symbols: List of stock ticker symbols.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            Dict mapping symbol → DataFrame.

        """
        result: dict[str, pl.DataFrame] = {}
        dt_start = date.fromisoformat(start_date)
        dt_end = date.fromisoformat(end_date)

        for symbol in symbols:
            if self._is_fresh(symbol, dt_start, dt_end):
                cached = self.data_store.read_ohlcv(symbol)
                if cached is not None:
                    result[symbol] = _slice_date_range(cached, dt_start, dt_end)
                    logger.info("data_cache_hit", symbol=symbol)
                    continue

            full_df = await self.download_symbol(symbol, start_date, end_date)
            result[symbol] = _slice_date_range(full_df, dt_start, dt_end)

        return result


def _slice_date_range(
    df: pl.DataFrame,
    start: date,
    end: date,
) -> pl.DataFrame:
    """Filter DataFrame to the requested date range (inclusive).

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
