"""Technical analysis tools for market data."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from ..clients.finra_client import FINRAClient
from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_short_volume, filter_technical_indicators

logger = get_logger(__name__)

# Row cap keeps a single paginated response under response_utils.MAX_RESPONSE_CHARS.
# Sized for the densest supported indicator (ADX returns 3 values/row) so the
# cap holds across all indicator_type values, not just single-value ones like RSI.
MAX_LIMIT = 120

# Short volume: one FINRA request spans a year (its API keeps about that much);
# one page holds at most about three months of trading days.
SHORT_VOLUME_HISTORY_DAYS = 366
MAX_SHORT_VOLUME_DAYS = 60
_SHORT_VOLUME_SOURCE = (
    "FINRA Reg SHO daily short-sale volume: off-exchange trades reported to FINRA "
    "(TRFs and ADF) only. totalVolume is that off-exchange volume, not the "
    "consolidated volume, and this is not short interest."
)


async def get_short_volume(
    symbol: str, limit: int = 20, offset: int = 0, today: date | None = None
) -> dict[str, Any]:
    """Get daily short-sale volume from FINRA, newest trading day first.

    One FINRA request covers the last SHORT_VOLUME_HISTORY_DAYS calendar days
    (FINRA's API keeps about a year); pagination then runs over those days.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Trading days to return (default: 20, capped at MAX_SHORT_VOLUME_DAYS)
        offset: Trading days to skip (for pagination)
        today: Last date queried; defaults to the current UTC date

    Returns:
        Daily short volume data with its source and pagination metadata

    Raises:
        ValueError: If symbol is empty, limit < 1 or offset < 0
        Exception: If the FINRA request fails
    """
    if not symbol.strip():
        raise ValueError("symbol must be a non-empty ticker")
    if limit < 1 or offset < 0:
        raise ValueError(f"need limit >= 1 and offset >= 0, got limit={limit}, offset={offset}")
    ticker = symbol.strip().upper()
    end = today or datetime.now(UTC).date()
    effective_limit = min(limit, MAX_SHORT_VOLUME_DAYS)
    try:
        settings = get_settings()
        async with FINRAClient(settings) as client:
            all_data = await client.get_daily_short_volume(
                ticker, end - timedelta(days=SHORT_VOLUME_HISTORY_DAYS), end
            )

        # Apply pagination
        total_count = len(all_data)
        paginated_data = all_data[offset : offset + effective_limit]
        has_more = total_count > offset + effective_limit

        return {
            "symbol": ticker,
            "source": _SHORT_VOLUME_SOURCE,
            "data": filter_short_volume(paginated_data),
            "pagination": {
                "limit": effective_limit,
                "requested_limit": limit,
                "offset": offset,
                "returned": len(paginated_data),
                "total_count": total_count,
                "has_more": has_more,
                "next_offset": offset + effective_limit if has_more else None,
            },
        }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_short_volume",
                "symbol": symbol,
            },
        )
        raise


async def get_technical_indicators(
    symbol: str,
    indicator_type: Literal["RSI", "SMA", "EMA", "WMA", "DEMA", "TEMA", "ADX"],
    period: int = 10,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Get technical indicators for a symbol.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        indicator_type: Type of technical indicator
        period: Period for the indicator calculation (default: 10)
        limit: Maximum number of records to return (default: 50)
        offset: Number of records to skip (for pagination)

    Returns:
        Technical indicator data with pagination metadata

    Raises:
        Exception: If technical indicator fetch fails
    """
    try:
        effective_limit = min(limit, MAX_LIMIT)
        settings = get_settings()
        async with FMPClient(settings) as client:
            all_data = await client.get_technical_indicators(symbol, indicator_type, period)

            # Apply pagination
            total_count = len(all_data)
            paginated_data = all_data[offset : offset + effective_limit]
            has_more = len(all_data) > offset + effective_limit

            # Filter technical indicators (keep all indicator values)
            filtered_data = filter_technical_indicators(paginated_data)

            return {
                "symbol": symbol,
                "indicator_type": indicator_type,
                "period": period,
                "data": filtered_data,
                "pagination": {
                    "limit": effective_limit,
                    "requested_limit": limit,
                    "offset": offset,
                    "returned": len(paginated_data),
                    "total_count": total_count,
                    "has_more": has_more,
                    "next_offset": offset + effective_limit if has_more else None,
                },
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_technical_indicators",
                "symbol": symbol,
                "indicator_type": indicator_type,
            },
        )
        raise
