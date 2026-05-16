"""FMP (Financial Modeling Prep) API client for historical OHLCV data."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Any

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call

logger = get_logger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0

_APIKEY_PATTERN = re.compile(r"apikey=[^&\s]+")


def _scrub_url(text: str) -> str:
    """Remove apikey query param from exception messages.

    Args:
        text: String that may contain apikey=... in a URL.

    Returns:
        String with apikey value replaced.

    """
    return _APIKEY_PATTERN.sub("apikey=***", text)


# Map our timeframe names to FMP endpoint suffixes
TIMEFRAME_TO_FMP_ENDPOINT: dict[str, str] = {
    "daily": "historical-price-eod/full",
    "1hour": "historical-chart/1hour",
    "15min": "historical-chart/15min",
    "5min": "historical-chart/5min",
}


def _build_date_chunks(
    start: date,
    end: date,
    chunk_days: int,
) -> list[tuple[date, date]]:
    """Split a date range into chunks of N calendar days.

    Args:
        start: Start date (inclusive).
        end: End date (inclusive).
        chunk_days: Max calendar days per chunk.

    Returns:
        List of (chunk_start, chunk_end) tuples.

    """
    chunks: list[tuple[date, date]] = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


class FMPClient:
    """Client for fetching historical OHLCV data from FMP API."""

    BASE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self, settings: Settings) -> None:
        """Initialize FMP client.

        Args:
            settings: Application settings with API key.

        """
        self.api_key = settings.fmp_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> FMPClient:
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

    async def get_historical_daily(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch daily OHLCV data for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL").
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            List of OHLCV dicts with keys: date, open, high, low, close, volume.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.

        """
        endpoint = "historical-price-eod/full"
        params: dict[str, str] = {"apikey": self.api_key, "symbol": symbol}
        if start_date:
            params["from"] = start_date
        if end_date:
            params["to"] = end_date

        log_api_call(
            logger,
            service="FMP",
            endpoint=endpoint,
            params={"symbol": symbol, "from": start_date, "to": end_date},
        )

        data = await self._request_with_retry(endpoint, params)

        if not isinstance(data, list):
            logger.warning("unexpected_response_type", symbol=symbol, type=type(data).__name__)
            return []

        return [
            {
                "date": row.get("date", ""),
                "open": row.get("open", 0.0),
                "high": row.get("high", 0.0),
                "low": row.get("low", 0.0),
                "close": row.get("close", 0.0),
                "volume": row.get("volume", 0),
            }
            for row in data
        ]

    # FMP caps intraday responses to ~7 calendar days per request
    # regardless of the from/to range. Chunk into windows to get full history.
    _INTRADAY_CHUNK_DAYS = 7

    async def get_historical_intraday(
        self,
        symbol: str,
        timeframe: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch intraday OHLCV data for a symbol.

        Automatically chunks into weekly requests because FMP caps
        intraday responses to ~7 calendar days per API call.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL").
            timeframe: Bar interval ("5min", "15min", "1hour").
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            List of OHLCV dicts with datetime string in 'date' key.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
            ValueError: If timeframe is not supported.

        """
        endpoint = TIMEFRAME_TO_FMP_ENDPOINT.get(timeframe)
        if endpoint is None or timeframe == "daily":
            msg = f"Use get_historical_daily() for daily data, not '{timeframe}'"
            raise ValueError(msg)

        if not start_date or not end_date:
            # Single request if no date range specified
            return await self._fetch_intraday_chunk(
                symbol,
                timeframe,
                endpoint,
                start_date,
                end_date,
            )

        # Chunk into weekly windows
        chunks = _build_date_chunks(
            date.fromisoformat(start_date),
            date.fromisoformat(end_date),
            self._INTRADAY_CHUNK_DAYS,
        )

        logger.info(
            "intraday_chunked_download",
            symbol=symbol,
            timeframe=timeframe,
            chunks=len(chunks),
            start=start_date,
            end=end_date,
        )

        # Fetch chunks in parallel with concurrency limit to avoid rate limits
        sem = asyncio.Semaphore(5)

        async def _bounded_fetch(cs: date, ce: date) -> list[dict[str, Any]]:
            async with sem:
                return await self._fetch_intraday_chunk(
                    symbol,
                    timeframe,
                    endpoint,
                    cs.isoformat(),
                    ce.isoformat(),
                )

        results = await asyncio.gather(
            *[_bounded_fetch(cs, ce) for cs, ce in chunks],
        )
        all_rows: list[dict[str, Any]] = []
        for rows in results:
            all_rows.extend(rows)

        logger.info(
            "intraday_download_complete",
            symbol=symbol,
            timeframe=timeframe,
            total_rows=len(all_rows),
            chunks_fetched=len(chunks),
        )
        return all_rows

    async def _fetch_intraday_chunk(
        self,
        symbol: str,
        timeframe: str,
        endpoint: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch a single chunk of intraday data from FMP.

        Args:
            symbol: Stock ticker symbol.
            timeframe: Bar interval.
            endpoint: FMP API endpoint.
            start_date: Chunk start date.
            end_date: Chunk end date.

        Returns:
            List of OHLCV dicts.

        """
        params: dict[str, str] = {"apikey": self.api_key, "symbol": symbol}
        if start_date:
            params["from"] = start_date
        if end_date:
            params["to"] = end_date

        log_api_call(
            logger,
            service="FMP",
            endpoint=endpoint,
            params={
                "symbol": symbol,
                "timeframe": timeframe,
                "from": start_date,
                "to": end_date,
            },
        )

        data = await self._request_with_retry(endpoint, params)

        if not isinstance(data, list):
            logger.warning(
                "unexpected_response_type",
                symbol=symbol,
                timeframe=timeframe,
                type=type(data).__name__,
            )
            return []

        return [
            {
                "date": row.get("date", ""),
                "open": row.get("open", 0.0),
                "high": row.get("high", 0.0),
                "low": row.get("low", 0.0),
                "close": row.get("close", 0.0),
                "volume": row.get("volume", 0),
            }
            for row in data
        ]

    async def health_check(self, timeout: float = 5.0) -> bool:
        """Check if FMP API is reachable.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            True if API is reachable.

        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/profile/AAPL",
                params={"apikey": self.api_key},
                timeout=timeout,
            )
            return response.status_code == 200  # noqa: PLR2004
        except (httpx.HTTPError, httpx.TimeoutException):
            return False

    async def _request_with_retry(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> Any:
        """Make an HTTP GET request with exponential backoff retry.

        Args:
            endpoint: API endpoint path.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: If all retries fail.

        """
        url = f"{self.BASE_URL}/{endpoint}"
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:  # noqa: PLR2004
                    backoff = INITIAL_BACKOFF_SECONDS * (2**attempt)
                    logger.warning(
                        "rate_limited",
                        attempt=attempt + 1,
                        backoff_seconds=backoff,
                    )
                    await asyncio.sleep(backoff)
                    last_error = exc
                    continue
                # Scrub apikey from the error before it propagates
                scrubbed = _scrub_url(str(exc))
                raise httpx.HTTPStatusError(
                    scrubbed,
                    request=exc.request,
                    response=exc.response,
                ) from None
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                json.JSONDecodeError,
            ) as exc:
                backoff = INITIAL_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "request_retryable_error",
                    error_type=type(exc).__name__,
                    attempt=attempt + 1,
                    backoff_seconds=backoff,
                    endpoint=endpoint,
                )
                await asyncio.sleep(backoff)
                last_error = exc

        if last_error is not None:
            # Scrub apikey from the final error too
            scrubbed = _scrub_url(str(last_error))
            if isinstance(last_error, httpx.HTTPStatusError):
                raise httpx.HTTPStatusError(
                    scrubbed,
                    request=last_error.request,
                    response=last_error.response,
                ) from None
            msg = f"All {MAX_RETRIES} retries exhausted for {endpoint}: {scrubbed}"
            raise httpx.HTTPError(msg) from None
        msg = f"All {MAX_RETRIES} retries exhausted for {endpoint}"
        raise httpx.HTTPError(msg)
