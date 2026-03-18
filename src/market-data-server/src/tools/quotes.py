"""Quote tools for market data."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_quote, filter_quote_short

logger = get_logger(__name__)


async def get_quote(symbol: str) -> dict[str, Any]:
    """Get full real-time quote with OHLCV data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Real-time quote with price, change, volume, and market data

    Raises:
        Exception: If quote fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_quote(symbol)
            # Filter out unnecessary fields to reduce token usage
            filtered_data = filter_quote(data)
            return {"symbol": symbol, "data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_quote", "symbol": symbol})
        raise


async def get_latest_trade(symbol: str) -> dict[str, Any]:
    """Get fast price snapshot (condensed quote).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Minimal quote data with current price and volume

    Raises:
        Exception: If quote fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_quote_short(symbol)
            # Filter unnecessary fields
            filtered_data = filter_quote_short(data)
            return {"symbol": symbol, "data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_latest_trade", "symbol": symbol})
        raise
