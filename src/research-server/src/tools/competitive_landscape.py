"""Competitive landscape research tool — competitors and market share via Exa."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..clients.exa_client import ExaClient, _days_ago, _is_retryable
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIXES = ("inc", "corp", "corporation", "co", "ltd", "llc", "plc", "ag", "sa")


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


def _normalize_for_match(text: str) -> str:
    """Lowercase, strip non-alphanumerics, drop common company-name suffixes."""
    s = _NON_ALNUM.sub("", text.lower())
    for suffix in _COMPANY_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _url_matches_company(url: str, normalized_company: str) -> bool:
    """Check if URL's domain contains the normalized company name.

    Filters out unrelated top results like aggregator pages or
    competitor sites that would otherwise seed find_similar with junk.
    """
    if not normalized_company:
        return False
    host = urlparse(url).hostname or ""
    domain = _NON_ALNUM.sub("", host.lower())
    return normalized_company in domain or domain in normalized_company


async def _resolve_company_url(client: ExaClient, company_name: str) -> str | None:
    """Resolve company name to its homepage URL via a quick Exa search.

    Looks at the top N results and only accepts one whose domain
    contains the company name — otherwise downstream ``find_similar``
    can be seeded with an unrelated site and return useless competitors.
    """
    normalized = _normalize_for_match(company_name)
    try:
        results = await client.search(
            query=f"{company_name} official website",
            # Not "keyword": Exa serves the company category from an entity
            # index with no keyword path and rejects the pair outright.
            search_type="auto",
            num_results=5,
            category="company",
        )
    except httpx.HTTPStatusError as exc:
        # A status the retry loop already declined to retry is a permanent
        # rejection of this request, not a blip. Degrading to None here made
        # every call return zero competitors while still reporting success.
        if not _is_retryable(exc.response.status_code):
            raise
        logger.warning("company_url_resolve_failed", company=company_name, error=str(exc))
        return None
    except httpx.HTTPError as exc:
        logger.warning("company_url_resolve_failed", company=company_name, error=str(exc))
        return None

    for result in results:
        if _url_matches_company(result.url, normalized):
            return result.url

    logger.info("company_url_no_domain_match", company=company_name, candidates=len(results))
    return None
