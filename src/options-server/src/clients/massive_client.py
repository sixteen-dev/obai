"""Massive.io API client for options data with exponential backoff retry logic."""

import asyncio
from typing import Any

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff in seconds


class MassiveAPIError(Exception):
    """Exception for Massive API errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize Massive API error.

        Args:
            message: Error message
            status_code: HTTP status code if available
        """
        super().__init__(message)
        self.status_code = status_code


class MassiveClient:
    """Client for Massive.io Options API.

    Provides access to real-time options snapshots, trades, quotes, and historical data.
    Uses header-based authentication and supports cursor-based pagination.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize Massive client.

        Args:
            settings: Application settings with Massive API key
        """
        self.settings = settings
        self.base_url = settings.massive_base_url.rstrip("/")
        self.api_key = settings.massive_api_key
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    async def __aenter__(self) -> "MassiveClient":
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
    ) -> dict[str, Any]:
        """Make GET request to Massive API with exponential backoff retry logic.

        Args:
            endpoint: API endpoint path (with leading slash)
            params: Query parameters

        Returns:
            JSON response data

        Raises:
            MassiveAPIError: On API request failure after all retries
            httpx.HTTPError: On network errors after all retries
        """
        url = f"{self.base_url}{endpoint}"
        query_params = {k: v for k, v in (params or {}).items() if v is not None}

        log_api_call(logger, "massive", endpoint, query_params)

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.get(url, params=query_params)
                response.raise_for_status()
                data: dict[str, Any] = response.json()

                # Check for Massive-specific error responses
                if data.get("status") == "ERROR":
                    error_msg = data.get("error", "Unknown Massive API error")
                    raise MassiveAPIError(error_msg, response.status_code)

                return data

            except httpx.HTTPStatusError as e:
                last_error = e

                # Try to extract error message from response body
                try:
                    error_body = e.response.json()
                    if error_body.get("status") == "ERROR":
                        error_msg = error_body.get("error", str(e))
                        raise MassiveAPIError(error_msg, e.response.status_code) from e
                except (ValueError, KeyError):
                    pass

                if attempt == MAX_RETRIES or not self._should_retry(e):
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

    async def _get_paginated(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated endpoint.

        Args:
            endpoint: API endpoint path
            params: Query parameters
            max_pages: Maximum number of pages to fetch

        Returns:
            Combined list of all results
        """
        all_results: list[dict[str, Any]] = []
        current_params = dict(params or {})
        pages_fetched = 0

        while pages_fetched < max_pages:
            data = await self._get(endpoint, current_params)
            results = data.get("results", [])

            if isinstance(results, list):
                all_results.extend(results)
            elif isinstance(results, dict):
                all_results.append(results)

            # Check for next page cursor
            next_url = data.get("next_url")
            if not next_url:
                break

            # Extract cursor from next_url
            # Massive returns full URL, we need to extract the cursor parameter
            if "cursor=" in next_url:
                cursor = next_url.split("cursor=")[-1].split("&")[0]
                current_params["cursor"] = cursor
            else:
                break

            pages_fetched += 1

        return all_results

    # =========================================================================
    # MVP Tools - Critical Real-Time Options Endpoints
    # =========================================================================

    async def get_option_chain_snapshot(
        self,
        underlying_asset: str,
        expiration_date: str | None = None,
        strike_price: float | None = None,
        contract_type: str | None = None,
        limit: int = 250,
    ) -> dict[str, Any]:
        """Get real-time snapshot of option chain for an underlying asset.

        Args:
            underlying_asset: Stock ticker symbol (e.g., 'AAPL')
            expiration_date: Filter by expiration date (YYYY-MM-DD)
            strike_price: Filter by exact strike price
            contract_type: Filter by 'call' or 'put'
            limit: Maximum results per page (max 250)

        Returns:
            Option chain snapshot with pricing, Greeks, IV, and OI
        """
        endpoint = f"/v3/snapshot/options/{underlying_asset.upper()}"
        params: dict[str, Any] = {"limit": min(limit, 250)}

        if expiration_date:
            params["expiration_date"] = expiration_date
        if strike_price is not None:
            params["strike_price"] = strike_price
        if contract_type:
            params["contract_type"] = contract_type.lower()

        return await self._get(endpoint, params)

    async def get_option_contract_snapshot(
        self,
        underlying_asset: str,
        option_contract: str,
    ) -> dict[str, Any]:
        """Get real-time snapshot for a single option contract.

        Args:
            underlying_asset: Stock ticker symbol (e.g., 'AAPL')
            option_contract: Option symbol (e.g., 'O:AAPL240119C00125000')

        Returns:
            Complete snapshot with pricing, Greeks, IV, and OI
        """
        # Strip 'O:' prefix if present for URL construction
        contract = option_contract.replace("O:", "")
        endpoint = f"/v3/snapshot/options/{underlying_asset.upper()}/{contract}"

        return await self._get(endpoint)

    async def get_latest_option_trade(self, options_ticker: str) -> dict[str, Any]:
        """Get the most recent trade for an option contract.

        Args:
            options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

        Returns:
            Latest trade with price, size, exchange, and timestamp
        """
        # Ensure O: prefix for trade endpoint
        ticker = options_ticker if options_ticker.startswith("O:") else f"O:{options_ticker}"
        endpoint = f"/v2/last/trade/{ticker}"

        return await self._get(endpoint)

    async def get_latest_option_quote(self, options_ticker: str) -> dict[str, Any]:
        """Get the most recent NBBO quote for an option contract.

        Args:
            options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

        Returns:
            Latest NBBO with bid/ask prices and sizes
        """
        ticker = options_ticker if options_ticker.startswith("O:") else f"O:{options_ticker}"
        endpoint = f"/v2/last/nbbo/{ticker}"

        return await self._get(endpoint)

    # =========================================================================
    # Optional Tools - Reference and Historical Data
    # =========================================================================

    async def list_option_contracts(
        self,
        underlying_ticker: str | None = None,
        expiration_date: str | None = None,
        contract_type: str | None = None,
        expired: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List all option contracts with optional filters.

        Args:
            underlying_ticker: Filter by underlying stock
            expiration_date: Filter by expiration date (YYYY-MM-DD)
            contract_type: Filter by 'call' or 'put'
            expired: Include expired contracts
            limit: Maximum results per page

        Returns:
            List of option contract references
        """
        endpoint = "/v3/reference/options/contracts"
        params: dict[str, Any] = {"limit": min(limit, 1000)}

        if underlying_ticker:
            params["underlying_ticker"] = underlying_ticker.upper()
        if expiration_date:
            params["expiration_date"] = expiration_date
        if contract_type:
            params["contract_type"] = contract_type.lower()
        if expired:
            params["expired"] = "true"

        return await self._get_paginated(endpoint, params)

    async def get_option_trades_history(
        self,
        options_ticker: str,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get historical trades for an option contract.

        Args:
            options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
            timestamp_from: Start timestamp (nanoseconds or RFC3339)
            timestamp_to: End timestamp (nanoseconds or RFC3339)
            limit: Maximum results

        Returns:
            Historical trades data
        """
        ticker = options_ticker if options_ticker.startswith("O:") else f"O:{options_ticker}"
        endpoint = f"/v3/trades/{ticker}"
        params: dict[str, Any] = {"limit": min(limit, 50000)}

        if timestamp_from:
            params["timestamp.gte"] = timestamp_from
        if timestamp_to:
            params["timestamp.lte"] = timestamp_to

        return await self._get(endpoint, params)

    async def get_option_quotes_history(
        self,
        options_ticker: str,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get historical NBBO quotes for an option contract.

        Args:
            options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
            timestamp_from: Start timestamp (nanoseconds or RFC3339)
            timestamp_to: End timestamp (nanoseconds or RFC3339)
            limit: Maximum results

        Returns:
            Historical quotes data
        """
        ticker = options_ticker if options_ticker.startswith("O:") else f"O:{options_ticker}"
        endpoint = f"/v3/quotes/{ticker}"
        params: dict[str, Any] = {"limit": min(limit, 50000)}

        if timestamp_from:
            params["timestamp.gte"] = timestamp_from
        if timestamp_to:
            params["timestamp.lte"] = timestamp_to

        return await self._get(endpoint, params)

    async def get_option_aggregates(
        self,
        options_ticker: str,
        multiplier: int,
        timespan: str,
        from_date: str,
        to_date: str,
        adjusted: bool = True,
        sort: str = "asc",
    ) -> dict[str, Any]:
        """Get OHLCV aggregate bars for an option contract.

        Args:
            options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
            multiplier: Size of timespan multiplier (e.g., 1, 5, 15)
            timespan: Timespan unit (minute, hour, day, week, month)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            adjusted: Adjust for splits
            sort: Sort order ('asc' or 'desc')

        Returns:
            OHLCV bars data
        """
        ticker = options_ticker if options_ticker.startswith("O:") else f"O:{options_ticker}"
        endpoint = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        params: dict[str, Any] = {
            "adjusted": str(adjusted).lower(),
            "sort": sort,
        }

        return await self._get(endpoint, params)

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
        endpoint = "/v3/reference/tickers"
        params: dict[str, str | int] = {"ticker": "AAPL", "limit": 1}

        try:
            response = await self.client.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.TimeoutException):
            return False
