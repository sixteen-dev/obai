"""Market movers tool for market data."""

import asyncio
from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..clients.index_cache import get_cached_symbols, store_symbols
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_movers

logger = get_logger(__name__)

IndexName = Literal["sp500", "nasdaq", "dowjones"]

# Max symbols per batch-quote request (URL length safety)
_BATCH_SIZE = 100


async def _resolve_index_symbols(client: FMPClient, index: IndexName) -> set[str]:
    """Return symbol set for an index, using cache when possible.

    Args:
        client: FMP API client
        index: Index identifier

    Returns:
        Set of ticker symbols in the index
    """
    cached = get_cached_symbols(index)
    if cached is not None:
        return cached

    constituents = await client.get_index_constituents(index)
    return store_symbols(index, constituents)


def _sort_key(mover_type: str) -> str:
    """Return the field to sort by for each mover type.

    Args:
        mover_type: 'gainers', 'losers', or 'actives'

    Returns:
        Sort key field name
    """
    if mover_type == "actives":
        return "volume"
    return "changesPercentage"


async def _index_movers(
    client: FMPClient,
    mover_type: Literal["gainers", "losers", "actives"],
    index: IndexName,
    limit: int,
) -> dict[str, Any]:
    """Fetch movers within a specific index via batch quotes.

    Resolves index constituents (cached 24h), batch-quotes all symbols
    in parallel chunks, then sorts and returns the top N.

    Args:
        client: FMP API client
        mover_type: Type of movers (gainers, losers, actives)
        index: Index to scope results to
        limit: Max results to return

    Returns:
        Dict with mover data scoped to the index
    """
    symbols = await _resolve_index_symbols(client, index)
    if not symbols:
        return {
            "type": mover_type,
            "index": index,
            "constituents_count": 0,
            "data": [],
        }

    symbol_list = sorted(symbols)
    chunks = [symbol_list[i : i + _BATCH_SIZE] for i in range(0, len(symbol_list), _BATCH_SIZE)]

    batch_results = await asyncio.gather(*(client.batch_quote(chunk) for chunk in chunks))

    all_quotes: list[dict[str, Any]] = []
    for i, batch in enumerate(batch_results):
        logger.info(
            "batch_quote_result",
            chunk=i,
            requested=len(chunks[i]),
            returned=len(batch),
        )
        all_quotes.extend(batch)

    logger.info(
        "index_movers_total",
        index=index,
        constituents=len(symbols),
        quotes_received=len(all_quotes),
    )

    sort_field = _sort_key(mover_type)
    reverse = mover_type != "losers"
    all_quotes.sort(key=lambda q: q.get(sort_field, 0), reverse=reverse)

    top_movers = filter_movers(all_quotes[:limit])

    return {
        "type": mover_type,
        "index": index,
        "constituents_count": len(symbols),
        "data": top_movers,
    }


async def get_movers(
    mover_type: Literal["gainers", "losers", "actives"],
    index: IndexName | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Get market movers (top gainers, losers, or most active stocks).

    Without index: returns exchange-wide movers from FMP.
    With index: batch-quotes all index constituents and returns the
    top movers sorted by change % (or volume for actives).

    Args:
        mover_type: Type of movers to retrieve (gainers, losers, or actives)
        index: When provided, scopes results to the given index
            (sp500, nasdaq, dowjones) by batch-quoting all constituents
            and sorting server-side. Constituent lists are cached 24h.
        limit: Max results to return for index movers (default 20)

    Returns:
        List of stocks with price change and volume data

    Raises:
        Exception: If movers fetch fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            if index is not None:
                return await _index_movers(client, mover_type, index, limit)

            data = await client.get_stock_movers(mover_type)
            filtered_data = filter_movers(data)
            return {"type": mover_type, "data": filtered_data}
    except Exception as e:
        log_error(logger, e, context={"tool": "get_movers", "type": mover_type})
        raise
