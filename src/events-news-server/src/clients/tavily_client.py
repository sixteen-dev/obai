"""Tavily API client for market news search with exponential backoff retry logic."""

import asyncio
from typing import Any, Literal

from tavily import (
    AsyncTavilyClient,
    BadRequestError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff in seconds

# Type aliases for Tavily parameters
SearchDepth = Literal["basic", "advanced"]
Topic = Literal["general", "news", "finance"]
TimeRange = Literal["day", "week", "month", "year", "d", "w", "m", "y"]

# Curated finance-news allowlist. Kept available for callers that
# explicitly want to restrict results to mainstream financial press,
# but no longer applied by default — the previous behavior silently
# filtered out primary sources (sec.gov, fda.gov, company IR pages),
# regional outlets, and any non-listed venue. Default is now "search
# the whole web and let Tavily rank by relevance".
DEFAULT_FINANCE_DOMAINS: list[str] = [
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "ft.com",
    "wsj.com",
    "marketwatch.com",
    "barrons.com",
    "seekingalpha.com",
    "investors.com",
    "thestreet.com",
    "benzinga.com",
    "fool.com",
]


class TavilyClient:
    """Client for Tavily API - Market news and research search."""

    def __init__(self, settings: Settings) -> None:
        """Initialize Tavily client.

        Args:
            settings: Application settings with Tavily API key
        """
        self.settings = settings
        self.client = AsyncTavilyClient(api_key=settings.tavily_api_key)

    async def __aenter__(self) -> "TavilyClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        # AsyncTavilyClient doesn't require explicit cleanup
        pass

    def _should_retry(self, error: Exception) -> bool:
        """Determine if request should be retried based on error type.

        Args:
            error: Exception raised during request

        Returns:
            True if request should be retried
        """
        # Don't retry on auth errors or bad requests - these are permanent failures
        return not isinstance(error, (InvalidAPIKeyError, MissingAPIKeyError, BadRequestError))

    async def search_market_news(
        self,
        query: str,
        ticker: str | None = None,
        topic: Topic = "finance",
        time_range: TimeRange | None = "week",
        max_results: int = 5,
        search_depth: SearchDepth = "basic",
        include_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for market news using Tavily API.

        Args:
            query: Search query (e.g., 'latest earnings report', 'market outlook')
            ticker: Optional ticker symbol to focus the search (e.g., 'AAPL')
            topic: Search topic - 'finance' for market data, 'news' for general news
            time_range: Time filter - 'day', 'week', 'month', 'year'
            max_results: Maximum number of results (1-20, default: 5)
            search_depth: 'basic' (1 credit) or 'advanced' (2 credits)
            include_domains: Domains to search. Defaults to reputable sources:
                Reuters, Bloomberg, CNBC, FT, WSJ, MarketWatch, Barron's, etc.

        Returns:
            List of news articles with title, url, content, score, published_date

        Raises:
            Exception: On API request failure after all retries
        """
        # Build the search query
        search_query = f"{ticker} stock {query}" if ticker else query

        log_api_call(
            logger,
            "tavily",
            "search",
            {
                "query": search_query,
                "topic": topic,
                "time_range": time_range,
                "max_results": max_results,
            },
        )

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Build search parameters
                search_params: dict[str, Any] = {
                    "query": search_query,
                    "topic": topic,
                    "max_results": max_results,
                    "search_depth": search_depth,
                    "include_answer": False,
                    "include_raw_content": False,
                }

                if time_range:
                    search_params["time_range"] = self._time_range_to_tavily(time_range)

                # Only restrict domains when the caller passed an explicit
                # non-empty list. The old behavior always applied a curated
                # finance-press allowlist, which silently filtered out SEC
                # filings, regulator pages, company IR releases, and any
                # non-mainstream venue. Now: empty/None means no filter.
                if include_domains:
                    search_params["include_domains"] = include_domains

                response = await self.client.search(**search_params)

                # Extract and normalize results
                results: list[dict[str, Any]] = response.get("results", [])
                return self._normalize_results(results, ticker)

            except (
                BadRequestError,
                InvalidAPIKeyError,
                MissingAPIKeyError,
                UsageLimitExceededError,
                Exception,
            ) as e:
                last_error = e

                if attempt == MAX_RETRIES:
                    break

                if not self._should_retry(e):
                    break

                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "api_call_retry",
                    endpoint="search",
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay_seconds=delay,
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)

        if last_error:
            log_error(
                logger,
                last_error,
                context={"endpoint": "search", "query": search_query},
            )
            raise last_error

        msg = "Request failed with no error recorded"
        raise RuntimeError(msg)

    def _time_range_to_tavily(self, time_range: TimeRange) -> str:
        """Map a requested recency window to Tavily's supported time_range token.

        Tavily's ``/search`` filters recency for ``topic="finance"`` via the
        ``time_range`` parameter (``d``/``w``/``m``/``y``). The legacy ``days``
        parameter is ignored for the finance topic, so it is not used.

        Args:
            time_range: Requested recency window

        Returns:
            Tavily recency token: 'd', 'w', 'm', or 'y' (defaults to 'w')
        """
        mapping: dict[TimeRange, str] = {
            "day": "d",
            "d": "d",
            "week": "w",
            "w": "w",
            "month": "m",
            "m": "m",
            "year": "y",
            "y": "y",
        }
        return mapping.get(time_range, "w")

    def _normalize_results(
        self,
        results: list[dict[str, Any]],
        ticker: str | None,
    ) -> list[dict[str, Any]]:
        """Normalize Tavily results to a consistent format.

        Args:
            results: Raw Tavily search results
            ticker: Optional ticker symbol for context

        Returns:
            Normalized list of news articles
        """
        normalized: list[dict[str, Any]] = []

        for result in results:
            article: dict[str, Any] = {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": result.get("content", ""),
                "publishedDate": result.get("published_date", ""),
                "score": result.get("score", 0.0),
                "source": self._extract_domain(result.get("url", "")),
            }

            if ticker:
                article["symbol"] = ticker

            normalized.append(article)

        return normalized

    def _extract_domain(self, url: str) -> str:
        """Extract domain name from URL for source attribution.

        Args:
            url: Full URL

        Returns:
            Domain name (e.g., 'finance.yahoo.com')
        """
        if not url:
            return ""
        try:
            # Simple extraction without external dependencies
            if "://" in url:
                url = url.split("://", 1)[1]
            return url.split("/", 1)[0]
        except (IndexError, ValueError):
            return ""
