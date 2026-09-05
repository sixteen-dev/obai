"""FMP (Financial Modeling Prep) API client for historical OHLCV data."""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call

logger = get_logger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0
FALLBACK_RISK_FREE_RATE = 0.045  # 3-month T-bill fallback when FMP unavailable
# FMP truncates a treasury-rates response at roughly one quarter, so a window
# is fetched in chunks this size. The chunk cap must admit the longest window
# the strategy schema accepts (``TIMEFRAME_MAX_YEARS["daily"]``, 30 years) so
# no valid backtest falls back to the constant; a test pins the two together.
TREASURY_CHUNK_DAYS = 90
MAX_RISK_FREE_WINDOW_YEARS = 30
MAX_TREASURY_CHUNKS = math.ceil(MAX_RISK_FREE_WINDOW_YEARS * 366 / TREASURY_CHUNK_DAYS)
# Memoized windows per client. A window ending today is refetched on the next
# UTC day so a partial window does not freeze for the life of the process.
MAX_RISK_FREE_MEMO_ENTRIES = 64

_APIKEY_PATTERN = re.compile(r"apikey=[^&\s]+")

# FMP's dividend-adjusted EOD endpoint returns adjusted OHLC under adj-prefixed
# keys; downstream OHLCV parsing reads the canonical open/high/low/close keys.
_ADJUSTED_FIELD_MAP = {
    "adjOpen": "open",
    "adjHigh": "high",
    "adjLow": "low",
    "adjClose": "close",
}


def _normalize_adjusted_row(row: dict[str, Any]) -> dict[str, Any]:
    """Fold FMP dividend-adjusted OHLC fields onto the canonical OHLCV keys.

    Args:
        row: One candle from the dividend-adjusted daily endpoint.

    Returns:
        Row with adjOpen/adjHigh/adjLow/adjClose moved onto open/high/low/close.
        Rows already in canonical shape are returned unchanged.

    """
    normalized = dict(row)
    for adj_key, canonical_key in _ADJUSTED_FIELD_MAP.items():
        if adj_key in normalized:
            normalized[canonical_key] = normalized.pop(adj_key)
    return normalized


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


def _parse_rate_window(start_date: str, end_date: str) -> tuple[date, date]:
    """Parse an inclusive risk-free rate window.

    Args:
        start_date: Window start in YYYY-MM-DD format.
        end_date: Window end in YYYY-MM-DD format.

    Returns:
        Tuple of (start, end) dates.

    Raises:
        ValueError: If either date is not ISO format or start is after end.

    """
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        msg = f"risk-free window dates must be ISO YYYY-MM-DD; got {start_date!r}..{end_date!r}"
        raise ValueError(msg) from exc
    if start > end:
        msg = f"risk-free window start_date {start_date} is after end_date {end_date}"
        raise ValueError(msg)
    return start, end


def _month3_by_date(
    rows: list[Any],
    chunk_start: date,
    chunk_end: date,
) -> dict[str, float]:
    """Index the 3-month treasury yield of each in-window row by its date.

    The provider truncates wide requests to their most recent rows and, with no
    range at all, returns the latest quotes. A row dated outside the requested
    chunk is therefore evidence the range was not honoured, and letting it into
    the mean would relabel today's yield as the window's. Such rows are counted
    and logged, never averaged.

    Args:
        rows: Treasury-rates rows as the provider returned them.
        chunk_start: First day the rows were requested for (inclusive).
        chunk_end: Last day the rows were requested for (inclusive).

    Returns:
        Mapping of ISO date to that day's ``month3`` percent value. A row that
        is not a dict, carries no parseable date, lies outside the chunk, or
        whose ``month3`` is missing, boolean, non-numeric or non-finite is
        skipped rather than coerced into a number.

    """
    values: dict[str, float] = {}
    outside = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_day = _row_date(row.get("date"))
        month3 = row.get("month3")
        if row_day is None:
            continue
        if not chunk_start <= row_day <= chunk_end:
            outside += 1
            continue
        if isinstance(month3, bool) or not isinstance(month3, int | float):
            continue
        if not math.isfinite(month3):
            continue
        values[row_day.isoformat()] = float(month3)
    if outside or not values:
        logger.warning(
            "treasury_chunk_rows_outside_window" if outside else "treasury_chunk_empty",
            chunk_start=chunk_start.isoformat(),
            chunk_end=chunk_end.isoformat(),
            rows_outside_window=outside,
            rows_in_window=len(values),
        )
    return values


def _row_date(value: object) -> date | None:
    """Parse a provider row's date, returning None when it is not an ISO date."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _utc_today() -> date:
    """Return today's UTC date; a seam so tests can move the calendar."""
    return datetime.now(UTC).date()


# The adjustment basis stored prices sit on. Changing the endpoint above must
# change this value too: a cache written on the old basis is not comparable
# with rows fetched on the new one, and mixing them fabricates price moves.
DAILY_PRICE_BASIS = "dividend_adjusted"
INTRADAY_PRICE_BASIS = "raw"


