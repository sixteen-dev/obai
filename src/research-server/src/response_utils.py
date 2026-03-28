"""Response formatting and truncation utilities."""

from __future__ import annotations

import json
from typing import Any

from .config import get_settings

# Keys that may contain result arrays needing truncation
_LIST_KEYS = ("results", "competitors", "comparisons")


def truncate_response(data: dict[str, Any]) -> dict[str, Any]:
    """Truncate response to fit within MCP response size limits.

    Trims any list-valued keys (results, competitors, comparisons) from
    the tail until the serialized payload fits under max_response_chars.

    Args:
        data: Response dict to potentially truncate.

    Returns:
        Truncated response dict.

    """
    settings = get_settings()
    if len(json.dumps(data, default=str)) <= settings.max_response_chars:
        return data

    # Iteratively trim the longest list until we fit
    trimmed = False
    while len(json.dumps(data, default=str)) > settings.max_response_chars:
        longest_key = _find_longest_list(data)
        if longest_key is None:
            break
        lst = data[longest_key]
        if len(lst) <= 1:
            break
        lst.pop()
        trimmed = True

    if trimmed:
        data["truncated"] = True

    return data


def _find_longest_list(data: dict[str, Any]) -> str | None:
    """Find the key with the longest list value among trimmable keys."""
    best_key: str | None = None
    best_len = 0
    for key in _LIST_KEYS:
        val = data.get(key)
        if isinstance(val, list) and len(val) > best_len:
            best_key = key
            best_len = len(val)
    return best_key


def format_api_error(error: Exception, service: str) -> dict[str, Any]:
    """Format an API error for MCP tool output.

    Args:
        error: The exception that occurred.
        service: Name of the service that failed.

    Returns:
        Error dict with isError flag.

    """
    return {
        "isError": True,
        "error": f"{service} API error: {type(error).__name__}",
        "message": str(error),
    }
