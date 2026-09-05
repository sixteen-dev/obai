"""Company profile research tool — deep dive via Exa."""

from __future__ import annotations

from typing import Any

from ..clients.exa_client import ExaClient, _days_ago
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)


async def research_company_profile(
    symbol: str,
    company_name: str,
    days_back: int = 180,
) -> dict[str, Any]:
    """Deep research on a company's business, strategy, and market position.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        days_back: How many days of history to search. Default 180.

    Returns:
        Dict with symbol, company_name, result count, and research results.

    """
    settings = get_settings()
    try:
        async with ExaClient() as client:
            results = await client.search(
                query=(
                    f"in-depth analysis of {company_name} ({symbol}) "
                    f"including its business model, recent strategy, and market position"
                ),
                search_type="auto",
                num_results=settings.default_num_results,
                # No category: Exa's company index cannot filter by date, and
                # days_back is this tool's documented parameter. Honouring the
                # caller's window beats an internal relevance hint.
                highlight_query=f"{company_name} strategy products market position growth",
                start_published_date=_days_ago(days_back),
                exclude_domains=["wikipedia.org"],
            )
        return {
            "symbol": symbol.upper(),
            "company_name": company_name,
            "tool": "company_profile",
            "count": len(results),
            "freshness": freshness_summary(results),
            "results": [r.to_dict() for r in results],
        }
    except Exception as exc:
        log_error(logger, exc, context={"tool": "company_profile", "symbol": symbol})
        raise
