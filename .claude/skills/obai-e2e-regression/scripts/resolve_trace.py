"""Opik trace lookup helper.

Given a unique marker that the test runner appended to its submitted query, find the
matching trace ID in the local Opik server. Used by run_one.py.

Stdlib only — no project dependencies required.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any


class TraceLookupError(RuntimeError):
    """The trace search could not produce a trustworthy result."""


class AmbiguousTraceError(TraceLookupError):
    """More than one trace contains a supposedly unique marker."""


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


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_at_or_after(value: str, lower_bound: str) -> bool:
    parsed_value = _parse_timestamp(value)
    parsed_bound = _parse_timestamp(lower_bound)
    return bool(parsed_value and parsed_bound and parsed_value >= parsed_bound)


def _trace_page_url(
    *,
    base_url: str,
    project: str,
    page: int,
    page_size: int,
    from_time: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "project_name": project,
            "page": page,
            "size": page_size,
            "from_time": from_time,
            "sorting": json.dumps([{"field": "start_time", "direction": "DESC"}]),
        }
    )
    return f"{base_url}/api/v1/private/traces?{query}"


def _fetch_trace_pages(
    *,
    base_url: str,
    project: str,
    page_size: int,
    from_time: str,
    max_pages: int,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = _fetch(
            _trace_page_url(
                base_url=base_url,
                project=project,
                page=page,
                page_size=page_size,
                from_time=from_time,
            )
        )
        content = payload.get("content") or []
        if not isinstance(content, list):
            raise TraceLookupError("Opik trace page has non-list content")
        traces.extend(trace for trace in content if isinstance(trace, dict))

        total = payload.get("total")
        if isinstance(total, int) and page * page_size >= total:
            return traces
        if len(content) < page_size:
            return traces

    raise TraceLookupError(
        f"Opik trace lookup exceeded {max_pages} pages; refusing an incomplete match"
    )


def find_trace_by_marker(
    marker: str,
    t0_iso: str,
    *,
    base_url: str = "http://localhost:5173",
    project: str = "obai-eval",
    page_size: int = 50,
    retries: int = 5,
    backoff_s: float = 1.0,
    max_pages: int = 100,
    expected_workflow: str = "OBaI Central Hub",
) -> tuple[str | None, int]:
    """Return one exact hub-marker match, rejecting ambiguity or partial searches."""

    last_transport_error: Exception | None = None
    successful_search = False
    for attempt in range(1, retries + 1):
        try:
            traces = _fetch_trace_pages(
                base_url=base_url,
                project=project,
                page_size=page_size,
                from_time=t0_iso,
                max_pages=max_pages,
            )
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_transport_error = exc
            if attempt < retries:
                time.sleep(backoff_s * attempt)
            continue

        successful_search = True

        matches: dict[str, dict[str, Any]] = {}
        for trace in traces:
            if trace.get("name") != expected_workflow:
                continue
            start_time = trace.get("start_time", "")
            if not _is_at_or_after(start_time, t0_iso):
                continue
            text = _extract_text(trace.get("input"))
            if marker in text:
                trace_id = trace.get("id")
                if isinstance(trace_id, str) and trace_id:
                    matches[trace_id] = trace

        if len(matches) == 1:
            return next(iter(matches)), attempt
        if len(matches) > 1:
            ids = ", ".join(sorted(matches))
            raise AmbiguousTraceError(f"Marker {marker!r} matched multiple traces: {ids}")

        if attempt < retries:
            time.sleep(backoff_s * attempt)

    if not successful_search and last_transport_error is not None:
        raise TraceLookupError(
            f"Opik trace lookup failed on every attempt: {last_transport_error}"
        ) from last_transport_error
    return None, retries
