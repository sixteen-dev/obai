"""Response utilities for MCP tools.

The corpus server has no external API dependencies — errors here are
domain/data errors (missing entry, malformed query) not HTTP errors.
"""

import json
from typing import Any

# MCP best practice: limit responses to ~40,000 characters
MAX_RESPONSE_CHARS = 40000


def truncate_response(data: Any, max_chars: int = MAX_RESPONSE_CHARS) -> Any:
    """Serialize a response and truncate if it exceeds max_chars.

    The truncation policy is conservative: if the serialized JSON exceeds the
    cap, we mark the response with a `_truncated: true` flag and shrink any
    long string fields (currently just `body`/`one_line`/`definition`) until
    the payload fits. Used by `get_corpus_entry` for entries whose body section
    is unusually long.
    """
    serialized = json.dumps(data, default=str)
    if len(serialized) <= max_chars:
        return data

    if not isinstance(data, dict):
        return data  # caller handles non-dict cases

    truncatable_fields = ("body", "body_thesis", "body_signal_intuition", "body_notes")
    headroom = max_chars - 200  # margin for the truncation marker

    for field in truncatable_fields:
        value = data.get(field)
        if not isinstance(value, str):
            continue
        # binary-search-ish: halve the value until total fits
        while len(json.dumps(data, default=str)) > headroom and len(value) > 200:
            value = value[: max(200, len(value) // 2)] + "\n... [truncated]"
            data[field] = value

    data["_truncated"] = True
    return data


def domain_error(message: str, **fields: Any) -> dict[str, Any]:
    """Build a uniform error response for corpus-side failures."""
    payload: dict[str, Any] = {"error": message}
    payload.update(fields)
    return payload
