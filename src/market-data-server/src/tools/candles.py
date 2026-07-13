"""Historical candles tool for market data."""

from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_candles

logger = get_logger(__name__)

# Row cap keeps a single paginated response under response_utils.MAX_RESPONSE_CHARS.
# Sized from ~178 chars/row observed for daily OHLCV.
MAX_LIMIT = 130


def _extract_candles_list(raw_data: Any) -> list[dict[str, Any]]:
    """Extract the candles list from FMP daily endpoint response.

    FMP's daily EOD endpoint returns either a flat list (stable API) or a
    legacy {"symbol": "...", "historical": [...]} dict. This extracts the
    list so pagination and filtering work consistently.

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
    """Get historical price candles (OHLCV data), returned oldest-first.

    Daily candles are split- and dividend-adjusted (total-return basis) so
    long-horizon returns include reinvested dividends; intraday candles are
    raw prices.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        interval: Time interval (1min, 5min, 15min, 30min, 1hour, 4hour, daily)
        from_date: Start date in YYYY-MM-DD format (optional)
        to_date: End date in YYYY-MM-DD format (optional)
        limit: Maximum number of candles to return (default: 30, max: 130).
            Requests above 130 are clamped; pagination metadata echoes both
            the requested and effective limit.
        offset: Number of candles to skip. Candles are oldest-first, so a
            higher offset pages forward in time.

    Returns:
        Historical OHLCV candle data with pagination metadata

    Raises:
        Exception: If candle fetch fails
    """
    try:
        # Clamp lower bounds — negative limit/offset would silently produce
        # surprising reverse slices instead of a clear error.
        effective_limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        settings = get_settings()
        async with FMPClient(settings) as client:
            if interval == "daily":
                raw_data = await client.get_historical_daily(symbol, from_date, to_date)
                # FMP daily endpoint returns {"symbol": "...", "historical": [...]}
                # Extract the candles list for consistent pagination
                candles = _extract_candles_list(raw_data)
            else:
                candles = await client.get_historical_intraday(symbol, interval, from_date, to_date)

            # FMP returns candles newest-first, so raw slicing would page
            # `offset` backward in time. Normalize to oldest-first so `offset`
            # pages forward and the returned order matches the documented contract.
            candles = sorted(candles, key=lambda row: row.get("date", ""))

            # Apply pagination
            total_count = len(candles)
            paginated_data = candles[offset : offset + effective_limit]
            has_more = total_count > offset + effective_limit

            # Filter candles data
            filtered_data = filter_candles(paginated_data)

            return {
                "symbol": symbol,
                "interval": interval,
                "from_date": from_date,
                "to_date": to_date,
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
                "tool": "get_candles",
                "symbol": symbol,
                "interval": interval,
            },
        )
        raise
