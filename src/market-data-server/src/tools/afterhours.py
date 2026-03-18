"""After-hours quote tool for market data."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_afterhours_quote

logger = get_logger(__name__)


async def get_afterhours_quote(symbol: str) -> dict[str, Any]:
    """Get pre-market and after-hours quote data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Pre-market and after-hours price data

    Raises:
        Exception: If afterhours quote fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_afterhours_quote(symbol)
            # Filter after-hours data (minimal, but apply for consistency)
            filtered_data = filter_afterhours_quote(data)
            return {"symbol": symbol, "data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_afterhours_quote", "symbol": symbol})
        raise
