"""Opik trace lookup helper.

Given a unique marker that the test runner prepended to its query, find the
matching trace ID in the local Opik server. Used by run_one.py.

Stdlib only — no project dependencies required.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _extract_text(blob: Any) -> str:
    if isinstance(blob, str):
        return blob
    if isinstance(blob, list):
        return "\n".join(_extract_text(item) for item in blob)
    if isinstance(blob, dict):
        if "text" in blob and isinstance(blob["text"], str):
            return blob["text"]
        if "content" in blob:
            return _extract_text(blob["content"])
        if "input" in blob:
            return _extract_text(blob["input"])
        if "messages" in blob:
            return _extract_text(blob["messages"])
        return json.dumps(blob)
    return ""


def _fetch(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_trace_by_marker(
    marker: str,
    t0_iso: str,
    *,
    base_url: str = "http://localhost:5173",
    project: str = "obai-eval",
    page_size: int = 50,
    retries: int = 5,
    backoff_s: float = 1.0,
) -> tuple[str | None, int]:
    """Return (trace_id, attempts_used). trace_id is None if not found."""
    query = urllib.parse.urlencode({"project_name": project, "size": page_size})
    url = f"{base_url}/api/v1/private/traces?{query}"

    for attempt in range(1, retries + 1):
        try:
            payload = _fetch(url)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            time.sleep(backoff_s * attempt)
            continue

        for trace in payload.get("content", []):
            start_time = trace.get("start_time", "")
            if start_time and start_time < t0_iso:
                continue
            text = _extract_text(trace.get("input"))
            if marker in text:
                return trace.get("id"), attempt

        time.sleep(backoff_s * attempt)

    return None, retries
