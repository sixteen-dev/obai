"""In-memory cache for market index constituent symbols.

Uses cachetools.TTLCache (same pattern as screening-server).
Index constituents change infrequently (quarterly rebalancing),
so a 24-hour TTL avoids redundant API calls.
"""

from typing import Any

from cachetools import TTLCache

from ..logging_config import get_logger

logger = get_logger(__name__)

# 24 hours — constituents change quarterly
_CACHE_TTL_SECONDS = 86_400

# 3 indexes (sp500, nasdaq100, dowjones), small maxsize is fine
_cache: TTLCache[str, set[str]] = TTLCache(maxsize=10, ttl=_CACHE_TTL_SECONDS)


def get_cached_symbols(index: str) -> set[str] | None:
    """Return cached symbol set if still valid, else None.

    Args:
        index: Index identifier ('sp500', 'nasdaq100', 'dowjones')

    Returns:
        Cached set of ticker symbols, or None if expired/missing
    """
    return _cache.get(index)


def store_symbols(index: str, constituents: list[dict[str, Any]]) -> set[str]:
    """Extract symbols from API response and cache them.

    Args:
        index: Index identifier
        constituents: Raw API response list with 'symbol' keys

    Returns:
        Set of ticker symbols
    """
    symbols = {entry["symbol"] for entry in constituents if "symbol" in entry}
    if not symbols:
        # A transient empty/rate-limited response must not poison the 24h
        # cache — skip the write so the next call retries the fetch.
        logger.warning("index_cache_empty_not_cached", index=index)
        return symbols

    _cache[index] = symbols
    logger.info("index_cache_stored", index=index, count=len(symbols))
    return symbols


def clear_cache() -> None:
    """Clear all cached index data (useful for testing)."""
    _cache.clear()
