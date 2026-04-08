"""FMP (Financial Modeling Prep) API client for portfolio-server."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error

# Fallback values if FMP API fails
FALLBACK_RISK_FREE_RATE = Decimal("0.045")  # 4.5%
FALLBACK_INFLATION = Decimal("0.025")  # 2.5%

logger = get_logger(__name__)


@dataclass
class ETFHolding:
    """A single holding within an ETF."""

    etf_symbol: str  # Parent ETF (e.g., "SPY")
    asset_symbol: str  # Underlying (e.g., "AAPL")
    name: str  # "APPLE INC"
    weight_percentage: Decimal  # 7.137 (percent, not decimal)
    market_value: Decimal | None
    shares: int | None
    isin: str | None
    updated_at: str  # "2025-01-16 05:01:09"


@dataclass
class ETFInfo:
    """Metadata about an ETF."""

    symbol: str
    name: str
    expense_ratio: Decimal
    aum: Decimal
    holdings_count: int
    inception_date: str
    sector_weights: dict[str, Decimal]  # {"Technology": 32.5, ...}
    updated_at: str


class EconomicDataCache:
    """Cache for Treasury rates and economic indicators."""

    def __init__(self, ttl_hours: int = 4) -> None:
        """Initialize cache with TTL.

        Args:
            ttl_hours: Time-to-live in hours for cached data.

        """
        self._cache: dict[str, tuple[datetime, Any]] = {}
        self._ttl = timedelta(hours=ttl_hours)

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if expired/missing.

        """
        if key in self._cache:
            cached_at, value = self._cache[key]
            if datetime.now() - cached_at < self._ttl:
                return value
        return None

    def set(self, key: str, value: Any) -> None:
        """Set cached value.

        Args:
            key: Cache key.
            value: Value to cache.

        """
        self._cache[key] = (datetime.now(), value)


class ETFHoldingsCache:
    """Cache for ETF holdings (24-hour TTL since holdings are quarterly)."""

    def __init__(self, ttl_hours: int = 24) -> None:
        """Initialize cache with TTL.

        Args:
            ttl_hours: Time-to-live in hours for cached data.

        """
        self._cache: dict[str, tuple[datetime, list[ETFHolding]]] = {}
        self._ttl = timedelta(hours=ttl_hours)

    def get(self, symbol: str) -> list[ETFHolding] | None:
        """Get cached holdings if not expired.

        Args:
            symbol: ETF symbol.

        Returns:
            Cached holdings or None if expired/missing.

        """
        if symbol in self._cache:
            cached_at, holdings = self._cache[symbol]
            if datetime.now() - cached_at < self._ttl:
                return holdings
        return None

    def set(self, symbol: str, holdings: list[ETFHolding]) -> None:
        """Set cached holdings.

        Args:
            symbol: ETF symbol.
            holdings: Holdings to cache.

        """
        self._cache[symbol] = (datetime.now(), holdings)


