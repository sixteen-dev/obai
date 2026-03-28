"""Earnings tools for earnings data by ticker symbol."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_earnings

logger = get_logger(__name__)

_OVERFETCH_BUFFER = 10


async def get_earnings(
    symbol: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Get earnings history and upcoming earnings for a specific ticker.

    Over-fetches from FMP to ensure both past reported and upcoming
    estimated earnings are included even when limit is small.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Maximum number of earnings records to return (default: 5)

    Returns:
        Earnings records with metadata.

    Raises:
        Exception: If earnings fetch fails
    """
    try:
        settings = get_settings()
        fetch_limit = limit + _OVERFETCH_BUFFER
        async with FMPClient(settings) as client:
            data = await client.get_earnings(symbol, fetch_limit)
        filtered_data = filter_earnings(data)
        return {
            "symbol": symbol,
            "count": len(filtered_data[:limit]),
            "earnings": filtered_data[:limit],
        }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_earnings",
                "symbol": symbol,
                "limit": limit,
            },
        )
        raise
