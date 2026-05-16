"""Response utilities for MCP tools."""

import json
import re
from typing import Any, cast

import httpx

# MCP best practice: limit responses to ~40,000 characters
MAX_RESPONSE_CHARS = 40000

# Map HTTP status codes to user-friendly messages
HTTP_ERROR_MESSAGES: dict[int, str] = {
    400: "Bad request - check the input parameters",
    401: "Authentication failed - invalid API key",
    402: "API subscription required for this endpoint",
    403: "Access forbidden - check API permissions",
    404: "Resource not found",
    429: "Rate limit exceeded - please try again later",
    500: "API server error - please try again later",
    502: "API temporarily unavailable",
    503: "API service unavailable - please try again later",
}


def format_api_error(error: Exception, api_name: str = "API") -> dict[str, Any]:
    """Format an API error into a user-friendly response.

    The full error details are logged separately. This function returns
    a clean message suitable for displaying to users without sensitive data.

    Args:
        error: The exception that occurred.
        api_name: Name of the API for error messages (e.g., "FMP", "Tavily").

    Returns:
        Error dict with user-friendly message.

    """
    error_type = type(error).__name__

    # Handle HTTP errors with clean messages
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        base_message = HTTP_ERROR_MESSAGES.get(
            status_code,
            f"Request failed with status {status_code}",
        )
        return {
            "isError": True,
            "error": f"{api_name}: {base_message}",
            "error_type": error_type,
            "status_code": status_code,
        }

    # Handle connection errors
    if isinstance(error, httpx.ConnectError):
        return {
            "isError": True,
            "error": f"Unable to connect to {api_name} - check network connection",
            "error_type": error_type,
        }

    # Handle timeout errors
    if isinstance(error, httpx.TimeoutException):
        return {
            "isError": True,
            "error": f"Request to {api_name} timed out - please try again",
            "error_type": error_type,
        }

    # Generic fallback - sanitize the message to remove any URLs/keys
    raw_message = str(error)
    # Remove URLs that might contain API keys
    sanitized = re.sub(r"https?://[^\s'\"]+", "[URL REDACTED]", raw_message)
    return {
        "isError": True,
        "error": sanitized,
        "error_type": error_type,
    }


def _measure(data: dict[str, Any]) -> int:
    return len(json.dumps(data, default=str))


def _longest_list_key(data: dict[str, Any]) -> str | None:
    """Return the top-level key holding the longest list value, or None."""
    best_key: str | None = None
    best_len = 0
    for key, val in data.items():
        if isinstance(val, list) and len(val) > best_len:
            best_key = key
            best_len = len(val)
    return best_key


def truncate_response(data: dict[str, Any], max_chars: int = MAX_RESPONSE_CHARS) -> dict[str, Any]:
    """Shrink response payload to fit within ``max_chars``.

    The previous implementation sliced the serialized JSON string and
    tried to re-parse it, which almost always failed json.loads, so
    callers silently got the metadata-only fallback. This version pops
    tail items off the longest list-typed top-level field until the
    payload fits, then marks the response as truncated.
    """
    size = _measure(data)
    if size <= max_chars:
        return data

    trimmed_items = 0
    while _measure(data) > max_chars:
        key = _longest_list_key(data)
        if key is None or len(data[key]) <= 1:
            break
        data[key].pop()
        trimmed_items += 1

    final_size = _measure(data)
    if final_size <= max_chars:
        data["_truncated"] = True
        data["_truncated_items"] = trimmed_items
        return data

    return cast(
        dict[str, Any],
        {
            "_truncated": True,
            "_original_size_chars": size,
            "_error": "Response too large to serialize",
            "_truncation_message": (
                f"Response of {size} characters exceeds limit of {max_chars}. "
                "Use pagination parameters (limit/offset) to retrieve data in smaller chunks."
            ),
        },
    )
