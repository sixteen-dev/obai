"""Tests for market movers tool with index-scoped movers."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.clients.index_cache import clear_cache
from src.tools.movers import get_movers


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Clear index cache before each test."""
    clear_cache()


def _make_quote(symbol: str, change_pct: float, volume: int = 1000) -> dict[str, Any]:
    """Build a minimal quote dict for testing."""
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc",
        "price": 100.0,
        "change": change_pct,
        "changesPercentage": change_pct,
        "volume": volume,
    }


class TestGetMovers:
    """Tests for get_movers tool function."""

    @pytest.mark.asyncio
    async def test_exchange_wide_movers_without_index(
        self, sample_movers_response: list[dict[str, Any]]
    ) -> None:
        """Without index param, returns exchange-wide movers from FMP."""
        mock_client = AsyncMock()
        mock_client.get_stock_movers = AsyncMock(return_value=sample_movers_response)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers")

        assert result["type"] == "gainers"
        assert "index" not in result
        mock_client.get_stock_movers.assert_called_once_with("gainers")
        mock_client.batch_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_movers_returns_sorted_gainers(self) -> None:
        """Index movers batch-quotes constituents and sorts by change %."""
        constituents = [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "GOOG"}]
        quotes = [
            _make_quote("MSFT", 5.0),
            _make_quote("AAPL", 2.0),
            _make_quote("GOOG", 8.0),
        ]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="sp500", limit=3)

        assert result["index"] == "sp500"
        assert result["constituents_count"] == 3
        symbols = [m["symbol"] for m in result["data"]]
        assert symbols == ["GOOG", "MSFT", "AAPL"]

    @pytest.mark.asyncio
    async def test_index_movers_sorts_losers_ascending(self) -> None:
        """Losers are sorted by change % ascending (most negative first)."""
        constituents = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
        quotes = [
            _make_quote("A", -2.0),
            _make_quote("B", -10.0),
            _make_quote("C", -5.0),
        ]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("losers", index="dowjones", limit=3)

        symbols = [m["symbol"] for m in result["data"]]
        assert symbols == ["B", "C", "A"]

    @pytest.mark.asyncio
    async def test_index_movers_sorts_actives_by_volume(self) -> None:
        """Actives are sorted by volume descending."""
        constituents = [{"symbol": "A"}, {"symbol": "B"}]
        quotes = [
            _make_quote("A", 1.0, volume=500),
            _make_quote("B", 1.0, volume=9000),
        ]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("actives", index="nasdaq", limit=2)

        symbols = [m["symbol"] for m in result["data"]]
        assert symbols == ["B", "A"]

    @pytest.mark.asyncio
    async def test_limit_caps_results(self) -> None:
        """Limit parameter caps the number of returned movers."""
        constituents = [{"symbol": s} for s in ["A", "B", "C", "D", "E"]]
        quotes = [_make_quote(s, float(i)) for i, s in enumerate("ABCDE")]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="sp500", limit=2)

        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_uses_cache_on_second_call(self) -> None:
        """Second call reuses cached constituent list."""
        constituents = [{"symbol": "AAPL"}]
        quotes = [_make_quote("AAPL", 1.0)]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await get_movers("gainers", index="sp500")
            await get_movers("losers", index="sp500")

        # Constituents fetched once, cached on second call
        assert mock_client.get_index_constituents.call_count == 1
        # Batch quotes called both times (real-time data, no caching)
        assert mock_client.batch_quote.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_constituents_returns_empty(self) -> None:
        """Empty constituent list returns empty data."""
        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=[])

        with patch("src.tools.movers.get_settings"), patch(
            "src.tools.movers.FMPClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="dowjones")

        assert result["data"] == []
        assert result["constituents_count"] == 0
        mock_client.batch_quote.assert_not_called()
