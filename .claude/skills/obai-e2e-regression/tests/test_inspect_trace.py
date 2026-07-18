from __future__ import annotations

import urllib.error
from urllib.parse import parse_qs, urlparse

import inspect_trace
import pytest


def test_fetch_redacts_query_credentials_from_url_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_url = "http://opik/api?api_key=trace-secret&safe=yes"
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError(f"upstream echoed {secret_url}")
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        inspect_trace.fetch(secret_url)

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "trace-secret" not in stderr
    assert "safe=yes" in stderr


def test_render_rejects_userinfo_without_leaking_credentials() -> None:
    with pytest.raises(ValueError, match="must not contain userinfo") as exc_info:
        inspect_trace.render_trace(
            "trace", "project", "http://trace-user:trace-pass@opik", raw=True
        )

    assert "trace-user" not in str(exc_info.value)
    assert "trace-pass" not in str(exc_info.value)


def test_fetch_spans_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_pages: list[int] = []

    def fake_fetch(url: str) -> dict:
        page = int(parse_qs(urlparse(url).query)["page"][0])
        requested_pages.append(page)
        return {
            "page": page,
            "size": 1,
            "total": 2,
            "content": [{"id": f"span-{page}"}],
        }

    monkeypatch.setattr(inspect_trace, "fetch", fake_fetch)
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)
    spans = inspect_trace.fetch_all_spans(
        trace_id="trace",
        project="project",
        base_url="http://opik",
        page_size=1,
    )

    assert [span["id"] for span in spans] == ["span-1", "span-2"]
    assert requested_pages == [1, 2, 1, 2, 1, 2]


def test_fetch_spans_retries_eventually_consistent_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_fetch(_url: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"page": 1, "size": 200, "total": 2, "content": []}
        return {
            "page": 1,
            "size": 200,
            "total": 2,
            "content": [{"id": "span-1"}, {"id": "span-2"}],
        }

    monkeypatch.setattr(inspect_trace, "fetch", fake_fetch)
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    spans = inspect_trace.fetch_all_spans(
        trace_id="trace",
        project="project",
        base_url="http://opik",
        expected_count=2,
        consistency_retries=4,
    )

    assert [span["id"] for span in spans] == ["span-1", "span-2"]
    assert calls == 4


def test_fetch_spans_does_not_accept_two_stale_zero_count_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {"page": 1, "size": 200, "total": 0, "content": []},
            {"page": 1, "size": 200, "total": 0, "content": []},
            {
                "page": 1,
                "size": 200,
                "total": 1,
                "content": [{"id": "late-span"}],
            },
            {
                "page": 1,
                "size": 200,
                "total": 1,
                "content": [{"id": "late-span"}],
            },
            {
                "page": 1,
                "size": 200,
                "total": 1,
                "content": [{"id": "late-span"}],
            },
        ]
    )
    monkeypatch.setattr(inspect_trace, "fetch", lambda _url: next(responses))
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    spans = inspect_trace.fetch_all_spans(
        trace_id="trace",
        project="project",
        base_url="http://opik",
        expected_count=0,
        consistency_retries=4,
    )

    assert [span["id"] for span in spans] == ["late-span"]


def test_fetch_spans_never_accepts_stably_empty_financial_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        inspect_trace,
        "fetch",
        lambda _url: {"page": 1, "size": 200, "total": 0, "content": []},
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="expected at least 1"):
        inspect_trace.fetch_all_spans(
            trace_id="trace",
            project="project",
            base_url="http://opik",
            expected_count=0,
            consistency_retries=4,
        )


def test_fetch_spans_waits_for_stable_positive_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {"total": 1, "content": [{"id": "early", "output": None}]},
            {
                "total": 2,
                "content": [
                    {"id": "early", "output": {"ok": True}},
                    {"id": "late", "error_info": {"message": "late error"}},
                ],
            },
            {
                "total": 2,
                "content": [
                    {"id": "early", "output": {"ok": True}},
                    {"id": "late", "error_info": {"message": "late error"}},
                ],
            },
            {
                "total": 2,
                "content": [
                    {"id": "early", "output": {"ok": True}},
                    {"id": "late", "error_info": {"message": "late error"}},
                ],
            },
        ]
    )
    monkeypatch.setattr(inspect_trace, "fetch", lambda _url: next(responses))
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    spans = inspect_trace.fetch_all_spans(
        trace_id="trace",
        project="project",
        base_url="http://opik",
        expected_count=1,
        consistency_retries=4,
    )

    assert [span["id"] for span in spans] == ["early", "late"]
    assert spans[1]["error_info"]["message"] == "late error"


def test_fetch_spans_rejects_duplicate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        inspect_trace,
        "fetch",
        lambda _url: {
            "total": 2,
            "content": [{"id": "duplicate"}, {"id": "duplicate"}],
        },
    )

    with pytest.raises(RuntimeError, match="duplicate span ids"):
        inspect_trace.fetch_all_spans(
            trace_id="trace",
            project="project",
            base_url="http://opik",
            expected_count=2,
        )


def test_render_includes_generic_specialist_evidence_and_full_final_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    final = "F" * 2500
    trace = {
        "id": "trace",
        "name": "hub",
        "start_time": "2026-07-15T12:00:00Z",
        "duration": 1000,
        "span_count": 1,
        "input": {"input": "question"},
        "output": {"output": final},
    }
    spans = [
        {
            "id": "span-1",
            "parent_span_id": "parent",
            "name": "fundamental_analysis",
            "start_time": "2026-07-15T12:00:01Z",
            "end_time": "2026-07-15T12:00:02Z",
            "duration": 1000,
            "input": {"symbols": ["AAPL"]},
            "output": {"pe": 30.1},
            "error_info": {"message": "provider degraded"},
        },
        {
            "id": "span-2",
            "parent_span_id": "parent",
            "name": "screener_lookup",
            "start_time": "2026-07-15T12:00:02Z",
            "end_time": "2026-07-15T12:00:03Z",
            "duration": 1000,
            "input": {"filters": {"sector": "technology"}},
            "output": {"symbols": ["AAPL"]},
        },
    ]
    monkeypatch.setattr(inspect_trace, "fetch", lambda _url: trace)
    monkeypatch.setattr(inspect_trace, "fetch_all_spans", lambda **_kwargs: spans)

    inspect_trace.render_trace("trace", "project", "http://opik", raw=False)
    output = capsys.readouterr().out

    assert "FUNDAMENTAL_ANALYSIS" in output
    assert '"symbols": [' in output
    assert '"AAPL"' in output
    assert '"pe": 30.1' in output
    assert "provider degraded" in output
    assert "SCREENER_LOOKUP" in output
    assert final in output


def test_render_tolerates_scalar_payloads_and_malformed_duration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = {
        "id": "trace",
        "name": "hub",
        "start_time": "2026-07-15T12:00:00Z",
        "duration": "not-a-number",
        "span_count": 0,
        "input": "plain question",
        "output": "plain answer",
    }
    monkeypatch.setattr(inspect_trace, "fetch", lambda _url: trace)
    monkeypatch.setattr(inspect_trace, "fetch_all_spans", lambda **_kwargs: [])

    inspect_trace.render_trace("trace", "project", "http://opik", raw=False)
    output = capsys.readouterr().out

    assert "Duration: unknown" in output
    assert "plain question" in output
    assert "plain answer" in output
