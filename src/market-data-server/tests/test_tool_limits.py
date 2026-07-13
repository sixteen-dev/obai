"""Tests for per-tool limit clamping on candles and technical indicators.

These caps prevent single-call responses from exceeding
response_utils.MAX_RESPONSE_CHARS. When clamped, the pagination block
exposes both the caller's request and the effective limit so the agent
can paginate.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.candles import MAX_LIMIT as CANDLE_MAX
from src.tools.candles import get_candles
from src.tools.technical import MAX_LIMIT as INDICATOR_MAX
from src.tools.technical import get_technical_indicators


def _candle_rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1_000_000 + i,
        }
        for i in range(n)
    ]


def _rsi_rows(n: int) -> list[dict[str, Any]]:
    return [{"date": f"2026-01-{(i % 28) + 1:02d}", "rsi": 50.0 + (i % 10)} for i in range(n)]


class TestCandlesLimit:
    """Candle tool clamps `limit` to MAX_LIMIT and exposes requested_limit."""

    @pytest.mark.asyncio
    async def test_limit_above_max_is_clamped(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_historical_daily = AsyncMock(
            return_value={"historical": _candle_rows(CANDLE_MAX * 2)}
        )
        with (
            patch("src.tools.candles.get_settings"),
            patch("src.tools.candles.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_candles("AAPL", "daily", limit=CANDLE_MAX * 2)

        pagination = result["pagination"]
        assert pagination["limit"] == CANDLE_MAX
        assert pagination["requested_limit"] == CANDLE_MAX * 2
        assert pagination["returned"] == CANDLE_MAX
        assert pagination["has_more"] is True
        assert pagination["next_offset"] == CANDLE_MAX

    @pytest.mark.asyncio
    async def test_limit_below_max_is_passthrough(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_historical_daily = AsyncMock(return_value={"historical": _candle_rows(50)})
        with (
            patch("src.tools.candles.get_settings"),
            patch("src.tools.candles.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_candles("AAPL", "daily", limit=30)

        pagination = result["pagination"]
        assert pagination["limit"] == 30
        assert pagination["requested_limit"] == 30
        assert pagination["returned"] == 30

    @pytest.mark.asyncio
    async def test_candles_default_limit_is_30(self) -> None:
        """Default limit is 30 and cap is 130 — in behavior and in the docstring."""
        assert CANDLE_MAX == 130
        doc = get_candles.__doc__ or ""
        assert "default: 100" not in doc
        assert "default: 30" in doc
        assert "130" in doc

        mock_client = AsyncMock()
        mock_client.get_historical_daily = AsyncMock(return_value={"historical": _candle_rows(200)})
        with (
            patch("src.tools.candles.get_settings"),
            patch("src.tools.candles.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_candles("AAPL", "daily")  # no limit -> default

        pagination = result["pagination"]
        assert pagination["requested_limit"] == 30
        assert pagination["limit"] == 30
        assert pagination["returned"] == 30


class TestIndicatorLimit:
    """Indicator tool clamps `limit` to MAX_LIMIT and exposes requested_limit."""

    @pytest.mark.asyncio
    async def test_limit_above_max_is_clamped(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_technical_indicators = AsyncMock(return_value=_rsi_rows(INDICATOR_MAX * 2))
        with (
            patch("src.tools.technical.get_settings"),
            patch("src.tools.technical.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_technical_indicators(
                "AAPL", "RSI", period=14, limit=INDICATOR_MAX * 2
            )

        pagination = result["pagination"]
        assert pagination["limit"] == INDICATOR_MAX
        assert pagination["requested_limit"] == INDICATOR_MAX * 2
        assert pagination["returned"] == INDICATOR_MAX
        assert pagination["has_more"] is True
        assert pagination["next_offset"] == INDICATOR_MAX

    @pytest.mark.asyncio
    async def test_limit_below_max_is_passthrough(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_technical_indicators = AsyncMock(return_value=_rsi_rows(40))
        with (
            patch("src.tools.technical.get_settings"),
            patch("src.tools.technical.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_technical_indicators("AAPL", "RSI", period=14, limit=20)

        pagination = result["pagination"]
        assert pagination["limit"] == 20
        assert pagination["requested_limit"] == 20
        assert pagination["returned"] == 20
