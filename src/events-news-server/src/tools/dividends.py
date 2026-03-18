"""Dividends tools for dividend history by ticker."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_dividends

logger = get_logger(__name__)


async def get_dividends(
    symbol: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Get dividend history for a specific ticker.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Maximum number of dividend records to return (default: 10)

    Returns:
        Dividend records with metadata

    Raises:
        Exception: If dividends fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_dividends(symbol, limit)
            # Filter dividends to essential fields
            filtered_data = filter_dividends(data)
            return {
                "symbol": symbol,
                "count": len(filtered_data),
                "dividends": filtered_data,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_dividends",
                "symbol": symbol,
                "limit": limit,
            },
        )
        raise
