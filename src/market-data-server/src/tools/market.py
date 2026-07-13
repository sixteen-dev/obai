"""Market overview tools for market data."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_market_open, filter_sector_performance

logger = get_logger(__name__)

# US equity venues relevant to this product; foreign exchanges are dropped
# so callers see only the sessions that govern US ticker quotes.
_US_EXCHANGES = frozenset({"NASDAQ", "NYSE"})


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
    """Check if the US market is currently open.

    Returns market hours and open/closed status for the US equity venues
    (NASDAQ, NYSE); foreign exchanges are dropped to avoid dumping every
    global exchange into context.

    Returns:
        Market hours and open status for the US exchanges.

    Raises:
        Exception: If market status fetch fails.
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.is_market_open()
            us_data = [row for row in data if row.get("exchange") in _US_EXCHANGES]
            filtered_data = filter_market_open(us_data)
            return {"data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "is_market_open"})
        raise
