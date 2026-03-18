"""Response utilities for MCP tools."""

import json
import re
from typing import Any, cast

import httpx

# MCP best practice: limit responses to ~25,000 characters
MAX_RESPONSE_CHARS = 25000

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


def truncate_response(data: dict[str, Any], max_chars: int = MAX_RESPONSE_CHARS) -> dict[str, Any]:
    """Truncate response data if it exceeds character limit.

    Args:
        data: Response dictionary to potentially truncate
        max_chars: Maximum character limit (default: 25,000)

    Returns:
        Original data if under limit, or truncated data with metadata
    """
    # Serialize to JSON to measure size
    json_str = json.dumps(data, default=str)

    # If under limit, return as-is
    if len(json_str) <= max_chars:
        return data

    # Response is too large - need to truncate the JSON string
    truncated_json = json_str[:max_chars] + "..."

    try:
        # Try to parse the truncated JSON (might be invalid)
        return cast(dict[str, Any], json.loads(truncated_json))
    except json.JSONDecodeError:
        # If truncated JSON is invalid, return metadata only
        return {
            "_truncated": True,
            "_original_size_chars": len(json_str),
            "_error": "Response too large to serialize",
            "_truncation_message": (
                f"Response of {len(json_str)} characters exceeds limit of {max_chars}. "
                "Use pagination parameters (limit/offset) to retrieve data in smaller chunks."
            ),
        }