def price_basis_for(timeframe: str) -> str:
    """Return the adjustment basis prices are stored on for a timeframe.

    Args:
        timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

    Returns:
        The basis identifier persisted alongside the cached rows.

    """
    return DAILY_PRICE_BASIS if timeframe == "daily" else INTRADAY_PRICE_BASIS


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
        # The risk-free rate describes a backtest window, so it is memoized per
        # (start, end, UTC day): walk-forward's ~2N folds share one window and
        # therefore one lookup, a second window is its own entry, and a window
        # still accruing yields is looked up afresh each day. The dict is
        # bounded; the oldest entry is evicted first.
        self._rfr_cache: dict[tuple[str, str, str], tuple[float, str]] = {}

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
        """Fetch daily OHLCV data for a symbol (split- and dividend-adjusted).

        Uses FMP's dividend-adjusted EOD endpoint so daily closes are on a
        total-return basis (reinvested dividends), folding the adjusted OHLC
        (adjOpen/adjHigh/adjLow/adjClose) onto the canonical open/high/low/close
        keys so downstream parsing is unchanged. Intraday data keeps raw prices.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL").
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.

        Returns:
            List of OHLCV dicts with keys: date, open, high, low, close, volume.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.

        """
        endpoint = "historical-price-eod/dividend-adjusted"
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
            for row in (_normalize_adjusted_row(r) for r in data)
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

    async def get_period_risk_free_rate_with_source(
        self,
        start_date: str,
        end_date: str,
    ) -> tuple[float, str]:
        """Resolve the mean 3-month treasury rate over a backtest window.

        Sharpe, Sortino and alpha must not move because today's yield moved
        while the historical prices did not, so the rate is the average of the
        daily 3-month yields printed inside the requested window rather than
        the latest quote. The result is memoized per window, so walk-forward's
        ~2N folds share one lookup. Provider failures never raise — a rate
        lookup must not break a backtest.

        Args:
            start_date: First day of the window (YYYY-MM-DD, inclusive).
            end_date: Last day of the window (YYYY-MM-DD, inclusive).

        Returns:
            Tuple of (annual risk-free rate as decimal, source label):
            ``(mean, "treasury_3m_period_mean")`` on success, or
            ``(FALLBACK_RISK_FREE_RATE, "fallback")`` when the provider fails,
            returns no usable yield, or the window exceeds the chunk cap.

        Raises:
            ValueError: If a date is not ISO format or start_date is after
                end_date. That is a caller bug, not a provider failure.

        """
        start, end = _parse_rate_window(start_date, end_date)
        key = (start_date, end_date, _utc_today().isoformat())
        cached = self._rfr_cache.get(key)
        if cached is not None:
            return cached

        result = await self._resolve_period_risk_free_rate(start, end)
        if len(self._rfr_cache) >= MAX_RISK_FREE_MEMO_ENTRIES:
            self._rfr_cache.pop(next(iter(self._rfr_cache)))
        self._rfr_cache[key] = result
        return result

    async def _resolve_period_risk_free_rate(
        self,
        start: date,
        end: date,
    ) -> tuple[float, str]:
        """Average the window's 3-month yields, falling back on any failure."""
        span_days = (end - start).days + 1
        chunk_count = math.ceil(span_days / TREASURY_CHUNK_DAYS)
        if chunk_count > MAX_TREASURY_CHUNKS:
            logger.warning(
                "risk_free_rate_fallback",
                reason="window exceeds the treasury chunk cap",
                chunks_required=chunk_count,
                max_chunks=MAX_TREASURY_CHUNKS,
            )
            return FALLBACK_RISK_FREE_RATE, "fallback"

        by_date: dict[str, float] = {}
        try:
            for chunk_start, chunk_end in _build_date_chunks(start, end, TREASURY_CHUNK_DAYS):
                rows = await self._fetch_treasury_chunk(chunk_start, chunk_end)
                by_date.update(_month3_by_date(rows, chunk_start, chunk_end))
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "risk_free_rate_fallback",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return FALLBACK_RISK_FREE_RATE, "fallback"

        if not by_date:
            logger.warning(
                "risk_free_rate_fallback",
                reason="no 3-month yield in the window",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            return FALLBACK_RISK_FREE_RATE, "fallback"

        return fmean(by_date.values()) / 100, "treasury_3m_period_mean"

    async def _fetch_treasury_chunk(
        self,
        chunk_start: date,
        chunk_end: date,
    ) -> list[Any]:
        """Fetch one chunk of daily treasury rates.

        Args:
            chunk_start: First day of the chunk (inclusive).
            chunk_end: Last day of the chunk (inclusive).

        Returns:
            The provider's rows, or an empty list when the body is not a list.

        Raises:
            httpx.HTTPError: If the request ultimately fails after retries.

        """
        data = await self._request_with_retry(
            "treasury-rates",
            {
                "apikey": self.api_key,
                "from": chunk_start.isoformat(),
                "to": chunk_end.isoformat(),
            },
        )
        if not isinstance(data, list):
            logger.warning(
                "unexpected_response_type",
                endpoint="treasury-rates",
                type=type(data).__name__,
            )
            return []
        return data

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
