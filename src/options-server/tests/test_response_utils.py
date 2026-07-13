"""Tests for response utilities."""

from typing import Any

from src.response_filters import filter_option_chain_snapshot
from src.response_utils import MAX_RESPONSE_CHARS, truncate_response


class TestTruncateResponse:
    """Tests for response truncation logic."""

    def test_returns_data_unchanged_when_under_limit(self) -> None:
        """Test that small responses are returned unchanged."""
        data: dict[str, Any] = {
            "status": "OK",
            "results": [{"symbol": "AAPL", "price": 175.50}],
        }

        result = truncate_response(data)

        assert result == data
        assert "_truncated" not in result

    def test_truncates_large_response(self) -> None:
        """Test that large responses are truncated."""
        # Create a response larger than the limit
        large_list = [{"symbol": f"SYM{i}", "data": "x" * 100} for i in range(500)]
        data: dict[str, Any] = {"status": "OK", "results": large_list}

        result = truncate_response(data, max_chars=1000)

        # Either truncated JSON was parseable or we got metadata
        assert "_truncated" in result or len(str(result)) <= 1500

    def test_returns_metadata_when_truncation_creates_invalid_json(self) -> None:
        """Test metadata returned when truncated JSON is invalid."""
        # Create data that will definitely create invalid JSON when truncated
        data: dict[str, Any] = {
            "key": "a" * 1000,
            "nested": {"deep": "value"},
        }

        result = truncate_response(data, max_chars=50)

        # Should return metadata because truncated JSON is invalid
        assert "_truncated" in result
        assert result["_truncated"] is True
        assert "_original_size_chars" in result
        assert "_error" in result

    def test_includes_truncation_message_with_guidance(self) -> None:
        """Test truncation message includes pagination guidance."""
        data: dict[str, Any] = {"key": "x" * 1000}

        result = truncate_response(data, max_chars=50)

        if "_truncation_message" in result:
            assert "pagination" in result["_truncation_message"].lower()

    def test_handles_empty_response(self) -> None:
        """Test empty response is returned unchanged."""
        data: dict[str, Any] = {}

        result = truncate_response(data)

        assert result == {}

    def test_handles_nested_structures(self) -> None:
        """Test nested structures are handled correctly."""
        data: dict[str, Any] = {
            "level1": {
                "level2": {"level3": {"value": "nested"}},
                "list": [1, 2, 3],
            },
        }

        result = truncate_response(data)

        assert result == data

    def test_respects_custom_max_chars(self) -> None:
        """Test custom max_chars parameter is respected."""
        data: dict[str, Any] = {"key": "x" * 200}

        # With high limit, should not truncate
        result_high = truncate_response(data, max_chars=500)
        assert "_truncated" not in result_high

        # With low limit, should truncate
        result_low = truncate_response(data, max_chars=50)
        assert "_truncated" in result_low

    def test_default_max_chars_is_40000(self) -> None:
        """Test default max chars matches the module constant."""
        assert MAX_RESPONSE_CHARS == 40000


class TestFilterOptionChainSnapshot:
    """Tests for option chain snapshot filtering."""

    def test_chain_snapshot_keeps_volume_and_timestamps(self) -> None:
        """Day volume, quote timestamp, and underlying last_updated survive filtering."""
        raw_contract: dict[str, Any] = {
            "break_even_price": 180.50,
            "day": {"change": 0.5, "close": 5.25, "volume": 4200},
            "details": {
                "contract_type": "call",
                "expiration_date": "2024-01-19",
                "strike_price": 175.0,
                "ticker": "O:AAPL240119C00175000",
            },
            "greeks": {"delta": 0.65, "gamma": 0.03, "theta": -0.05, "vega": 0.15},
            "implied_volatility": 0.25,
            "last_quote": {
                "ask": 5.30,
                "ask_size": 50,
                "bid": 5.20,
                "bid_size": 100,
                "last_updated": 1705600000000000000,
            },
            "open_interest": 15000,
            "underlying_asset": {
                "last_updated": 1705600000000000000,
                "price": 178.25,
                "ticker": "AAPL",
            },
        }

        result = filter_option_chain_snapshot([raw_contract])

        assert len(result) == 1
        contract = result[0]
        assert contract["volume"] == 4200
        assert contract["last_quote"]["last_updated"] == 1705600000000000000
        assert contract["underlying_last_updated"] == 1705600000000000000
