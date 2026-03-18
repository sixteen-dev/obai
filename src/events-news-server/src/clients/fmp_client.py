"""FMP API client for events and news data with exponential backoff retry logic."""

import asyncio
from typing import Any

import httpx
from cachetools import TTLCache

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

logger = get_logger(__name__)

# Cache TTLs (in seconds)
EARNINGS_CACHE_TTL = 14400  # 4 hours - earnings dates are known in advance
DIVIDENDS_CACHE_TTL = 86400  # 24 hours - historical dividends don't change

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff in seconds


class FMPClient:
    """Client for Financial Modeling Prep API - Events and News endpoints."""

    def __init__(self, settings: Settings) -> None:
        """Initialize FMP client.

        Args:
            settings: Application settings with FMP API key
        """
        self.settings = settings
        self.base_url = settings.fmp_base_url
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

        # In-memory caches
        self._earnings_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=500, ttl=EARNINGS_CACHE_TTL
        )
        self._dividends_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=500, ttl=DIVIDENDS_CACHE_TTL
        )

    async def __aenter__(self) -> "FMPClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    def _should_retry(self, error: Exception) -> bool:
        """Determine if request should be retried based on error type.

        Args:
            error: Exception raised during request

        Returns:
            True if request should be retried
        """
        # Retry on network errors
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            return True

        # Retry on 5xx server errors
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code >= 500

        return False

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make GET request to FMP API with exponential backoff retry logic.

        Args:
            endpoint: API endpoint path (without leading slash)
            params: Query parameters

        Returns:
            JSON response data

        Raises:
            httpx.HTTPError: On API request failure after all retries
        """
        url = f"{self.base_url}/{endpoint}"
        query_params = {**(params or {}), "apikey": self.api_key}

        log_api_call(logger, "fmp", endpoint, params)

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.get(url, params=query_params)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPError as e:
                last_error = e

                # Don't retry on final attempt
                if attempt == MAX_RETRIES:
                    break

                # Check if we should retry this error
                if not self._should_retry(e):
                    break

                # Calculate retry delay
                delay = RETRY_DELAYS[attempt]

                logger.warning(
                    "api_call_retry",
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay_seconds=delay,
                    error_type=type(e).__name__,
                )

                # Wait before retry with exponential backoff
                await asyncio.sleep(delay)

        # All retries exhausted or non-retryable error
        if last_error:
            log_error(logger, last_error, context={"endpoint": endpoint, "params": params})
            raise last_error

        # Should never reach here, but mypy needs this
        msg = "Request failed with no error recorded"
        raise RuntimeError(msg)

    async def get_stock_news(
        self,
        tickers: str,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Get stock news for one or more tickers.

        Args:
            tickers: Comma-separated ticker symbols (e.g., 'AAPL,MSFT')
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
            limit: Maximum number of news items to return (default: 5)

        Returns:
            List of news articles
        """
        # FMP API changed: news/stock-latest uses page & limit, not tickers directly
        params: dict[str, Any] = {"page": 0, "limit": limit}
        if tickers:
            params["tickers"] = tickers
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data: list[dict[str, Any]] = await self._get("news/stock-latest", params)
        return data

    async def get_earnings(
        self,
        symbol: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get earnings history and upcoming earnings for a specific ticker.

        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL')
            limit: Maximum number of earnings records to return (default: 10)

        Returns:
            List of earnings records (past and future) for the symbol
        """
        cache_key = f"earnings:{symbol.upper()}:{limit}"
        if cache_key in self._earnings_cache:
            logger.debug("cache_hit", endpoint="earnings", symbol=symbol)
            return self._earnings_cache[cache_key]

        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        data: list[dict[str, Any]] = await self._get("earnings", params)
        self._earnings_cache[cache_key] = data
        return data

    async def get_dividends(
        self,
        symbol: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get dividend history for a specific ticker.

        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL')
            limit: Maximum number of dividend records to return (default: 10)

        Returns:
            List of dividend records for the symbol
        """
        cache_key = f"dividends:{symbol.upper()}:{limit}"
        if cache_key in self._dividends_cache:
            logger.debug("cache_hit", endpoint="dividends", symbol=symbol)
            return self._dividends_cache[cache_key]

        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        data: list[dict[str, Any]] = await self._get("dividends", params)
        self._dividends_cache[cache_key] = data
        return data

    async def health_check(self, timeout: float = 5.0) -> bool:
        """Verify API connectivity with a lightweight request.

        Args:
            timeout: Request timeout in seconds (default: 5.0)

        Returns:
            True if API is reachable and responding
        """
        # Use S&P 500 quote as health check - lightweight and always available
        url = f"{self.base_url}/quote"
        params = {"apikey": self.api_key, "symbol": "^GSPC"}

        try:
            response = await self.client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.TimeoutException):
            return False
