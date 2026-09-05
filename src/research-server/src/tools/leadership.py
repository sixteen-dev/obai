"""Leadership research tool — CEO/exec track record via Exa."""

from __future__ import annotations

from typing import Any

from ..clients.exa_client import ExaClient, _days_ago
from ..config import get_settings
from ..logging_config import get_logger, log_error
from .freshness import freshness_summary

logger = get_logger(__name__)


async def research_leadership(
    symbol: str,
    company_name: str,
    person_name: str | None = None,
    days_back: int = 365,
) -> dict[str, Any]:
    """Research CEO/exec track record, leadership changes, and management quality.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        person_name: Specific exec to research. Defaults to CEO.
        days_back: How many days of history to search. Default 365.

    Returns:
        Dict with symbol, company_name, person, result count, and results.

    """
    settings = get_settings()
    person = person_name or "CEO"
    try:
        async with ExaClient() as client:
            results = await client.search(
                query=(
                    f"profile and track record of {person} at {company_name}, "
                    f"including leadership decisions, strategy changes, and performance"
                ),
                search_type="auto",
                num_results=settings.default_num_results,
                # No category: Exa's people index cannot filter by date, and
                # days_back is this tool's documented parameter.
                highlight_query=f"{person} {company_name} leadership decisions performance",
                start_published_date=_days_ago(days_back),
            )
        return {
            "symbol": symbol.upper(),
            "company_name": company_name,
            "person": person,
            "tool": "leadership",
            "count": len(results),
            "freshness": freshness_summary(results),
            "results": [r.to_dict() for r in results],
        }
    except Exception as exc:
        log_error(logger, exc, context={"tool": "leadership", "symbol": symbol})
        raise
