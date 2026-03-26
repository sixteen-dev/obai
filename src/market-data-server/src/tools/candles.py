"""Historical candles tool for market data."""

from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_candles

logger = get_logger(__name__)


def _extract_candles_list(raw_data: Any) -> list[dict[str, Any]]:
    """Extract the candles list from FMP daily endpoint response.

    FMP's historical-price-eod/full returns {"symbol": "...", "historical": [...]}.
    This extracts the list so pagination and filtering work consistently.

    Args:
        raw_data: Raw response from FMP daily endpoint.

    Returns:
        List of candle dicts.
    """
    if isinstance(raw_data, list):
        return raw_data
    if isinstance(raw_data, dict):
        historical = raw_data.get("historical")
        if isinstance(historical, list):
            return historical
    logger.warning("unexpected_daily_response_format", data_type=type(raw_data).__name__)
    return []


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
            if interval == "daily":
                raw_data = await client.get_historical_daily(symbol, from_date, to_date)
                # FMP daily endpoint returns {"symbol": "...", "historical": [...]}
                # Extract the candles list for consistent pagination
                candles = _extract_candles_list(raw_data)
            else:
                candles = await client.get_historical_intraday(symbol, interval, from_date, to_date)

            # Apply pagination
            total_count = len(candles)
            paginated_data = candles[offset : offset + limit]
            has_more = total_count > offset + limit

            # Filter candles data
            filtered_data = filter_candles(paginated_data)

            return {
                "symbol": symbol,
                "interval": interval,
                "from_date": from_date,
                "to_date": to_date,
                "data": filtered_data,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "returned": len(paginated_data),
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
