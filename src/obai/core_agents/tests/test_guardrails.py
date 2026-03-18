"""Tests for guardrail helper functions.

Tests _extract_text_from_item and _extract_latest_user_query
which handle session-aware query extraction for the input guardrail.
"""

from __future__ import annotations

from typing import Any

import pytest

from core_agents.guardrails import _extract_latest_user_query, _extract_text_from_item


class TestExtractTextFromItem:
    """Tests for _extract_text_from_item."""

    def test_dict_with_content(self) -> None:
        item = {"role": "user", "content": "What is AAPL?"}
        assert _extract_text_from_item(item) == "What is AAPL?"

    def test_dict_with_text(self) -> None:
        item = {"role": "user", "text": "Analyze Tesla"}
        assert _extract_text_from_item(item) == "Analyze Tesla"

    def test_object_with_content_attr(self) -> None:
        class Msg:
            content = "Hello world"

        assert _extract_text_from_item(Msg()) == "Hello world"

    def test_object_with_text_attr(self) -> None:
        class Msg:
            text = "Hello world"

        assert _extract_text_from_item(Msg()) == "Hello world"

    def test_list_content_parts(self) -> None:
        """Content can be a list of content parts with text attrs."""

        class Part:
            def __init__(self, text: str) -> None:
                self.text = text

        item = {"content": [Part("hello"), Part(" world")]}
        assert _extract_text_from_item(item) == "hello  world"

    def test_list_content_dicts(self) -> None:
        item = {"content": [{"text": "part1"}, {"text": "part2"}]}
        assert _extract_text_from_item(item) == "part1 part2"

    def test_list_content_strings(self) -> None:
        item = {"content": ["hello", "world"]}
        assert _extract_text_from_item(item) == "hello world"

    def test_empty_dict(self) -> None:
        assert _extract_text_from_item({}) == ""

    def test_none_content(self) -> None:
        item = {"content": None}
        assert _extract_text_from_item(item) == ""

    def test_no_text_attrs(self) -> None:
        assert _extract_text_from_item(42) == ""


class TestExtractLatestUserQuery:
    """Tests for _extract_latest_user_query."""

    def test_string_input(self) -> None:
        assert _extract_latest_user_query("What is AAPL?") == "What is AAPL?"

    def test_empty_string(self) -> None:
        assert _extract_latest_user_query("") == ""

    def test_empty_list(self) -> None:
        assert _extract_latest_user_query([]) == ""

    def test_single_user_message(self) -> None:
        items: list[Any] = [{"role": "user", "content": "analyze Tesla"}]
        assert _extract_latest_user_query(items) == "analyze Tesla"

    def test_session_history_includes_context(self) -> None:
        """With session history, should include previous user msg as context."""
        items: list[Any] = [
            {"role": "user", "content": "What is AAPL trading at?"},
            {"role": "assistant", "content": "AAPL is trading at $182.50..."},
            {"role": "user", "content": "What about its P/E ratio?"},
        ]
        result = _extract_latest_user_query(items)
        assert "What about its P/E ratio?" in result
        assert "What is AAPL trading at?" in result
        # Assistant response should NOT be included
        assert "182.50" not in result

    def test_ignores_assistant_messages(self) -> None:
        """Should skip assistant messages when looking for user queries."""
        items: list[Any] = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "long financial response here..."},
        ]
        # Only one user message, no context prefix
        assert _extract_latest_user_query(items) == "first question"

    def test_multiple_turns_uses_last_two_user_msgs(self) -> None:
        """With 3+ user messages, only last 2 are included."""
        items: list[Any] = [
            {"role": "user", "content": "old query 1"},
            {"role": "assistant", "content": "response 1"},
            {"role": "user", "content": "old query 2"},
            {"role": "assistant", "content": "response 2"},
            {"role": "user", "content": "latest query"},
        ]
        result = _extract_latest_user_query(items)
        assert "latest query" in result
        assert "old query 2" in result
        # Oldest user message should NOT be included (only last 2)
        assert "old query 1" not in result

    def test_non_financial_after_financial_history(self) -> None:
        """Non-financial query after financial session includes context."""
        items: list[Any] = [
            {"role": "user", "content": "analyze AAPL fundamentals"},
            {
                "role": "assistant",
                "content": "AAPL trades at $182.50 with P/E of 28.5...",
            },
            {"role": "user", "content": "what's the weather today?"},
        ]
        result = _extract_latest_user_query(items)
        # Current query present, assistant data absent
        assert "what's the weather today?" in result
        assert "182.50" not in result

    def test_followup_pronoun_includes_context(self) -> None:
        """Follow-up with pronoun should include previous query for resolution."""
        items: list[Any] = [
            {"role": "user", "content": "tell me about Tesla"},
            {"role": "assistant", "content": "Tesla is trading at..."},
            {"role": "user", "content": "what's the latest news surrounding it?"},
        ]
        result = _extract_latest_user_query(items)
        assert "what's the latest news surrounding it?" in result
        assert "tell me about Tesla" in result

    def test_fallback_to_last_item_no_role(self) -> None:
        """If no items have a role, fall back to extracting from last item."""
        items: list[Any] = [{"content": "hello"}, {"content": "world"}]
        assert _extract_latest_user_query(items) == "world"

    def test_skips_empty_user_messages(self) -> None:
        """Should skip user messages with empty content."""
        items: list[Any] = [
            {"role": "user", "content": "first query"},
            {"role": "user", "content": ""},
        ]
        assert _extract_latest_user_query(items) == "first query"

    def test_object_items_with_role(self) -> None:
        """Handle object items with role attribute (not just dicts)."""

        class Msg:
            def __init__(self, role: str, content: str) -> None:
                self.role = role
                self.content = content

        items = [
            Msg("user", "first"),
            Msg("assistant", "response"),
            Msg("user", "latest"),
        ]
        result = _extract_latest_user_query(items)
        assert "latest" in result
        assert "first" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
