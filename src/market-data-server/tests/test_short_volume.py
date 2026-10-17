"""Tests for daily short-sale volume, served by FINRA's Reg SHO daily dataset."""

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.finra_client import FINRA_MAX_ROWS, FINRAClient, consolidate_facilities
from src.config import Settings
from src.server import market_data_get_short_volume_tool
from src.tools.technical import (
    MAX_SHORT_VOLUME_DAYS,
    SHORT_VOLUME_HISTORY_DAYS,
    get_short_volume,
)

# AAPL on 2026-10-09 as FINRA's API reports it, one row per reporting facility.
# The sums equal that day's consolidated CNMSshvol20261009.txt row:
# short 9011284.413782, exempt 27097, total 15300955.899485.
AAPL_FACILITY_ROWS: list[dict[str, Any]] = [
    {
        "reportingFacilityCode": "NCTRF",
        "tradeReportDate": "2026-10-09",
        "securitiesInformationProcessorSymbolIdentifier": "AAPL",
        "shortParQuantity": 117560.542903,
        "shortExemptParQuantity": 80,
        "totalParQuantity": 242469.809061,
        "marketCode": "B",
    },
    {
        "reportingFacilityCode": "NQTRF",
        "tradeReportDate": "2026-10-09",
        "securitiesInformationProcessorSymbolIdentifier": "AAPL",
        "shortParQuantity": 8538628.852349,
        "shortExemptParQuantity": 26977,
        "totalParQuantity": 14457459.097314,
        "marketCode": "Q",
    },
    {
        "reportingFacilityCode": "NYTRF",
        "tradeReportDate": "2026-10-09",
        "securitiesInformationProcessorSymbolIdentifier": "AAPL",
        "shortParQuantity": 355095.01853,
        "shortExemptParQuantity": 40,
        "totalParQuantity": 601026.99311,
        "marketCode": "N",
    },
]


def _response(status: int, rows: list[dict[str, Any]] | None, total: int | None) -> MagicMock:
    """Build a FINRA API response: JSON rows plus the record-total header."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.raise_for_status = MagicMock()
    response.headers = {} if total is None else {"record-total": str(total)}
    response.json = MagicMock(return_value=rows)
    return response


def _days(n: int, last: date) -> list[dict[str, Any]]:
    """Return n consolidated days, newest first, ending at ``last``."""
    return [
        {
            "symbol": "AAPL",
            "date": (last - timedelta(days=i)).isoformat(),
            "shortVolume": 100.0 + i,
            "shortExemptVolume": 0.0,
            "totalVolume": 200.0 + i,
        }
        for i in range(n)
    ]


class TestConsolidateFacilities:
    """Facility rows of one day add up to FINRA's consolidated daily figure."""

    def test_sums_facilities_per_day(self) -> None:
        result = consolidate_facilities(AAPL_FACILITY_ROWS)

        assert result == [
            {
                "symbol": "AAPL",
                "date": "2026-10-09",
                "shortVolume": 9011284.413782,
                "shortExemptVolume": 27097.0,
                "totalVolume": 15300955.899485,
            }
        ]

    def test_orders_days_newest_first(self) -> None:
        older = {**AAPL_FACILITY_ROWS[0], "tradeReportDate": "2026-10-08"}

        result = consolidate_facilities([older, *AAPL_FACILITY_ROWS])

        assert [row["date"] for row in result] == ["2026-10-09", "2026-10-08"]


