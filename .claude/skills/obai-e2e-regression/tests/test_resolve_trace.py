from __future__ import annotations

import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest
import resolve_trace


def _trace(
    trace_id: str,
    marker: str,
    start: str = "2026-07-15T12:00:01Z",
    name: str = "OBaI Central Hub",
) -> dict:
    return {"id": trace_id, "name": name, "start_time": start, "input": {"input": marker}}


def test_lookup_paginates_until_unique_marker_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_pages: list[int] = []

    def fake_fetch(url: str) -> dict:
        page = int(parse_qs(urlparse(url).query)["page"][0])
        requested_pages.append(page)
        if page == 1:
            return {
                "page": 1,
                "size": 1,
                "total": 2,
                "content": [_trace("other", "different marker")],
            }
        return {
            "page": 2,
            "size": 1,
            "total": 2,
            "content": [_trace("wanted", "regress:T1:unique")],
        }

    monkeypatch.setattr(resolve_trace, "_fetch", fake_fetch)
    trace_id, attempts = resolve_trace.find_trace_by_marker(
        "regress:T1:unique",
        "2026-07-15T12:00:00Z",
        page_size=1,
        retries=1,
        backoff_s=0,
    )

    assert trace_id == "wanted"
    assert attempts == 1
    assert requested_pages == [1, 2]


def test_lookup_rejects_ambiguous_marker_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolve_trace,
        "_fetch",
        lambda _url: {
            "page": 1,
            "size": 50,
            "total": 2,
            "content": [
                _trace("trace-a", "regress:T1:duplicate"),
                _trace("trace-b", "regress:T1:duplicate"),
            ],
        },
    )

    with pytest.raises(resolve_trace.AmbiguousTraceError, match="trace-a.*trace-b"):
        resolve_trace.find_trace_by_marker(
            "regress:T1:duplicate",
            "2026-07-15T12:00:00Z",
            retries=1,
            backoff_s=0,
        )


def test_lookup_ignores_specialist_trace_with_same_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "regress:T1:workflow-filter"
    monkeypatch.setattr(
        resolve_trace,
        "_fetch",
        lambda _url: {
            "page": 1,
            "size": 50,
            "total": 2,
            "content": [
                _trace("specialist", marker, name="Technical Analysis"),
                _trace("hub", marker),
            ],
        },
    )

    trace_id, _attempts = resolve_trace.find_trace_by_marker(
        marker,
        "2026-07-15T12:00:00Z",
        retries=1,
        backoff_s=0,
    )

    assert trace_id == "hub"


def test_lookup_sends_server_side_time_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_query: dict[str, list[str]] = {}

    def fake_fetch(url: str) -> dict:
        requested_query.update(parse_qs(urlparse(url).query))
        return {"page": 1, "size": 50, "total": 0, "content": []}

    monkeypatch.setattr(resolve_trace, "_fetch", fake_fetch)
    resolve_trace.find_trace_by_marker(
        "regress:T1:unique",
        "2026-07-15T12:00:00Z",
        retries=1,
        backoff_s=0,
    )

    assert requested_query["from_time"] == ["2026-07-15T12:00:00Z"]
    assert requested_query["page"] == ["1"]


def test_lookup_propagates_transport_outage_instead_of_reporting_missing_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolve_trace,
        "_fetch",
        lambda _url: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )
    monkeypatch.setattr(resolve_trace.time, "sleep", lambda _seconds: None)

    with pytest.raises(resolve_trace.TraceLookupError, match="connection refused"):
        resolve_trace.find_trace_by_marker(
            "regress:T1:outage",
            "2026-07-15T12:00:00Z",
            retries=2,
            backoff_s=0,
        )
