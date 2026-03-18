"""Market movers tool for market data."""

from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_movers

logger = get_logger(__name__)


async def get_movers(mover_type: Literal["gainers", "losers", "actives"]) -> dict[str, Any]:
    """Get market movers (top gainers, losers, or most active stocks).

    Args:
        mover_type: Type of movers to retrieve (gainers, losers, or actives)

    Returns:
        List of stocks with price change and volume data

    Raises:
        Exception: If movers fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_stock_movers(mover_type)
            # Filter to essential fields to reduce token usage
            filtered_data = filter_movers(data)
            return {"type": mover_type, "data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_movers", "type": mover_type})
        raise
