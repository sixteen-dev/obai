"""FMP API client for stock screening with exponential backoff retry logic."""

import asyncio
from typing import Any

import httpx
from cachetools import TTLCache

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

logger = get_logger(__name__)

# Cache TTL (in seconds) - company names/symbols rarely change
SEARCH_CACHE_TTL = 86400  # 24 hours

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff in seconds


class FMPAPIError(Exception):
    """Exception for FMP API errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize FMP API error.

        Args:
            message: Error message
            status_code: HTTP status code if available
        """
        super().__init__(message)
        self.status_code = status_code


class FMPClient:
    """Client for FMP (Financial Modeling Prep) API.

    Provides access to stock screening and company search endpoints.
    Uses query parameter authentication.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize FMP client.

        Args:
            settings: Application settings with FMP API key
        """
        self.settings = settings
        self.base_url = settings.fmp_base_url.rstrip("/")
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

        # In-memory cache for search results (names/symbols rarely change)
        self._search_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=1000, ttl=SEARCH_CACHE_TTL
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
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            return True

        if isinstance(error, httpx.HTTPStatusError):
            # Retry on 5xx server errors and 429 rate limit
            return error.response.status_code >= 500 or error.response.status_code == 429

        return False

    async def _get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Make GET request to FMP API with exponential backoff retry logic.

        Args:
            endpoint: API endpoint path (with leading slash)
            params: Query parameters (api key added automatically)

        Returns:
            JSON response data (typically a list of results)

        Raises:
            FMPAPIError: On API request failure after all retries
            httpx.HTTPError: On network errors after all retries
        """
        url = f"{self.base_url}{endpoint}"
        query_params = {k: v for k, v in (params or {}).items() if v is not None}
        query_params["apikey"] = self.api_key

        sanitized_params = {k: v for k, v in query_params.items() if k != "apikey"}
        log_api_call(logger, "fmp", endpoint, sanitized_params)

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.get(url, params=query_params)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()

                # FMP returns empty list or error message on some endpoints
                if isinstance(data, dict) and "Error Message" in data:
                    error_msg = data.get("Error Message", "Unknown FMP API error")
                    raise FMPAPIError(str(error_msg), response.status_code)

                return data

            except httpx.HTTPStatusError as e:
                last_error = e

                # Decide whether to retry *before* wrapping the response body
                # in `FMPAPIError`. 429s and 5xxs often carry an "Error
                # Message" body too; wrapping first would short-circuit the
                # backoff path tests explicitly exercise.
                should_retry = self._should_retry(e) and attempt < MAX_RETRIES
                if not should_retry:
                    try:
                        error_body = e.response.json()
                        if isinstance(error_body, dict) and "Error Message" in error_body:
                            error_msg = error_body.get("Error Message", str(e))
                            raise FMPAPIError(str(error_msg), e.response.status_code) from e
                    except (ValueError, KeyError):
                        pass
                    break

                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "api_call_retry",
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay_seconds=delay,
                    status_code=e.response.status_code,
                )
                await asyncio.sleep(delay)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e

                if attempt == MAX_RETRIES:
                    break

                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "api_call_retry",
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay_seconds=delay,
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)

        if last_error:
            log_error(logger, last_error, context={"endpoint": endpoint, "params": params})
            raise last_error

        msg = "Request failed with no error recorded"
        raise RuntimeError(msg)

    # =========================================================================
    # Screening Tools
    # =========================================================================

    async def screen_stocks(
        self,
        market_cap_more_than: int | None = None,
        market_cap_lower_than: int | None = None,
        price_more_than: float | None = None,
        price_lower_than: float | None = None,
        volume_more_than: int | None = None,
        volume_lower_than: int | None = None,
        beta_more_than: float | None = None,
        beta_lower_than: float | None = None,
        dividend_more_than: float | None = None,
        dividend_lower_than: float | None = None,
        sector: str | None = None,
        industry: str | None = None,
        country: str | None = None,
        exchange: str | None = None,
        is_etf: bool | None = None,
        is_fund: bool | None = None,
        is_actively_trading: bool = True,
        include_all_share_classes: bool = False,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Screen stocks with various filters.

        Args:
            market_cap_more_than: Minimum market cap
            market_cap_lower_than: Maximum market cap
            price_more_than: Minimum price
            price_lower_than: Maximum price
            volume_more_than: Minimum volume
            volume_lower_than: Maximum volume
            beta_more_than: Minimum beta
            beta_lower_than: Maximum beta
            dividend_more_than: Minimum dividend yield
            dividend_lower_than: Maximum dividend yield
            sector: Sector filter (e.g., "Technology")
            industry: Industry filter
            country: Country code (e.g., "US")
            exchange: Exchange (e.g., "NASDAQ")
            is_etf: Filter for ETFs only
            is_fund: Filter for mutual funds only
            is_actively_trading: Only actively traded stocks (default: True)
            include_all_share_classes: Include all share classes (default: False)
            limit: Maximum results (default: 25, max: 100)

        Returns:
            List of matching stocks with key metrics
        """
        endpoint = "/company-screener"
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "isActivelyTrading": str(is_actively_trading).lower(),
            "includeAllShareClasses": str(include_all_share_classes).lower(),
        }

        if market_cap_more_than is not None:
            params["marketCapMoreThan"] = market_cap_more_than
        if market_cap_lower_than is not None:
            params["marketCapLowerThan"] = market_cap_lower_than
        if price_more_than is not None:
            params["priceMoreThan"] = price_more_than
        if price_lower_than is not None:
            params["priceLowerThan"] = price_lower_than
        if volume_more_than is not None:
            params["volumeMoreThan"] = volume_more_than
        if volume_lower_than is not None:
            params["volumeLowerThan"] = volume_lower_than
        if beta_more_than is not None:
            params["betaMoreThan"] = beta_more_than
        if beta_lower_than is not None:
            params["betaLowerThan"] = beta_lower_than
        if dividend_more_than is not None:
            params["dividendMoreThan"] = dividend_more_than
        if dividend_lower_than is not None:
            params["dividendLowerThan"] = dividend_lower_than
        if sector is not None:
            params["sector"] = sector
        if industry is not None:
            params["industry"] = industry
        if country is not None:
            params["country"] = country
        if exchange is not None:
            params["exchange"] = exchange
        if is_etf is not None:
            params["isEtf"] = str(is_etf).lower()
        if is_fund is not None:
            params["isFund"] = str(is_fund).lower()

        return await self._get(endpoint, params)

    async def search_by_name(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search companies by name.

        Args:
            query: Company name or partial name
            limit: Maximum results (default: 10, max: 20)

        Returns:
            List of matching companies with symbols
        """
        cache_key = f"name:{query.lower()}:{limit}"
        if cache_key in self._search_cache:
            logger.debug("cache_hit", endpoint="search-name", query=query)
            return self._search_cache[cache_key]

        endpoint = "/search-name"
        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 20),
        }

        result = await self._get(endpoint, params)
        self._search_cache[cache_key] = result
        return result

    async def search_by_symbol(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search companies by symbol.

        Args:
            query: Ticker or partial ticker
            limit: Maximum results (default: 10, max: 20)

        Returns:
            List of matching companies with symbols
        """
        cache_key = f"symbol:{query.upper()}:{limit}"
        if cache_key in self._search_cache:
            logger.debug("cache_hit", endpoint="search-symbol", query=query)
            return self._search_cache[cache_key]

        endpoint = "/search-symbol"
        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 20),
        }

        result = await self._get(endpoint, params)
        self._search_cache[cache_key] = result
        return result

    # =========================================================================
    # Reference Data
    # =========================================================================

    async def get_available_industries(self) -> list[dict[str, Any]]:
        """Fetch all valid industry values accepted by the screener.

        Returns:
            List of dicts with "industry" key, e.g. [{"industry": "Software - Application"}, ...]
        """
        cache_key = "available_industries"
        if cache_key in self._search_cache:
            logger.debug("cache_hit", endpoint="available-industries")
            return self._search_cache[cache_key]

        result = await self._get("/available-industries")
        self._search_cache[cache_key] = result
        return result

    async def get_available_sectors(self) -> list[dict[str, Any]]:
        """Fetch all valid sector values accepted by the screener.

        Returns:
            List of dicts with "sector" key, e.g. [{"sector": "Technology"}, ...]
        """
        cache_key = "available_sectors"
        if cache_key in self._search_cache:
            logger.debug("cache_hit", endpoint="available-sectors")
            return self._search_cache[cache_key]

        result = await self._get("/available-sectors")
        self._search_cache[cache_key] = result
        return result

    # =========================================================================
    # Health Check
    # =========================================================================

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
