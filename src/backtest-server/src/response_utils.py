"""Response utilities for backtest-server."""

import json
import re
from typing import Any, cast

import httpx

MAX_RESPONSE_CHARS = 40000

HTTP_ERROR_MESSAGES: dict[int, str] = {
    400: "Bad request - check the input parameters",
    401: "Authentication failed - invalid API key",
    402: "FMP API subscription required for this endpoint",
    403: "Access forbidden - check API permissions",
    404: "Resource not found",
    429: "Rate limit exceeded - please try again later",
    500: "FMP API server error - please try again later",
    502: "FMP API temporarily unavailable",
    503: "FMP API service unavailable - please try again later",
}


def format_api_error(error: Exception) -> dict[str, Any]:
    """Format an API error into a user-friendly response."""
    error_type = type(error).__name__

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        user_message = HTTP_ERROR_MESSAGES.get(
            status_code,
            f"API request failed with status {status_code}",
        )
        return {
            "isError": True,
            "error": user_message,
            "error_type": error_type,
            "status_code": status_code,
        }

    if isinstance(error, httpx.ConnectError):
        return {
            "isError": True,
            "error": "Unable to connect to FMP API - check network connection",
            "error_type": error_type,
        }

    if isinstance(error, httpx.TimeoutException):
        return {
            "isError": True,
            "error": "Request timed out - FMP API may be slow, please try again",
            "error_type": error_type,
        }

    # Generic fallback - sanitize message
    raw_message = str(error)
    sanitized = re.sub(r"https?://[^\s'\"]+", "[URL REDACTED]", raw_message)
    return {
        "isError": True,
        "error": sanitized,
        "error_type": error_type,
    }


def truncate_response(
    data: dict[str, Any],
    max_chars: int = MAX_RESPONSE_CHARS,
) -> dict[str, Any]:
    """Truncate response data if it exceeds character limit."""
    json_str = json.dumps(data, default=str)

    if len(json_str) <= max_chars:
        return data

    truncated_json = json_str[:max_chars] + "..."

    try:
        return cast(dict[str, Any], json.loads(truncated_json))
    except json.JSONDecodeError:
        return {
            "_truncated": True,
            "_original_size_chars": len(json_str),
            "_error": "Response too large to serialize",
            "_truncation_message": (
                f"Response of {len(json_str)} chars exceeds limit of {max_chars}. "
                "Request fewer symbols or a shorter date range."
            ),
        }
