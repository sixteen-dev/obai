"""Historical candles tool for market data."""

from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_candles

logger = get_logger(__name__)


async def get_candles(
    symbol: str,
    interval: Literal["1min", "5min", "15min", "30min", "1hour", "4hour", "daily"],
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """Get historical price candles (OHLCV data).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        interval: Time interval (1min, 5min, 15min, 30min, 1hour, 4hour, daily)
        from_date: Start date in YYYY-MM-DD format (optional)
        to_date: End date in YYYY-MM-DD format (optional)
        limit: Maximum number of candles to return (default: 100)
        offset: Number of candles to skip (for pagination)

    Returns:
        Historical OHLCV candle data with pagination metadata

    Raises:
        Exception: If candle fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            all_data: Any
            if interval == "daily":
                all_data = await client.get_historical_daily(symbol, from_date, to_date)
            else:
                all_data = await client.get_historical_intraday(
                    symbol, interval, from_date, to_date
                )

            # Apply pagination to the data
            if isinstance(all_data, list):
                total_count = len(all_data)
                paginated_data = all_data[offset : offset + limit]
                has_more = len(all_data) > offset + limit
            else:
                # If data is not a list, return as-is (no pagination)
                total_count = 1
                paginated_data = all_data
                has_more = False

            # Filter candles data (OHLCV is all essential, but apply filter for consistency)
            filtered_data = (
                filter_candles(paginated_data)
                if isinstance(paginated_data, list)
                else paginated_data
            )

            return {
                "symbol": symbol,
                "interval": interval,
                "from_date": from_date,
                "to_date": to_date,
                "data": filtered_data,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "returned": len(paginated_data) if isinstance(paginated_data, list) else 1,
                    "total_count": total_count,
                    "has_more": has_more,
                    "next_offset": offset + limit if has_more else None,
                },
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_candles",
                "symbol": symbol,
                "interval": interval,
            },
        )
        raise
