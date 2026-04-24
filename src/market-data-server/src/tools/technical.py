"""Technical analysis tools for market data."""

from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_short_volume, filter_technical_indicators

logger = get_logger(__name__)

# Row cap keeps a single paginated response under response_utils.MAX_RESPONSE_CHARS.
# Sized for the densest supported indicator (ADX returns 3 values/row) so the
# cap holds across all indicator_type values, not just single-value ones like RSI.
MAX_LIMIT = 120


async def get_short_volume(symbol: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Get historical short sale volume data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Maximum number of records to return (default: 50)
        offset: Number of records to skip (for pagination)

    Returns:
        Historical short volume data with pagination metadata

    Raises:
        Exception: If short volume fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            all_data = await client.get_short_volume(symbol)

            # Apply pagination
            total_count = len(all_data)
            paginated_data = all_data[offset : offset + limit]
            has_more = len(all_data) > offset + limit

            # Filter short volume data to essential fields
            filtered_data = filter_short_volume(paginated_data)

            return {
                "symbol": symbol,
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