class FMPClient:
    """Client for Financial Modeling Prep API."""

    BASE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self, settings: Settings) -> None:
        """Initialize FMP client.

        Args:
            settings: Application settings with API key and cache TTLs.

        """
        self.settings = settings
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

        # Separate caches with configurable TTLs
        self._treasury_cache = EconomicDataCache(ttl_hours=settings.treasury_rates_cache_ttl_hours)
        self._economic_cache = EconomicDataCache(
            ttl_hours=settings.economic_indicators_cache_ttl_hours
        )
        self._etf_cache = ETFHoldingsCache(ttl_hours=settings.etf_holdings_cache_ttl_hours)
        # Company profile cache: {symbol: (data, timestamp_epoch)}
        self._company_profile_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._company_profile_ttl = 24 * 3600.0  # 24 hours in seconds

    async def __aenter__(self) -> "FMPClient":
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def _get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make GET request to FMP API.

        Args:
            endpoint: API endpoint (relative to BASE_URL).
            params: Query parameters.

        Returns:
            JSON response data.

        Raises:
            httpx.HTTPError: If request fails.

        """
        url = f"{self.BASE_URL}/{endpoint}"
        query_params = {**(params or {}), "apikey": self.api_key}

        log_api_call(logger, "FMP", endpoint, params)

        try:
            response = await self.client.get(url, params=query_params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            log_error(logger, e, context={"endpoint": endpoint, "params": params})
            raise

    # ─────────────────────────────────────────────────────────────────
    # ETF Data
    # ─────────────────────────────────────────────────────────────────

    async def get_etf_holdings(self, symbol: str) -> list[ETFHolding] | None:
        """Get ETF holdings from FMP API.

        Args:
            symbol: ETF ticker symbol.

        Returns:
            List of holdings or None if unavailable.

        """
        # Check cache first
        cached = self._etf_cache.get(symbol)
        if cached is not None:
            logger.info("etf_holdings_cache_hit", symbol=symbol)
            return cached

        try:
            data = await self._get("etf/holdings", {"symbol": symbol})

            if not data or not isinstance(data, list):
                return None

            holdings = [
                ETFHolding(
                    etf_symbol=symbol,
                    asset_symbol=h.get("asset", ""),
                    name=h.get("name", ""),
                    weight_percentage=Decimal(str(h.get("weightPercentage", 0))),
                    market_value=(Decimal(str(h["marketValue"])) if h.get("marketValue") else None),
                    shares=h.get("sharesNumber"),
                    isin=h.get("isin"),
                    updated_at=h.get("updatedAt", ""),
                )
                for h in data
                if h.get("asset")  # Skip entries without asset symbol
            ]

            # Cache the results
            self._etf_cache.set(symbol, holdings)
            logger.info("etf_holdings_fetched", symbol=symbol, count=len(holdings))

            return holdings

        except httpx.HTTPStatusError as e:
            # Let HTTP errors bubble up with full details
            log_error(logger, e, context={"symbol": symbol, "operation": "get_etf_holdings"})
            raise
        except Exception as e:
            # For other errors (parsing, etc.), log and re-raise
            log_error(logger, e, context={"symbol": symbol, "operation": "get_etf_holdings"})
            raise

    async def get_etf_info(self, symbol: str) -> ETFInfo | None:
        """Get ETF information from FMP API.

        Args:
            symbol: ETF ticker symbol.

        Returns:
            ETF info or None if unavailable.

        """
        try:
            data = await self._get("etf/info", {"symbol": symbol})

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            info = data[0]
            sector_weights = {}
            if "sectorsList" in info:
                for sector in info["sectorsList"]:
                    sector_weights[sector.get("industry", "Other")] = Decimal(
                        str(sector.get("exposure", 0))
                    )

            return ETFInfo(
                symbol=info.get("symbol", symbol),
                name=info.get("name", ""),
                expense_ratio=Decimal(str(info.get("expenseRatio", 0))),
                aum=Decimal(str(info.get("assetsUnderManagement", 0))),
                holdings_count=info.get("holdingsCount", 0),
                inception_date=info.get("inceptionDate", ""),
                sector_weights=sector_weights,
                updated_at=info.get("updatedAt", ""),
            )

        except httpx.HTTPStatusError as e:
            log_error(logger, e, context={"symbol": symbol, "operation": "get_etf_info"})
            raise
        except Exception as e:
            log_error(logger, e, context={"symbol": symbol, "operation": "get_etf_info"})
            raise

    async def get_etf_sector_weightings(self, symbol: str) -> dict[str, Decimal] | None:
        """Get ETF sector weightings from FMP API.

        Args:
            symbol: ETF ticker symbol.

        Returns:
            Dictionary of sector -> weight percentage, or None if unavailable.

        """
        try:
            data = await self._get("etf/sector-weightings", {"symbol": symbol})

            if not data or not isinstance(data, list):
                return None

            return {
                item.get("sector", "Other"): Decimal(str(item.get("weightPercentage", 0)))
                for item in data
            }

        except Exception as e:
            log_error(
                logger, e, context={"symbol": symbol, "operation": "get_etf_sector_weightings"}
            )
            return None

    # ─────────────────────────────────────────────────────────────────
    # Economic Data (Internal use - not exposed as tools)
    # ─────────────────────────────────────────────────────────────────

    async def get_treasury_rates(self) -> dict[str, float] | None:
        """Get current Treasury rates for all maturities.

        Returns:
            Dictionary with rates for each maturity (month1, month3, year1, etc.)
            or None if unavailable.

        """
        # Check cache first
        cached = self._treasury_cache.get("treasury_rates")
        if cached is not None:
            logger.info("treasury_rates_cache_hit")
            return cast(dict[str, float], cached)

        try:
            data = await self._get("treasury-rates")

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            rates: dict[str, float] = data[0]
            # Cache the results
            self._treasury_cache.set("treasury_rates", rates)
            logger.info("treasury_rates_fetched", date=rates.get("date"))

            return rates

        except httpx.HTTPStatusError as e:
            log_error(logger, e, context={"operation": "get_treasury_rates"})
            raise
        except Exception as e:
            log_error(logger, e, context={"operation": "get_treasury_rates"})
            raise

    async def get_risk_free_rate(self) -> Decimal:
        """Get current 3-month Treasury rate for risk-free rate calculations.

        Returns:
            Risk-free rate as decimal (e.g., 0.0545 for 5.45%).
            Falls back to default if API fails.

        """
        try:
            rates = await self.get_treasury_rates()
            if rates and "month3" in rates:
                # API returns percent (5.45), convert to decimal (0.0545)
                return Decimal(str(rates["month3"])) / 100
            logger.warning("risk_free_rate_fallback", reason="month3 not in response")
        except Exception as e:
            logger.warning("risk_free_rate_fallback", error=str(e), error_type=type(e).__name__)

        return FALLBACK_RISK_FREE_RATE

    async def get_economic_indicator(self, name: str) -> Decimal | None:
        """Get economic indicator value from FMP API.

        Args:
            name: Indicator name (e.g., "inflationRate", "GDP", "unemploymentRate").

        Returns:
            Latest indicator value or None if unavailable.

        """
        # Check cache first
        cache_key = f"indicator_{name}"
        cached = self._economic_cache.get(cache_key)
        if cached is not None:
            logger.info("economic_indicator_cache_hit", indicator=name)
            return cast(Decimal, cached)

        try:
            data = await self._get("economic-indicators", {"name": name})

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            value = Decimal(str(data[0]["value"]))
            # Cache the results
            self._economic_cache.set(cache_key, value)
            logger.info("economic_indicator_fetched", indicator=name, value=float(value))

            return value

        except Exception as e:
            log_error(logger, e, context={"indicator": name, "operation": "get_economic_indicator"})
            return None

    async def get_inflation_rate(self) -> Decimal:
        """Get current inflation rate (CPI-based).

        Returns:
            Inflation rate as decimal (e.g., 0.025 for 2.5%).

        """
        try:
            rate = await self.get_economic_indicator("inflationRate")
            if rate is not None:
                # API returns percent (2.5), convert to decimal (0.025)
                return rate / 100
        except Exception as e:
            logger.warning("inflation_rate_fallback", error=str(e))

        return FALLBACK_INFLATION

    async def get_company_profile(self, symbol: str) -> dict[str, Any] | None:
        """Get company profile (for sector information). Uses 24h cache.

        Args:
            symbol: Stock ticker symbol.

        Returns:
            Company profile dict or None if unavailable.

        """
        # Check cache first
        cached = self._company_profile_cache.get(symbol)
        if cached is not None:
            data, cached_at = cached
            if time.monotonic() - cached_at < self._company_profile_ttl:
                logger.info("company_profile_cache_hit", symbol=symbol)
                return data

        try:
            data = await self._get("profile", {"symbol": symbol})

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            result = cast(dict[str, Any], data[0])
            # Cache the result
            self._company_profile_cache[symbol] = (result, time.monotonic())
            return result

        except Exception as e:
            log_error(logger, e, context={"symbol": symbol, "operation": "get_company_profile"})
            return None

    async def get_company_profiles_batch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Get company profiles for multiple symbols in parallel.

        Uses cached values where available, fetches uncached in parallel.

        Args:
            symbols: List of stock ticker symbols.

        Returns:
            Map of symbol to company profile dict.

        """
        results: dict[str, dict[str, Any]] = {}
        to_fetch: list[str] = []

        # Check cache first for each symbol
        for symbol in symbols:
            cached = self._company_profile_cache.get(symbol)
            if cached is not None:
                data, cached_at = cached
                if time.monotonic() - cached_at < self._company_profile_ttl:
                    results[symbol] = data
                    continue
            to_fetch.append(symbol)

        if to_fetch:
            logger.info(
                "company_profiles_batch_fetch",
                cached=len(results),
                fetching=len(to_fetch),
            )
            tasks = [self.get_company_profile(s) for s in to_fetch]
            fetched: list[dict[str, Any] | None | BaseException] = await asyncio.gather(
                *tasks, return_exceptions=True
            )
            for symbol, raw_result in zip(to_fetch, fetched, strict=True):
                if isinstance(raw_result, BaseException):
                    logger.warning(
                        "company_profile_fetch_failed",
                        symbol=symbol,
                        error=str(raw_result),
                    )
                elif raw_result is not None:
                    results[symbol] = raw_result

        return results

    # ─────────────────────────────────────────────────────────────────
    # Historical Prices
    # ─────────────────────────────────────────────────────────────────

    async def get_historical_prices(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Get historical daily prices for a symbol.

        Args:
            symbol: Ticker symbol.
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).

        Returns:
            List of price dicts with date, close, and OHLCV fields.
            Sorted by date ascending.

        Raises:
            httpx.HTTPError: If request fails.

        """
        data = await self._get(
            "historical-price-eod/full",
            {"symbol": symbol, "from": from_date, "to": to_date},
        )

        if not data or not isinstance(data, list):
            return []

        # FMP returns newest first; sort ascending by date
        data.sort(key=lambda d: d.get("date", ""))
        return cast(list[dict[str, Any]], data)

    async def get_historical_prices_multi(
        self,
        symbols: list[str],
        from_date: str,
        to_date: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get historical prices for multiple symbols in parallel.

        Args:
            symbols: List of ticker symbols.
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).

        Returns:
            Map of symbol to price data list. Symbols that failed are omitted.

        """
        tasks = [self.get_historical_prices(s, from_date, to_date) for s in symbols]
        results_raw: list[list[dict[str, Any]] | BaseException] = await asyncio.gather(
            *tasks, return_exceptions=True
        )

        results: dict[str, list[dict[str, Any]]] = {}
        for symbol, raw_result in zip(symbols, results_raw, strict=True):
            if isinstance(raw_result, BaseException):
                logger.warning(
                    "historical_prices_fetch_failed",
                    symbol=symbol,
                    error=str(raw_result),
                )
            elif raw_result:
                results[symbol] = raw_result

        return results

    # ─────────────────────────────────────────────────────────────────
    # Quotes
    # ─────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> dict[str, Any] | None:
        """Get current quote for a symbol.

        Args:
            symbol: Ticker symbol.

        Returns:
            Quote dict with price, volume, etc. or None if unavailable.

        """
        try:
            data = await self._get("quote", {"symbol": symbol})

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            return cast(dict[str, Any], data[0])

        except Exception as e:
            log_error(logger, e, context={"symbol": symbol, "operation": "get_quote"})
            return None

    async def get_quotes_batch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Get current quotes for multiple symbols in a single request.

        Args:
            symbols: List of ticker symbols.

        Returns:
            Map of symbol to quote dict. Symbols not returned are omitted.

        """
        if not symbols:
            return {}

        joined = ",".join(symbols)
        try:
            data = await self._get("batch-quote", {"symbols": joined})
        except Exception as e:
            log_error(logger, e, context={"operation": "get_quotes_batch"})
            return {}

        if not isinstance(data, list):
            return {}

        return {
            quote["symbol"]: quote
            for quote in data
            if isinstance(quote, dict) and "symbol" in quote
        }

    # ─────────────────────────────────────────────────────────────────
    # Health Check
    # ─────────────────────────────────────────────────────────────────

    async def health_check(self, timeout: float = 5.0) -> bool:
        """Verify FMP API connectivity.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            True if API is accessible, False otherwise.

        """
        try:
            # Use a simple endpoint to verify connectivity
            response = await self.client.get(
                f"{self.BASE_URL}/quote",
                params={"symbol": "AAPL", "apikey": self.api_key},
                timeout=timeout,
            )
            response.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.TimeoutException):
            return False
