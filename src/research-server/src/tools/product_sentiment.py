"""Product sentiment research tool — user reviews and forum discussions via Exa."""

from __future__ import annotations

from typing import Any

from ..clients.exa_client import ExaClient, _days_ago
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)

_NEWS_DOMAINS = [
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "wsj.com",
    "marketwatch.com",
    "barrons.com",
    "seekingalpha.com",
]


async def research_product_sentiment(
    symbol: str,
    company_name: str,
    product: str | None = None,
    days_back: int = 90,
) -> dict[str, Any]:
    """Research product/service reception — reviews, Reddit, forums, app stores.

    Uses neural search to find conceptually related user feedback content,
    excluding major news sites (Tavily handles news).

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        product: Specific product/service to research. Defaults to all products.
        days_back: How many days of history to search. Default 90.

    Returns:
        Dict with symbol, company_name, product, result count, and results.

    """
    settings = get_settings()
    product_term = product or "products"
    try:
        async with ExaClient() as client:
            results = await client.search(
                query=(
                    f"user reviews and customer feedback about {company_name} {product_term}, "
                    f"including complaints, praise, and overall sentiment"
                ),
                search_type="neural",
                num_results=min(10, settings.default_num_results + 2),
                highlight_query=f"{company_name} {product_term} user experience quality issues",
                start_published_date=_days_ago(days_back),
                exclude_domains=_NEWS_DOMAINS,
            )
        return {
            "symbol": symbol.upper(),
            "company_name": company_name,
            "product": product_term,
            "tool": "product_sentiment",
            "count": len(results),
            "freshness": freshness_summary(results),
            "results": [r.to_dict() for r in results],
        }
    except Exception as exc:
        log_error(logger, exc, context={"tool": "product_sentiment", "symbol": symbol})
        raise
