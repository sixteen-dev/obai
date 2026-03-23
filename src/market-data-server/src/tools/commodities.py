"""Commodity discovery tools for market data."""

from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_commodities_list

logger = get_logger(__name__)


async def list_commodities() -> dict[str, Any]:
    """List available commodity symbols with display names.

    Returns:
        List of commodities with symbol and name

    Raises:
        Exception: If commodities list fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_commodities_list()
            filtered_data = filter_commodities_list(data)
            return {"data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "list_commodities"})
        raise
