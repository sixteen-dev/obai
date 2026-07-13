"""Tests for index constituent cache."""

from typing import Any
from unittest.mock import AsyncMock

from src.clients.index_cache import clear_cache, get_cached_symbols, store_symbols
from src.tools.movers import _resolve_index_symbols


class TestIndexCache:
    """Tests for in-memory index constituent cache."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_cache()

    def test_cache_miss_returns_none(self) -> None:
        """Empty cache returns None."""
        assert get_cached_symbols("sp500") is None

    def test_store_and_retrieve(self) -> None:
        """Stored symbols are retrievable."""
        constituents: list[dict[str, Any]] = [
            {"symbol": "AAPL", "name": "Apple"},
            {"symbol": "MSFT", "name": "Microsoft"},
        ]
        stored = store_symbols("sp500", constituents)
        assert stored == {"AAPL", "MSFT"}

        cached = get_cached_symbols("sp500")
        assert cached == {"AAPL", "MSFT"}

    def test_skips_entries_without_symbol(self) -> None:
        """Entries missing 'symbol' key are skipped."""
        constituents: list[dict[str, Any]] = [
            {"symbol": "AAPL", "name": "Apple"},
            {"name": "No Symbol Corp"},
        ]
        stored = store_symbols("nasdaq100", constituents)
        assert stored == {"AAPL"}

    def test_separate_indexes_cached_independently(self) -> None:
        """Each index has its own cache entry."""
        store_symbols("sp500", [{"symbol": "AAPL"}])
        store_symbols("nasdaq100", [{"symbol": "GOOG"}])

        assert get_cached_symbols("sp500") == {"AAPL"}
        assert get_cached_symbols("nasdaq100") == {"GOOG"}

    def test_clear_cache_removes_all(self) -> None:
        """clear_cache empties all entries."""
        store_symbols("sp500", [{"symbol": "AAPL"}])
        store_symbols("nasdaq100", [{"symbol": "GOOG"}])
        clear_cache()

        assert get_cached_symbols("sp500") is None
        assert get_cached_symbols("nasdaq100") is None

    async def test_empty_constituents_not_cached(self) -> None:
        """A transient empty constituents response is not cached; next call retries."""
        real = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        client = AsyncMock()
        client.get_index_constituents = AsyncMock(side_effect=[[], real])

        first = await _resolve_index_symbols(client, "sp500")
        assert first == set()

        # Empty response must not poison the cache for 24h.
        assert get_cached_symbols("sp500") is None

        second = await _resolve_index_symbols(client, "sp500")
        assert second == {"AAPL", "MSFT"}
        assert client.get_index_constituents.call_count == 2
