"""Polars Parquet read/write for OHLCV data storage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from ..logging_config import get_logger

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
    """Read/write OHLCV data as Parquet files."""

    def __init__(self, data_dir: str) -> None:
        """Initialize data store.

        Args:
            data_dir: Directory path for Parquet file storage.

        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _symbol_path(self, symbol: str) -> Path:
        """Get Parquet file path for a symbol."""
        return self.data_dir / f"{symbol.upper()}.parquet"

    def read_ohlcv(self, symbol: str) -> pl.DataFrame | None:
        """Read OHLCV data for a symbol from Parquet.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            DataFrame with OHLCV data sorted by date, or None if not found.

        """
        path = self._symbol_path(symbol)
        if not path.exists():
            return None
        try:
            df = pl.read_parquet(path)
            return df.sort("date")
        except Exception as exc:
            logger.warning("parquet_read_failed", symbol=symbol, error=str(exc))
            path.unlink(missing_ok=True)
            return None

    def write_ohlcv(self, symbol: str, df: pl.DataFrame) -> None:
        """Write OHLCV data for a symbol to Parquet.

        Args:
            symbol: Stock ticker symbol.
            df: DataFrame with OHLCV data.

        """
        path = self._symbol_path(symbol)
        df.sort("date").write_parquet(path)
        logger.info("parquet_written", symbol=symbol, rows=len(df), path=str(path))

    def get_date_range(self, symbol: str) -> tuple[date, date] | None:
        """Get the min/max date range stored for a symbol.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            Tuple of (start_date, end_date) or None if no data.

        """
        df = self.read_ohlcv(symbol)
        if df is None or df.is_empty():
            return None
        min_val = df["date"].min()
        max_val = df["date"].max()
        if not isinstance(min_val, date) or not isinstance(max_val, date):
            return None
        return (min_val, max_val)

    def list_available_symbols(self) -> list[str]:
        """List all symbols with stored Parquet data.

        Returns:
            Sorted list of symbol strings.

        """
        return sorted(p.stem for p in self.data_dir.glob("*.parquet"))

    def get_last_modified(self, symbol: str) -> float | None:
        """Get last modification timestamp for a symbol's Parquet file.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            Unix timestamp of last modification, or None if not found.

        """
        path = self._symbol_path(symbol)
        if not path.exists():
            return None
        return path.stat().st_mtime
