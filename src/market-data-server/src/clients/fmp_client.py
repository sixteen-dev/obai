"""FMP API client for market data."""

from typing import Any

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

logger = get_logger(__name__)


class FMPAPIError(Exception):
    """Raised when FMP returns HTTP 200 with an error body."""

    def __init__(self, message: str, endpoint: str) -> None:
        self.endpoint = endpoint
        super().__init__(f"FMP API error on /{endpoint}: {message}")


class FMPClient:
    """Client for Financial Modeling Prep API - Market Data endpoints."""

    def __init__(self, settings: Settings) -> None:
        """Initialize FMP client.

        Args:
            settings: Application settings with FMP API key
        """
        self.settings = settings
        self.base_url = settings.fmp_base_url
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> "FMPClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make GET request to FMP API.

        Args:
            endpoint: API endpoint path (without leading slash)
            params: Query parameters

        Returns:
            JSON response data

        Raises:
            httpx.HTTPError: On API request failure.
            FMPAPIError: When FMP returns 200 OK with an error body.
        """
        url = f"{self.base_url}/{endpoint}"
        query_params = {**(params or {}), "apikey": self.api_key}

        log_api_call(logger, "fmp", endpoint, params)

        try:
            response = await self.client.get(url, params=query_params)
            response.raise_for_status()
            data = response.json()

            # FMP returns 200 OK with error bodies instead of proper HTTP errors
            if isinstance(data, dict) and "Error Message" in data:
                msg = data["Error Message"]
                logger.warning("fmp_api_error_in_body", endpoint=endpoint, error=msg)
                raise FMPAPIError(msg, endpoint)

            return data
        except httpx.HTTPError as e:
            log_error(logger, e, context={"endpoint": endpoint, "params": params})
            raise

    async def get_quote(self, symbol: str) -> list[dict[str, Any]]:
        """Get full real-time quote for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL')

        Returns:
            List with quote data (price, change, volume, etc.)
        """
        data: list[dict[str, Any]] = await self._get("quote", {"symbol": symbol})
        return data

    async def get_quote_short(self, symbol: str) -> list[dict[str, Any]]:
        """Get condensed real-time quote for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List with minimal quote data
        """
        data: list[dict[str, Any]] = await self._get("quote-short", {"symbol": symbol})
        return data

    async def get_historical_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get intraday historical price data.

        Args:
            symbol: Stock ticker symbol
            interval: Time interval (1min, 5min, 15min, 30min, 1hour, 4hour)
            from_date: Start date (YYYY-MM-DD format)
            to_date: End date (YYYY-MM-DD format)

        Returns:
            List of OHLCV candles
        """
        params: dict[str, Any] = {"symbol": symbol}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data: list[dict[str, Any]] = await self._get(f"historical-chart/{interval}", params)
        return data

    async def get_historical_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """Get daily historical price data.

        Args:
            symbol: Stock ticker symbol
            from_date: Start date (YYYY-MM-DD format)
            to_date: End date (YYYY-MM-DD format)

        Returns:
            Dict with symbol info and historical data
        """
        params: dict[str, Any] = {"symbol": symbol}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data: dict[str, Any] = await self._get("historical-price-eod/full", params)
        return data

    async def get_stock_movers(self, mover_type: str) -> list[dict[str, Any]]:
        """Get market movers (gainers, losers, or most active).

        Args:
            mover_type: Type of movers ('gainers', 'losers', 'actives')

        Returns:
            List of stocks with price change data
        """
        # FMP changed endpoint names
        endpoint_map = {
            "gainers": "biggest-gainers",
            "losers": "biggest-losers",
            "actives": "most-actives",
        }
        endpoint = endpoint_map.get(mover_type, f"biggest-{mover_type}")
        data: list[dict[str, Any]] = await self._get(endpoint)
        return data

    async def get_sector_performance(self) -> list[dict[str, Any]]:
        """Get sector performance overview.

        Returns:
            List of sectors with performance metrics
        """
        data: list[dict[str, Any]] = await self._get("sector-performance-snapshot")
        return data

    async def get_afterhours_quote(self, symbol: str) -> list[dict[str, Any]]:
        """Get after-hours quote for a symbol.

        Requires FMP Professional plan or higher.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List with after-hours bid/ask prices, volume, and trends
        """
        data: list[dict[str, Any]] = await self._get("aftermarket-quote", {"symbol": symbol})
        return data

    async def is_market_open(self) -> list[dict[str, Any]]:
        """Check if market is currently open.

        Uses the stable all-exchange-market-hours endpoint which returns
        trading hours and open/closed status for all global exchanges.

        Returns:
            List of dicts with exchange hours and isMarketOpen status.
        """
        data: list[dict[str, Any]] = await self._get("all-exchange-market-hours")
        return data

    async def get_short_volume(self, symbol: str) -> list[dict[str, Any]]:
        """Get historical short sale volume data.

        NOTE: This endpoint is not documented in the new FMP API.
        This may not work and could return errors.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List with short volume, total volume, and short interest ratio
        """
        # This endpoint is not documented in new FMP API - may not exist
        data: list[dict[str, Any]] = await self._get("short-volume", {"symbol": symbol})
        return data

    async def get_technical_indicators(
        self, symbol: str, indicator_type: str, period: int = 10
    ) -> list[dict[str, Any]]:
        """Get technical indicators for a symbol.

        Args:
            symbol: Stock ticker symbol
            indicator_type: Type of indicator
                ('RSI', 'SMA', 'EMA', 'WMA', 'DEMA', 'TEMA', 'ADX')
            period: Period for the indicator calculation (default: 10)

        Returns:
            List with technical indicator values
        """
        # New FMP API structure:
        # /technical-indicators/{type}?symbol=X&periodLength=10&timeframe=1day
        indicator_lower = indicator_type.lower()
        params = {
            "symbol": symbol,
            "periodLength": period,
            "timeframe": "1day",
        }
        data: list[dict[str, Any]] = await self._get(
            f"technical-indicators/{indicator_lower}", params
        )
        return data

    async def get_commodities_list(self) -> list[dict[str, Any]]:
        """Get list of available commodity symbols.

        Returns:
            List of commodities with symbol and name
        """
        data: list[dict[str, Any]] = await self._get("commodities-list")
        return data

    async def batch_quote(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Get quotes for multiple symbols in a single request.

        The batch-quote endpoint returns 'changePercentage' while
        other endpoints use 'changesPercentage'. This method normalizes
        the field name for consistency.

        Args:
            symbols: List of ticker symbols (e.g., ['AAPL', 'MSFT', 'GOOG'])

        Returns:
            List of quote dicts with price, change, volume, etc.

        Raises:
            httpx.HTTPError: On API request failure.
            FMPAPIError: When FMP returns 200 OK with an error body.
        """
        if not symbols:
            return []

        joined = ",".join(symbols)
        data: list[dict[str, Any]] = await self._get("batch-quote", {"symbols": joined})

        # Normalize: batch-quote returns 'changePercentage',
        # other endpoints return 'changesPercentage'
        for quote in data:
            if "changePercentage" in quote and "changesPercentage" not in quote:
                quote["changesPercentage"] = quote.pop("changePercentage")

        return data

    async def get_index_constituents(self, index: str) -> list[dict[str, Any]]:
        """Get constituent symbols for a market index.

        Args:
            index: Index identifier ('sp500', 'nasdaq', 'dowjones')

        Returns:
            List of constituent entries with symbol, name, sector, etc.

        Raises:
            ValueError: If index is not recognized.
            httpx.HTTPError: On API request failure.
            FMPAPIError: When FMP returns 200 OK with an error body.
        """
        endpoint_map = {
            "sp500": "sp500-constituent",
            "nasdaq": "nasdaq-constituent",
            "dowjones": "dowjones-constituent",
        }
        endpoint = endpoint_map.get(index)
        if endpoint is None:
            raise ValueError(f"Unknown index: {index!r}. Valid: {list(endpoint_map)}")

        data: list[dict[str, Any]] = await self._get(endpoint)
        return data

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
