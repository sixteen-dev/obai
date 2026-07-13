"""Market movers tool for market data."""

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from ..clients.fmp_client import FMPClient
from ..clients.index_cache import get_cached_symbols, store_symbols
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_movers

logger = get_logger(__name__)

IndexName = Literal["sp500", "nasdaq100", "dowjones"]

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
        # An empty constituent set means the provider returned nothing (not a
        # genuine "no movers"). Disclose it and do not present it as authoritative;
        # the empty set is not cached, so a subsequent call retries the fetch.
        logger.warning("index_constituents_unavailable", index=index)
        return {
            "type": mover_type,
            "index": index,
            "constituents_count": 0,
            "data": [],
            "retrieved_at": datetime.now(UTC).isoformat(),
            "warnings": [
                f"Constituent list for '{index}' was unavailable; "
                "results are incomplete and will be retried on the next request."
            ],
        }

    symbol_list = sorted(symbols)
    chunks = [symbol_list[i : i + _BATCH_SIZE] for i in range(0, len(symbol_list), _BATCH_SIZE)]

    # `return_exceptions=True` keeps a single provider hiccup from killing
    # the whole index mover response — bad chunks are logged and skipped.
    batch_results = await asyncio.gather(
        *(client.batch_quote(chunk) for chunk in chunks),
        return_exceptions=True,
    )

    all_quotes: list[dict[str, Any]] = []
    failed_chunks = 0
    for i, batch in enumerate(batch_results):
        if isinstance(batch, BaseException):
            failed_chunks += 1
            logger.warning(
                "batch_quote_chunk_failed",
                chunk=i,
                requested=len(chunks[i]),
                error=str(batch),
                error_type=type(batch).__name__,
            )
            continue
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
        failed_chunks=failed_chunks,
    )

    sort_field = _sort_key(mover_type)
    reverse = mover_type != "losers"
    # Null-safe: drop quotes whose sort field is missing/None (common pre-market
    # for changesPercentage) so list.sort never compares None to a float and
    # crashes the whole response.
    rankable = [q for q in all_quotes if q.get(sort_field) is not None]
    rankable.sort(key=lambda q: q[sort_field], reverse=reverse)
    top_movers = filter_movers(rankable[:limit])

    quotes_received = len(all_quotes)
    constituents_count = len(symbols)
    coverage_pct = round(quotes_received / constituents_count * 100, 1)
    result: dict[str, Any] = {
        "type": mover_type,
        "index": index,
        "constituents_count": constituents_count,
        "quotes_received": quotes_received,
        "failed_chunks": failed_chunks,
        "coverage_pct": coverage_pct,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "data": top_movers,
    }
    if failed_chunks > 0 or quotes_received < constituents_count:
        # Disclose partial coverage explicitly instead of ranking a fraction of
        # the universe and presenting it as the complete leaderboard.
        result["warnings"] = [
            f"Partial coverage for '{index}': priced {quotes_received} of "
            f"{constituents_count} constituents ({coverage_pct}%); "
            f"{failed_chunks} batch chunk(s) failed. The ranking may omit movers."
        ]
    return result


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
            (sp500, nasdaq100, dowjones) by batch-quoting all constituents
            and sorting server-side. nasdaq100 is the Nasdaq-100 index
            (~100 names), not the full Nasdaq exchange. Constituent lists
            are cached 24h.
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
            return {
                "type": mover_type,
                "data": filtered_data,
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
    except Exception as e:
        log_error(logger, e, context={"tool": "get_movers", "type": mover_type})
        raise
