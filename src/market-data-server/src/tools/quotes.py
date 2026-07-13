"""Quote tools for market data."""

from datetime import UTC, datetime
from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_quote, filter_quote_short

logger = get_logger(__name__)


def _as_of_from_quote(data: list[dict[str, Any]]) -> str | None:
    """Return the provider quote timestamp as a UTC ISO string, if present.

    FMP's full quote carries a Unix-epoch ``timestamp``; surface it as the
    native as-of time. Returns None when no usable timestamp is present so
    callers never fabricate an as-of they do not have.

    Args:
        data: Filtered quote rows (single-symbol quote is a one-element list)

    Returns:
        UTC ISO timestamp of the provider quote, or None when unavailable
    """
    if not data:
        return None
    ts = data[0].get("timestamp")
    if not isinstance(ts, int | float) or isinstance(ts, bool):
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


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
            result: dict[str, Any] = {
                "symbol": symbol,
                "data": filtered_data,
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
            as_of = _as_of_from_quote(filtered_data)
            if as_of is not None:
                result["as_of"] = as_of
            return result
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
            # quote-short carries no provider timestamp; retrieved_at is the
            # honest floor so the specialist can still time-stamp the price.
            return {
                "symbol": symbol,
                "data": filtered_data,
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
    except Exception as e:
        log_error(logger, e, context={"tool": "get_latest_trade", "symbol": symbol})
        raise
