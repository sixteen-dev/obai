"""Tests for the sector-performance snapshot, which FMP now serves per date."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.fmp_client import FMPClient
from src.config import Settings
from src.tools.market import SNAPSHOT_LOOKBACK_DAYS, get_market_snapshot

FRIDAY_ROWS = [
    {"date": "2026-10-09", "sector": "Technology", "exchange": "NASDAQ", "averageChange": 1.2},
    {"date": "2026-10-09", "sector": "Utilities", "exchange": "NASDAQ", "averageChange": 2.8},
]


def _patched_tool_client(rows_by_date: dict[str, list[dict[str, Any]]]) -> tuple[Any, AsyncMock]:
    """Patch the tool's FMPClient so each date returns its rows ([] when absent)."""
    client = AsyncMock()
    client.get_sector_performance = AsyncMock(
        side_effect=lambda day: rows_by_date.get(day.isoformat(), [])
    )
    patcher = patch("src.tools.market.FMPClient")
    mock_cls = patcher.start()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return patcher, client


class TestSectorPerformanceClient:
    """FMPClient.get_sector_performance sends the date FMP requires."""

    @pytest.mark.asyncio
    async def test_sends_the_snapshot_date(self, mock_settings: Settings) -> None:
        response = MagicMock(spec=httpx.Response)
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=FRIDAY_ROWS)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = response
            async with FMPClient(mock_settings) as client:
                result = await client.get_sector_performance(date(2026, 10, 9))

        assert result == FRIDAY_ROWS
        url = mock_get.call_args.args[0]
        params = mock_get.call_args.kwargs["params"]
        assert url.endswith("/sector-performance-snapshot")
        assert params["date"] == "2026-10-09"


class TestMarketSnapshotTool:
    """get_market_snapshot walks back from today to the latest session with data."""

    @pytest.mark.asyncio
    async def test_weekend_falls_back_to_the_last_session(self) -> None:
        patcher, client = _patched_tool_client({"2026-10-09": FRIDAY_ROWS})
        try:
            with patch("src.tools.market.get_settings"):
                result = await get_market_snapshot(today=date(2026, 10, 11))
        finally:
            patcher.stop()

        assert result == {"data": FRIDAY_ROWS}
        asked = [call.args[0] for call in client.get_sector_performance.call_args_list]
        assert asked == [date(2026, 10, 11), date(2026, 10, 10), date(2026, 10, 9)]

    @pytest.mark.asyncio
    async def test_a_trading_day_needs_one_request(self) -> None:
        patcher, client = _patched_tool_client({"2026-10-09": FRIDAY_ROWS})
        try:
            with patch("src.tools.market.get_settings"):
                result = await get_market_snapshot(today=date(2026, 10, 9))
        finally:
            patcher.stop()

        assert result == {"data": FRIDAY_ROWS}
        assert client.get_sector_performance.call_count == 1

    @pytest.mark.asyncio
    async def test_rows_from_other_sessions_are_not_that_days_snapshot(self) -> None:
        # Seen live 2026-10-12 (before Monday's data existed): FMP answered a date
        # it had no snapshot for with 11 stale rows from 10-07, 10-08 and 10-09.
        stale = [
            {"date": "2026-10-08", "sector": "Technology", "averageChange": -0.5},
            {"date": "2026-10-09", "sector": "Utilities", "averageChange": 2.8},
        ]
        patcher, client = _patched_tool_client({"2026-10-12": stale, "2026-10-09": FRIDAY_ROWS})
        try:
            with patch("src.tools.market.get_settings"):
                result = await get_market_snapshot(today=date(2026, 10, 12))
        finally:
            patcher.stop()

        assert result == {"data": FRIDAY_ROWS}
        assert client.get_sector_performance.call_count == 4

    @pytest.mark.asyncio
    async def test_no_data_within_the_lookback_raises(self) -> None:
        patcher, client = _patched_tool_client({})
        try:
            with (
                patch("src.tools.market.get_settings"),
                pytest.raises(LookupError, match=f"{SNAPSHOT_LOOKBACK_DAYS} days"),
            ):
                await get_market_snapshot(today=date(2026, 10, 11))
        finally:
            patcher.stop()

        assert client.get_sector_performance.call_count == SNAPSHOT_LOOKBACK_DAYS
