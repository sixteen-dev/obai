"""Competitive landscape research tool — competitors and market share via Exa."""

from __future__ import annotations

from typing import Any

from ..clients.exa_client import ExaClient, _days_ago
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)


async def research_competitive_landscape(
    symbol: str,
    company_name: str,
    days_back: int = 180,
) -> dict[str, Any]:
    """Research competitors, market share, and competitive positioning.

    Two-step approach:
    1. Find similar companies via Exa find_similar
    2. Search for comparison/analysis articles

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        days_back: How many days of history to search. Default 180.

    Returns:
        Dict with symbol, company_name, competitors, comparisons, and results.

    """
    settings = get_settings()
    try:
        async with ExaClient() as client:
            # Step 1: Discover competitor company pages
            company_url = await _resolve_company_url(client, company_name)
            competitors = []
            if company_url:
                competitors = await client.find_similar(
                    url=company_url,
                    num_results=5,
                    exclude_source_domain=True,
                    category="company",
                )

            # Step 2: Find comparison/analysis articles
            comparisons = await client.search(
                query=(
                    f"analysis comparing {company_name} against its competitors, "
                    f"including market share, competitive advantages, and positioning"
                ),
                search_type="auto",
                num_results=settings.default_num_results,
                highlight_query=f"{company_name} market share competitive advantage moat",
                start_published_date=_days_ago(days_back),
            )

        all_results = competitors + comparisons
        return {
            "symbol": symbol.upper(),
            "company_name": company_name,
            "tool": "competitive_landscape",
            "competitor_count": len(competitors),
            "competitors": [r.to_dict() for r in competitors],
            "comparison_count": len(comparisons),
            "comparisons": [r.to_dict() for r in comparisons],
            "freshness": freshness_summary(all_results),
        }
    except Exception as exc:
        log_error(logger, exc, context={"tool": "competitive_landscape", "symbol": symbol})
        raise


async def _resolve_company_url(client: ExaClient, company_name: str) -> str | None:
    """Resolve company name to its homepage URL via a quick Exa search.

    Args:
        client: Initialized ExaClient.
        company_name: Company name to resolve.

    Returns:
        Company homepage URL or None if not found.

    """
    try:
        results = await client.search(
            query=f"{company_name} official website",
            search_type="keyword",
            num_results=1,
            category="company",
        )
        if results:
            return results[0].url
    except Exception:
        logger.warning("company_url_resolve_failed", company=company_name)
    return None
