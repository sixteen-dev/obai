"""FINRA Query API client for daily short-sale volume (Reg SHO daily dataset).

FINRA publishes off-exchange short-sale volume per symbol, per trading day and
per reporting facility (the Nasdaq/NYSE TRFs and the ADF). The dataset is
public: no API key. One day's facility rows add up to the consolidated
``CNMSshvol{YYYYMMDD}.txt`` figure FINRA posts on its CDN.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Final

import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call, log_error
from ..utils import is_retryable_httpx_exc, retry_async

logger = get_logger(__name__)

_DATASET_PATH: Final = "data/group/otcMarket/name/regShoDaily"
_SYMBOL_FIELD: Final = "securitiesInformationProcessorSymbolIdentifier"
FINRA_MAX_ROWS: Final = 5000
"""FINRA's per-request record cap; a year of one symbol is about 760 rows."""


def consolidate_facilities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sum one symbol's facility rows into one row per trading day, newest first.

    Args:
        rows: FINRA regShoDaily records for one symbol.

    Returns:
        Rows with symbol, ISO date, shortVolume, shortExemptVolume and
        totalVolume. Sums use Decimal so they match FINRA's consolidated file.
    """
    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 3)
    symbols: dict[str, str] = {}
    for row in rows:
        day = row["tradeReportDate"]
        symbols[day] = row[_SYMBOL_FIELD]
        sums = totals[day]
        sums[0] += Decimal(str(row["shortParQuantity"]))
        sums[1] += Decimal(str(row["shortExemptParQuantity"]))
        sums[2] += Decimal(str(row["totalParQuantity"]))
    return [
        {
            "symbol": symbols[day],
            "date": day,
            "shortVolume": float(short),
            "shortExemptVolume": float(exempt),
            "totalVolume": float(total),
        }
        for day, (short, exempt, total) in sorted(totals.items(), reverse=True)
    ]


class FINRAClient:
    """Client for FINRA's public Query API — daily short-sale volume."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the FINRA client.

        Args:
            settings: Application settings with the FINRA base URL.
        """
        self.base_url = settings.finra_base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> "FINRAClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_daily_short_volume(
        self, symbol: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Get a symbol's consolidated daily short-sale volume over a date range.

        Args:
            symbol: Ticker symbol, as FINRA reports it (e.g., 'AAPL').
            start: First trade date, inclusive.
            end: Last trade date, inclusive.

        Returns:
            One row per trading day, newest first; [] when FINRA has none.

        Raises:
            httpx.HTTPError: On request failure.
            ValueError: If FINRA reports more records than one request returns.
        """
        body = {
            "limit": FINRA_MAX_ROWS,
            "compareFilters": [
                {"compareType": "EQUAL", "fieldName": _SYMBOL_FIELD, "fieldValue": symbol}
            ],
            "dateRangeFilters": [
                {
                    "fieldName": "tradeReportDate",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                }
            ],
        }
        rows = await self._query(body)
        return consolidate_facilities(rows)

    @retry_async(
        max_attempts=3,
        initial_delay=0.5,
        backoff=2.0,
        jitter=0.25,
        retry_on=(httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError),
        retry_if=is_retryable_httpx_exc,
    )
    async def _query(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """POST one query to the regShoDaily dataset.

        Args:
            body: FINRA query (limit, filters).

        Returns:
            The matching records; [] on 204 No Content (no match).

        Raises:
            httpx.HTTPError: On request failure.
            ValueError: If ``record-total`` exceeds the records returned.
        """
        url = f"{self.base_url}/{_DATASET_PATH}"
        log_api_call(logger, "finra", _DATASET_PATH, body)
        try:
            response = await self.client.post(
                url, json=body, headers={"Accept": "application/json"}
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            log_error(logger, e, context={"endpoint": _DATASET_PATH})
            raise
        if response.status_code == httpx.codes.NO_CONTENT:
            return []
        rows: list[dict[str, Any]] = response.json()
        total = int(response.headers.get("record-total", len(rows)))
        if total > len(rows):
            raise ValueError(f"FINRA response truncated: {len(rows)} of {total} records returned")
        return rows
