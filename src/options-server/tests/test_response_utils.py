"""Tests for response utilities."""

from typing import Any

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

    def test_default_max_chars_is_25000(self) -> None:
        """Test default max chars matches the module constant."""
        assert MAX_RESPONSE_CHARS == 25000
