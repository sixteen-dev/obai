"""Shared utilities for OBaI clients (TUI, CLI, Web).

Centralizes tool tracking, argument formatting, and specialist
name mappings used by all client implementations.
"""

from __future__ import annotations

import json
import time

# Specialist tool name -> display name
SPECIALIST_TOOLS: dict[str, str] = {
    "market_data_analysis": "Market Data Agent",
    "fundamentals_analysis": "Fundamentals Agent",
    "events_news_analysis": "Events & News Agent",
    "options_analysis": "Options Agent",
    "screener_lookup": "Screener Agent",
    "portfolio_analysis": "Portfolio Agent",
    "strategy_analysis": "Strategy Agent",
    "research_analysis": "Research Agent",
    "prediction_market_analysis": "Prediction Markets Agent",
    "crypto_analysis": "Crypto Agent",
}


class ToolCallTracker:
    """Track tool call timing and specialist-to-MCP parent relationships.

    Used by both the TUI and Web clients to measure tool execution
    duration and link MCP tool calls to their parent specialist.
    """

    def __init__(self) -> None:
        """Initialize timing and parent tracking state."""
        self._start_times: dict[str, float] = {}
        self._specialist_ids: dict[str, str] = {}  # specialist_name -> call_id
        self._current_specialist_id: str | None = None

    def clear(self) -> None:
        """Clear tracked state for new query."""
        self._start_times.clear()
        self._specialist_ids.clear()
        self._current_specialist_id = None

    def start_specialist(self, call_id: str, specialist_name: str) -> None:
        """Record a specialist tool call starting."""
        self._start_times[call_id] = time.perf_counter()
        self._specialist_ids[specialist_name] = call_id
        self._current_specialist_id = call_id

    def start_mcp(self, call_id: str) -> None:
        """Record an MCP tool call starting."""
        self._start_times[call_id] = time.perf_counter()

    def complete(self, call_id: str) -> int | None:
        """Complete a call and return duration in ms, or None if unknown."""
        start = self._start_times.pop(call_id, None)
        if start is None:
            return None
        return int((time.perf_counter() - start) * 1000)

    def get_specialist_id(self, specialist_name: str) -> str | None:
        """Get the call ID for a specialist by name."""
        return self._specialist_ids.get(specialist_name)

    @property
    def current_specialist_id(self) -> str | None:
        """Get the current specialist call ID for MCP nesting."""
        return self._current_specialist_id


def format_tool_args(raw_args: str, tool_name: str) -> str:
    """Format tool arguments for display.

    For specialist tools, shows the input text (truncated).
    For other tools, shows key=value pairs (up to 3).

    Args:
        raw_args: Raw JSON argument string.
        tool_name: Tool function name.

    Returns:
        Formatted argument string for display.
    """
    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            return str(raw_args)[:60]

        # For specialist tools, show the input query
        if tool_name in SPECIALIST_TOOLS:
            input_text = str(args.get("input", ""))
            if len(input_text) > 50:
                return input_text[:50] + "..."
            return input_text

        # For other tools, show key=value pairs
        pairs = [f"{k}={v}" for k, v in list(args.items())[:3]]
        result = ", ".join(pairs)
        if len(args) > 3:
            result += ", ..."
        return result

    except (json.JSONDecodeError, TypeError):
        return raw_args[:60] if raw_args else ""
