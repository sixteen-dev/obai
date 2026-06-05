"""Response utilities for crypto-server."""

from __future__ import annotations

import re
from typing import Any

import httpx

HTTP_ERROR_MESSAGES: dict[int, str] = {
    400: "Bad request - check the input parameters",
    404: "Coinbase resource not found",
    429: "Coinbase rate limit exceeded - retry later",
    500: "Coinbase server error - retry later",
    502: "Coinbase temporarily unavailable",
    503: "Coinbase service unavailable",
    504: "Coinbase gateway timeout",
}


def format_api_error(error: Exception, provider: str = "Coinbase") -> dict[str, Any]:
    """Format an API error into a user-facing response."""
    error_type = type(error).__name__
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return {
            "isError": True,
            "error": HTTP_ERROR_MESSAGES.get(status_code, f"{provider} request failed"),
            "error_type": error_type,
            "status_code": status_code,
        }
    if isinstance(error, httpx.ConnectError):
        return {
            "isError": True,
            "error": f"Unable to connect to {provider}",
            "error_type": error_type,
        }
    if isinstance(error, httpx.TimeoutException):
        return {
            "isError": True,
            "error": f"{provider} request timed out",
            "error_type": error_type,
        }
    raw_message = str(error)
    sanitized = re.sub(r"https?://[^\s'\"]+", "[URL REDACTED]", raw_message)
    return {"isError": True, "error": sanitized, "error_type": error_type}
