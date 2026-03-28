"""General research tool — thematic and open-ended qualitative queries via Exa."""

from __future__ import annotations

from typing import Any

from ..clients.exa_client import ExaClient, _days_ago
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)


async def research_general(
    query: str,
    symbol: str | None = None,
    days_back: int = 180,
) -> dict[str, Any]:
    """Thematic or cross-cutting qualitative research via Exa.

    Args:
        query: Free-form research query.
        symbol: Optional stock ticker for context.
        days_back: How many days of history to search. Default 180.

    Returns:
        Dict with query, symbol, result count, and results.

    """
    settings = get_settings()
    try:
        client = ExaClient()
        results = await client.search(
            query=query,
            search_type="auto",
            num_results=min(10, settings.default_num_results + 2),
            highlight_query=query,
            start_published_date=_days_ago(days_back),
        )
        return {
            "query": query,
            "symbol": symbol.upper() if symbol else None,
            "tool": "general_research",
            "count": len(results),
            "freshness": freshness_summary(results),
            "results": [r.to_dict() for r in results],
        }
    except Exception as exc:
        log_error(logger, exc, context={"tool": "general_research", "query": query})
        raise
