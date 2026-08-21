"""News tools using Tavily for market news search."""

from typing import Any, Literal

from ..clients.tavily_client import TavilyClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_news

logger = get_logger(__name__)

# Type aliases
TimeRange = Literal["day", "week", "month", "year", "d", "w", "m", "y"]


async def search_market_news(
    query: str,
    ticker: str | None = None,
    time_range: TimeRange | None = "week",
    limit: int = 5,
) -> dict[str, Any]:
    """AI-powered search for market news using Tavily.

    Performs intelligent search for financial news based on natural language
    queries. Optimized for latest news by default.

    Args:
        query: Natural language search query
            (e.g., 'earnings report', 'FDA approval', 'why did stock drop')
        ticker: Optional ticker symbol to focus search (e.g., 'AAPL', 'TSLA')
        time_range: Recency filter - 'day', 'week', 'month', 'year'
            (default: 'week')
        limit: Max articles to return (1-20, default: 5)

    Returns:
        Dict with query, ticker, time_range, count, and news list

    Raises:
        Exception: If news search fails
    """
    try:
        settings = get_settings()
        async with TavilyClient(settings) as client:
            data = await client.search_market_news(
                query=query,
                ticker=ticker,
                topic="news",
                time_range=time_range,
                max_results=limit,
            )
            filtered_data = filter_news(data)
            return {
                "query": query,
                "ticker": ticker,
                "time_range": time_range,
                "count": len(filtered_data),
                "news": filtered_data,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "search_market_news",
                "query": query,
                "ticker": ticker,
                "time_range": time_range,
            },
        )
        raise
