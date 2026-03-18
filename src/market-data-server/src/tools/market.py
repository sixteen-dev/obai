"""Market overview tools for market data."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_market_open, filter_sector_performance

logger = get_logger(__name__)


async def get_market_snapshot() -> dict[str, Any]:
    """Get market sector performance overview.

    Returns:
        Sector performance data with price changes

    Raises:
        Exception: If sector performance fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_sector_performance()
            # Filter sector performance (already minimal, but apply for consistency)
            filtered_data = filter_sector_performance(data)
            return {"data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_market_snapshot"})
        raise


async def is_market_open() -> dict[str, Any]:
    """Check if the market is currently open.

    Returns all exchange market hours with open/closed status.

    Returns:
        Market hours and open status for all exchanges.

    Raises:
        Exception: If market status fetch fails.
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.is_market_open()
            filtered_data = filter_market_open(data)
            return {"data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "is_market_open"})
        raise
