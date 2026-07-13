"""Tests for market movers tool with index-scoped movers."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.clients.fmp_client import FMPClient
from src.clients.index_cache import clear_cache
from src.config import Settings
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("actives", index="nasdaq100", limit=2)

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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
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

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="dowjones")

        assert result["data"] == []
        assert result["constituents_count"] == 0
        mock_client.batch_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_nasdaq_index_labeled_nasdaq100(self, mock_settings: Settings) -> None:
        """The Nasdaq index key is 'nasdaq100' (the Nasdaq-100 index, not the exchange).

        Guards accuracy.md §23: 'nasdaq-constituent' returns the Nasdaq-100 (~100
        names), so the key must be labeled nasdaq100 and echoed as such; the bare
        'nasdaq' key must be rejected, not silently aliased to the 100-name index.
        """
        constituents = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        quotes = [_make_quote("AAPL", 2.0), _make_quote("MSFT", 5.0)]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="nasdaq100", limit=2)

        # Accepted key/label and echoed index are both 'nasdaq100'.
        assert result["index"] == "nasdaq100"
        mock_client.get_index_constituents.assert_awaited_once_with("nasdaq100")

        # 'nasdaq100' maps to FMP's nasdaq-constituent endpoint (the Nasdaq-100).
        async with FMPClient(mock_settings) as client:
            with patch.object(client, "_get", new=AsyncMock(return_value=[])) as mock_get:
                await client.get_index_constituents("nasdaq100")
            mock_get.assert_awaited_once_with("nasdaq-constituent")

            # The old bare 'nasdaq' key is rejected, not aliased to the Nasdaq-100.
            with pytest.raises(ValueError, match="nasdaq"):
                await client.get_index_constituents("nasdaq")

    @pytest.mark.asyncio
    async def test_index_movers_null_change_no_crash(self) -> None:
        """A null changesPercentage (common pre-market) must not crash the sort.

        Guards accuracy.md §21 bug (1): q.get('changesPercentage') returns None
        and list.sort comparing None to a float raises TypeError, so the tool
        returns no mover data. The sort must be null-safe.
        """
        constituents = [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "GOOG"}]
        quotes = [
            _make_quote("AAPL", 2.0),
            {**_make_quote("MSFT", 0.0), "changesPercentage": None},
            _make_quote("GOOG", 8.0),
        ]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(return_value=quotes)

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="sp500", limit=3)

        # Normal result dict (not an error envelope) and no exception raised.
        assert result["type"] == "gainers"
        assert result["index"] == "sp500"
        # Non-null gainers are ranked; the null-change quote is unrankable and
        # sorts out of the leaderboard rather than crashing the comparison.
        symbols = [m["symbol"] for m in result["data"]]
        assert symbols == ["GOOG", "AAPL"]
        assert "MSFT" not in symbols

    @pytest.mark.asyncio
    async def test_index_movers_reports_partial_coverage(self) -> None:
        """A failed batch chunk must be disclosed, not silently absorbed.

        Guards accuracy.md §21 bug (2): failed_chunks was incremented but never
        read, so a partial constituent universe was presented as the complete
        leaderboard. The response must carry a coverage signal and a warning.
        """
        constituents = [{"symbol": s} for s in ["A", "B", "C", "D"]]
        first_chunk = [_make_quote("A", 2.0), _make_quote("B", 5.0)]

        mock_client = AsyncMock()
        mock_client.get_index_constituents = AsyncMock(return_value=constituents)
        mock_client.batch_quote = AsyncMock(
            side_effect=[first_chunk, RuntimeError("chunk 2 failed")]
        )

        with (
            patch("src.tools.movers.get_settings"),
            patch("src.tools.movers.FMPClient") as mock_cls,
            patch("src.tools.movers._BATCH_SIZE", 2),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_movers("gainers", index="sp500", limit=10)

        # Coverage is surfaced explicitly: 2 of 4 constituents priced, 1 chunk failed.
        assert result["constituents_count"] == 4
        assert result["quotes_received"] == 2
        assert result["failed_chunks"] == 1
        assert result["coverage_pct"] == 50.0
        warnings = result["warnings"]
        assert isinstance(warnings, list) and warnings
        assert any("coverage" in w.lower() for w in warnings)
