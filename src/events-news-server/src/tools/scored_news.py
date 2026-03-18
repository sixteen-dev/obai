"""AI-scored news tool using TezNewz REST API."""

from typing import Any

from ..clients.teznewz_client import TezNewzClient
from ..config import get_settings
from ..logging_config import get_logger

logger = get_logger(__name__)


async def get_scored_news(
    symbol: str,
    hours_back: int = 24,
    min_abs_impact: int = 0,
    limit: int = 10,
) -> dict[str, Any]:
    """Get AI-scored news for a stock ticker.

    Fetches curated, AI-analyzed news from the news_hot table. Each article
    has an impact_score from -100 to +100 indicating predicted effect on
    stock price.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA')
        hours_back: How far back to search (1-8760 hours, default: 24)
        min_abs_impact: Minimum absolute impact score (0-100, default: 0).
            Setting to 10 returns articles with score <= -50 OR >= 50.
        limit: Maximum articles to return (default: 10)

    Returns:
        Dict with symbol, articles list, count, and metadata
    """
    settings = get_settings()

    async with TezNewzClient(settings) as client:
        articles = await client.get_news_by_ticker(
            ticker=symbol,
            hours_back=hours_back,
            min_abs_impact=min_abs_impact,
            limit=limit,
        )

    # Format response - keep essential fields
    formatted_articles = []
    for article in articles:
        formatted = {
            "headline": article.get("headline", ""),
            "summary": article.get("ai_summary") or article.get("summary", ""),
            "impact_score": article.get("impact_score", 0),
            "source": article.get("source", ""),
            "url": article.get("url", ""),
            "published_date": article.get("created_time", ""),
            "sector": article.get("sector", ""),
        }

        # Include company info if available
        if "company_info" in article:
            formatted["company"] = article["company_info"].get("company_name", "")

        formatted_articles.append(formatted)

    # Sort by absolute impact score (most impactful first)
    formatted_articles.sort(key=lambda x: abs(x.get("impact_score", 0)), reverse=True)

    return {
        "symbol": symbol.upper(),
        "hours_back": hours_back,
        "min_abs_impact": min_abs_impact,
        "articles": formatted_articles,
        "count": len(formatted_articles),
        "scoring_info": {
            "range": "-100 to +100",
            "negative": "bearish impact on stock",
            "positive": "bullish impact on stock",
            "magnitude": "higher absolute value = more significant",
        },
        "disclaimer": "AI-scored news for informational purposes only. Not investment advice.",
        "attribution": "News data curated by tezQ Research Platform",
    }


async def get_sector_news(
    sector: str,
    hours_back: int = 24,
    min_abs_impact: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Get AI-scored news for a market sector.

    Args:
        sector: Sector name (e.g., 'Healthcare', 'Technology', 'Financial')
        hours_back: How far back to search (default: 24)
        min_abs_impact: Minimum absolute impact score (0-100)
        limit: Maximum articles to return (default: 20)

    Returns:
        Dict with sector, articles list, count, and metadata
    """
    settings = get_settings()

    async with TezNewzClient(settings) as client:
        articles = await client.get_news_by_sector(
            sector=sector,
            hours_back=hours_back,
            min_abs_impact=min_abs_impact,
            limit=limit,
        )

    # Format response
    formatted_articles = []
    for article in articles:
        formatted = {
            "ticker": article.get("ticker", ""),
            "headline": article.get("headline", ""),
            "summary": article.get("ai_summary") or article.get("summary", ""),
            "impact_score": article.get("impact_score", 0),
            "source": article.get("source", ""),
            "url": article.get("url", ""),
            "published_date": article.get("created_time", ""),
        }
        formatted_articles.append(formatted)

    # Sort by absolute impact score
    formatted_articles.sort(key=lambda x: abs(x.get("impact_score", 0)), reverse=True)

    # Extract unique tickers mentioned
    tickers_mentioned = list({a["ticker"] for a in formatted_articles if a["ticker"]})

    return {
        "sector": sector,
        "hours_back": hours_back,
        "articles": formatted_articles,
        "count": len(formatted_articles),
        "tickers_mentioned": sorted(tickers_mentioned),
        "scoring_info": {
            "range": "-100 to +100",
            "negative": "bearish impact",
            "positive": "bullish impact",
        },
        "disclaimer": "AI-scored news for informational purposes only. Not investment advice.",
    }
