"""FMP API client for financial data."""

from typing import Any

import httpx
from cachetools import TTLCache

from ..config import get_settings
from ..logging_config import get_logger
from ..utils import is_retryable_httpx_exc, retry_async

logger = get_logger(__name__)

# Cache sizes and TTLs (in seconds)
PROFILE_CACHE_TTL = 86400  # 24 hours - company info rarely changes
STATEMENT_CACHE_TTL = 21600  # 6 hours - quarterly data
ANALYST_CACHE_TTL = 14400  # 4 hours - updates more frequently
FILINGS_CACHE_TTL = 3600  # 1 hour - new filings can appear


class FMPClient:
    """Client for Financial Modeling Prep API."""

    def __init__(self) -> None:
        """Initialize FMP client."""
        settings = get_settings()
        self.base_url = settings.fmp_base_url
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

        # In-memory caches with TTL and LRU eviction
        self._profile_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=500, ttl=PROFILE_CACHE_TTL
        )
        self._statement_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=1000, ttl=STATEMENT_CACHE_TTL
        )
        self._analyst_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=500, ttl=ANALYST_CACHE_TTL
        )
        self._filings_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=500, ttl=FILINGS_CACHE_TTL
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    @retry_async(
        max_attempts=3,
        initial_delay=0.5,
        backoff=2.0,
        jitter=0.25,
        retry_on=(httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError),
        retry_if=is_retryable_httpx_exc,
    )
    async def _get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Make GET request to FMP API.

        Args:
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            JSON response data

        Raises:
            httpx.HTTPError: If request fails
        """
        if params is None:
            params = {}

        params["apikey"] = self.api_key
        url = f"{self.base_url}/{endpoint}"

        response = await self.client.get(url, params=params)
        response.raise_for_status()
        data: list[dict[str, Any]] = response.json()
        return data

    async def get_income_statement(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get income statement for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of statements to return

        Returns:
            List of income statement data
        """
        cache_key = f"income:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="income-statement", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get(
            "income-statement", {"symbol": symbol, "period": period, "limit": limit}
        )
        self._statement_cache[cache_key] = result
        return result

    async def get_balance_sheet(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get balance sheet for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of statements to return

        Returns:
            List of balance sheet data
        """
        cache_key = f"balance:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="balance-sheet", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get(
            "balance-sheet-statement", {"symbol": symbol, "period": period, "limit": limit}
        )
        self._statement_cache[cache_key] = result
        return result

    async def get_cash_flow(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get cash flow statement for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of statements to return

        Returns:
            List of cash flow data
        """
        cache_key = f"cashflow:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="cash-flow", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get(
            "cash-flow-statement", {"symbol": symbol, "period": period, "limit": limit}
        )
        self._statement_cache[cache_key] = result
        return result

    async def get_company_profile(self, symbol: str) -> list[dict[str, Any]]:
        """Get company profile.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Company profile data
        """
        cache_key = f"profile:{symbol.upper()}"
        if cache_key in self._profile_cache:
            logger.debug("cache_hit", endpoint="profile", symbol=symbol)
            return self._profile_cache[cache_key]

        result = await self._get("profile", {"symbol": symbol})
        self._profile_cache[cache_key] = result
        return result

    async def get_key_metrics(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get key metrics for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to return

        Returns:
            List of key metrics data
        """
        cache_key = f"metrics:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="key-metrics", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get(
            "key-metrics", {"symbol": symbol, "period": period, "limit": limit}
        )
        self._statement_cache[cache_key] = result
        return result

    async def get_financial_ratios(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get financial ratios for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to return

        Returns:
            List of financial ratios data
        """
        cache_key = f"ratios:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="ratios", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get("ratios", {"symbol": symbol, "period": period, "limit": limit})
        self._statement_cache[cache_key] = result
        return result

    async def get_key_metrics_ttm(self, symbol: str) -> list[dict[str, Any]]:
        """Get trailing-twelve-month (TTM) key metrics for a symbol.

        TTM metrics are computed against the latest price and the trailing four
        quarters, so valuation ratios reflect current conditions rather than a
        fiscal-period-end snapshot.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List holding the single TTM key-metrics snapshot record
        """
        cache_key = f"metrics-ttm:{symbol.upper()}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="key-metrics-ttm", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get("key-metrics-ttm", {"symbol": symbol})
        self._statement_cache[cache_key] = result
        return result

    async def get_financial_ratios_ttm(self, symbol: str) -> list[dict[str, Any]]:
        """Get trailing-twelve-month (TTM) financial ratios for a symbol.

        TTM ratios (P/E, P/S, EV/EBITDA, margins, ROE) are computed against the
        latest price and the trailing four quarters, not a fiscal-period end.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List holding the single TTM financial-ratios snapshot record
        """
        cache_key = f"ratios-ttm:{symbol.upper()}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="ratios-ttm", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get("ratios-ttm", {"symbol": symbol})
        self._statement_cache[cache_key] = result
        return result

    async def get_analyst_estimates(
        self, symbol: str, period: str = "annual", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get analyst estimates for a symbol.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to return

        Returns:
            List of analyst estimates (EPS, revenue forecasts)
        """
        cache_key = f"estimates:{symbol.upper()}:{period}:{limit}"
        if cache_key in self._analyst_cache:
            logger.debug("cache_hit", endpoint="analyst-estimates", symbol=symbol)
            return self._analyst_cache[cache_key]

        result = await self._get(
            "analyst-estimates", {"symbol": symbol, "period": period, "limit": limit}
        )
        self._analyst_cache[cache_key] = result
        return result

    async def get_price_target_summary(self, symbol: str) -> list[dict[str, Any]]:
        """Get price target summary for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List with analyst price target data
        """
        cache_key = f"price-target:{symbol.upper()}"
        if cache_key in self._analyst_cache:
            logger.debug("cache_hit", endpoint="price-target-summary", symbol=symbol)
            return self._analyst_cache[cache_key]

        result = await self._get("price-target-summary", {"symbol": symbol})
        self._analyst_cache[cache_key] = result
        return result

    async def get_company_rating(self, symbol: str) -> list[dict[str, Any]]:
        """Get company rating for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List with company rating data
        """
        cache_key = f"rating:{symbol.upper()}"
        if cache_key in self._analyst_cache:
            logger.debug("cache_hit", endpoint="ratings-snapshot", symbol=symbol)
            return self._analyst_cache[cache_key]

        result = await self._get("ratings-snapshot", {"symbol": symbol})
        self._analyst_cache[cache_key] = result
        return result

    async def get_sec_filings(
        self,
        symbol: str,
        limit: int = 10,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get SEC filings for a symbol.

        Args:
            symbol: Stock ticker symbol
            limit: Number of filings to return
            from_date: Start date filter (YYYY-MM-DD), defaults to 3 months ago
            to_date: End date filter (YYYY-MM-DD), defaults to today

        Returns:
            List of SEC filing data
        """
        from datetime import date, timedelta

        today = date.today()
        if to_date is None:
            to_date = today.isoformat()
        if from_date is None:
            from_date = (today - timedelta(days=90)).isoformat()

        cache_key = f"filings:{symbol.upper()}:{limit}:{from_date}:{to_date}"
        if cache_key in self._filings_cache:
            logger.debug("cache_hit", endpoint="sec-filings", symbol=symbol)
            return self._filings_cache[cache_key]

        params: dict[str, Any] = {
            "symbol": symbol,
            "limit": limit,
            "from": from_date,
            "to": to_date,
        }
        result = await self._get("sec-filings-search/symbol", params)
        self._filings_cache[cache_key] = result
        return result

    async def get_insider_trades(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get insider trading activity for a symbol.

        Args:
            symbol: Stock ticker symbol
            limit: Number of trades to return

        Returns:
            List of insider trade data
        """
        cache_key = f"insider:{symbol.upper()}:{limit}"
        if cache_key in self._filings_cache:
            logger.debug("cache_hit", endpoint="insider-trades", symbol=symbol)
            return self._filings_cache[cache_key]

        result = await self._get("insider-trading/search", {"symbol": symbol, "limit": limit})
        self._filings_cache[cache_key] = result
        return result

    async def get_insider_trading_statistics(
        self, symbol: str, limit: int = 4
    ) -> list[dict[str, Any]]:
        """Get aggregated insider trading statistics by quarter.

        Args:
            symbol: Stock ticker symbol
            limit: Number of most recent quarters to return (default: 4)

        Returns:
            List of quarterly insider trading statistics, most recent first
        """
        cache_key = f"insider-stats:{symbol.upper()}:{limit}"
        if cache_key in self._filings_cache:
            logger.debug("cache_hit", endpoint="insider-trading-statistics", symbol=symbol)
            return self._filings_cache[cache_key]

        data = await self._get("insider-trading/statistics", {"symbol": symbol})

        # API returns all quarters — sort by year/quarter descending and limit
        sorted_data = sorted(
            data,
            key=lambda q: (q.get("year", 0), q.get("quarter", 0)),
            reverse=True,
        )
        result = sorted_data[:limit]

        self._filings_cache[cache_key] = result
        return result

    async def get_revenue_product_segmentation(
        self, symbol: str, period: str = "annual"
    ) -> list[dict[str, Any]]:
        """Get revenue breakdown by product segment.

        Args:
            symbol: Stock ticker symbol
            period: 'annual' or 'quarter'

        Returns:
            List of revenue segmentation data by product
        """
        cache_key = f"revenue-seg:{symbol.upper()}:{period}"
        if cache_key in self._statement_cache:
            logger.debug("cache_hit", endpoint="revenue-segmentation", symbol=symbol)
            return self._statement_cache[cache_key]

        result = await self._get(
            "revenue-product-segmentation", {"symbol": symbol, "period": period}
        )
        self._statement_cache[cache_key] = result
        return result

    async def health_check(self, timeout: float = 5.0) -> bool:
        """Verify API connectivity with a lightweight request.

        Args:
            timeout: Request timeout in seconds (default: 5.0)

        Returns:
            True if API is reachable and responding
        """
        url = f"{self.base_url}/is-the-market-open"
        params = {"apikey": self.api_key}

        try:
            response = await self.client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.TimeoutException):
            return False