class TestFINRAClient:
    """FINRAClient queries one symbol over a date range in a single request."""

    @pytest.mark.asyncio
    async def test_posts_symbol_and_date_range(self, mock_settings: Settings) -> None:
        response = _response(200, AAPL_FACILITY_ROWS, total=3)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = response
            async with FINRAClient(mock_settings) as client:
                result = await client.get_daily_short_volume(
                    "AAPL", date(2025, 10, 10), date(2026, 10, 11)
                )

        assert result == consolidate_facilities(AAPL_FACILITY_ROWS)
        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
        assert url == "https://api.finra.org/data/group/otcMarket/name/regShoDaily"
        assert body["limit"] == FINRA_MAX_ROWS
        assert body["compareFilters"] == [
            {
                "compareType": "EQUAL",
                "fieldName": "securitiesInformationProcessorSymbolIdentifier",
                "fieldValue": "AAPL",
            }
        ]
        assert body["dateRangeFilters"] == [
            {"fieldName": "tradeReportDate", "startDate": "2025-10-10", "endDate": "2026-10-11"}
        ]

    @pytest.mark.asyncio
    async def test_no_content_means_no_data(self, mock_settings: Settings) -> None:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _response(204, None, total=None)
            async with FINRAClient(mock_settings) as client:
                result = await client.get_daily_short_volume(
                    "ZZZZQX", date(2025, 10, 10), date(2026, 10, 11)
                )

        assert result == []

    @pytest.mark.asyncio
    async def test_truncated_response_raises(self, mock_settings: Settings) -> None:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _response(200, AAPL_FACILITY_ROWS, total=FINRA_MAX_ROWS + 1)
            async with FINRAClient(mock_settings) as client:
                with pytest.raises(ValueError, match="truncated"):
                    await client.get_daily_short_volume(
                        "AAPL", date(2025, 10, 10), date(2026, 10, 11)
                    )


class TestShortVolumeTool:
    """get_short_volume pages the last year of FINRA days, newest first."""

    @staticmethod
    def _patch_client(days: list[dict[str, Any]]) -> tuple[Any, AsyncMock]:
        client = AsyncMock()
        client.get_daily_short_volume = AsyncMock(return_value=days)
        patcher = patch("src.tools.technical.FINRAClient")
        mock_cls = patcher.start()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        return patcher, client

    @pytest.mark.asyncio
    async def test_queries_one_year_and_pages(self) -> None:
        today = date(2026, 10, 11)
        patcher, client = self._patch_client(_days(25, date(2026, 10, 9)))
        try:
            with patch("src.tools.technical.get_settings"):
                result = await get_short_volume("aapl", limit=20, offset=0, today=today)
        finally:
            patcher.stop()

        client.get_daily_short_volume.assert_awaited_once_with(
            "AAPL", today - timedelta(days=SHORT_VOLUME_HISTORY_DAYS), today
        )
        assert result["symbol"] == "AAPL"
        assert "FINRA" in result["source"]
        assert [row["date"] for row in result["data"]][:2] == ["2026-10-09", "2026-10-08"]
        assert result["pagination"] == {
            "limit": 20,
            "requested_limit": 20,
            "offset": 0,
            "returned": 20,
            "total_count": 25,
            "has_more": True,
            "next_offset": 20,
        }

    @pytest.mark.asyncio
    async def test_limit_above_max_is_clamped(self) -> None:
        patcher, _ = self._patch_client(_days(MAX_SHORT_VOLUME_DAYS * 2, date(2026, 10, 9)))
        try:
            with patch("src.tools.technical.get_settings"):
                result = await get_short_volume(
                    "AAPL", limit=MAX_SHORT_VOLUME_DAYS * 2, today=date(2026, 10, 11)
                )
        finally:
            patcher.stop()

        assert result["pagination"]["limit"] == MAX_SHORT_VOLUME_DAYS
        assert result["pagination"]["requested_limit"] == MAX_SHORT_VOLUME_DAYS * 2
        assert result["pagination"]["returned"] == MAX_SHORT_VOLUME_DAYS

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("symbol", "limit", "offset"), [("", 20, 0), ("AAPL", 0, 0), ("AAPL", 20, -1)]
    )
    async def test_rejects_invalid_arguments(self, symbol: str, limit: int, offset: int) -> None:
        with pytest.raises(ValueError):
            await get_short_volume(symbol, limit=limit, offset=offset, today=date(2026, 10, 11))


class TestShortVolumeServerTool:
    """The MCP wrapper names FINRA, not FMP, when the request fails."""

    @pytest.mark.asyncio
    async def test_http_error_names_finra(self) -> None:
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503
        error = httpx.HTTPStatusError("down", request=MagicMock(), response=error_response)

        with patch("src.server.get_short_volume", new=AsyncMock(side_effect=error)):
            result = await market_data_get_short_volume_tool("AAPL")

        assert result["isError"] is True
        assert result["error"].startswith("FINRA:")
