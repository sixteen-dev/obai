"""Tests for response filtering logic."""

from typing import Any

from src.response_filters import filter_screen_results, filter_search_results


class TestFilterScreenResults:
    """Tests for stock screening result filtering."""

    def test_keeps_essential_fields(self) -> None:
        """Test that essential fields are kept in screen results."""
        raw_data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "companyName": "Apple Inc.",
                "marketCap": 3000000000000,
                "price": 175.50,
                "beta": 1.2,
                "volume": 50000000,
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "exchangeShortName": "NASDAQ",
                "country": "US",
                # Fields that should be filtered out
                "image": "https://example.com/logo.png",
                "ipoDate": "1980-12-12",
                "defaultImage": False,
                "isEtf": False,
                "isActivelyTrading": True,
                "ceo": "Tim Cook",
                "website": "https://apple.com",
            }
        ]

        result = filter_screen_results(raw_data)

        assert len(result) == 1
        item = result[0]

        # Essential fields should be present
        assert item["symbol"] == "AAPL"
        assert item["companyName"] == "Apple Inc."
        assert item["marketCap"] == 3000000000000
        assert item["price"] == 175.50
        assert item["beta"] == 1.2
        assert item["volume"] == 50000000
        assert item["sector"] == "Technology"
        assert item["industry"] == "Consumer Electronics"
        assert item["exchangeShortName"] == "NASDAQ"
        assert item["country"] == "US"

        # Non-essential fields should be filtered out
        assert "image" not in item
        assert "ipoDate" not in item
        assert "defaultImage" not in item
        assert "ceo" not in item
        assert "website" not in item

    def test_dividend_fields_survive(self) -> None:
        """Dividend amount fields must survive the screen result filter.

        Guards accuracy.md §18: lastDividend/lastAnnualDividend must reach the
        model so it can report/compute real yields instead of fabricating them.
        """
        raw_data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "companyName": "Apple Inc.",
                "price": 175.50,
                "lastDividend": 0.25,
                "lastAnnualDividend": 1.00,
            }
        ]

        result = filter_screen_results(raw_data)

        assert len(result) == 1
        item = result[0]
        assert item["lastDividend"] == 0.25
        assert item["lastAnnualDividend"] == 1.00

    def test_handles_empty_list(self) -> None:
        """Test filtering empty list returns empty list."""
        result = filter_screen_results([])
        assert result == []

    def test_handles_multiple_items(self) -> None:
        """Test filtering multiple items in list."""
        raw_data: list[dict[str, Any]] = [
            {"symbol": "AAPL", "companyName": "Apple Inc.", "ceo": "Tim Cook"},
            {"symbol": "MSFT", "companyName": "Microsoft", "ceo": "Satya Nadella"},
            {"symbol": "GOOGL", "companyName": "Alphabet", "ceo": "Sundar Pichai"},
        ]

        result = filter_screen_results(raw_data)

        assert len(result) == 3
        for item in result:
            assert "symbol" in item
            assert "companyName" in item
            assert "ceo" not in item


class TestFilterSearchResults:
    """Tests for company search result filtering."""

    def test_keeps_essential_search_fields(self) -> None:
        """Test that essential fields are kept in search results."""
        raw_data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NASDAQ Global Select",
                "exchangeShortName": "NASDAQ",
                "currency": "USD",
                # Fields that should be filtered out
                "stockExchange": "NASDAQ Global Select",
                "price": 175.50,
                "changesPercentage": 1.5,
            }
        ]

        result = filter_search_results(raw_data)

        assert len(result) == 1
        item = result[0]

        # Essential fields should be present
        assert item["symbol"] == "AAPL"
        assert item["name"] == "Apple Inc."
        assert item["exchangeShortName"] == "NASDAQ"
        assert item["currency"] == "USD"

        # Non-essential fields should be filtered out
        assert "stockExchange" not in item
        assert "price" not in item
        assert "changesPercentage" not in item

    def test_handles_empty_search_results(self) -> None:
        """Test filtering empty search results returns empty list."""
        result = filter_search_results([])
        assert result == []

    def test_handles_partial_data(self) -> None:
        """Test filtering when some expected fields are missing."""
        raw_data: list[dict[str, Any]] = [
            {
                "symbol": "AAPL",
                # Missing name, exchange, etc.
            }
        ]

        result = filter_search_results(raw_data)

        assert len(result) == 1
        assert result[0] == {"symbol": "AAPL"}
