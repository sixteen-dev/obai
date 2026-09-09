from __future__ import annotations

import email.message
import http.client
import json
import urllib.error
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import inspect_trace
import pytest


class _FakeResponse:
    """Minimal ``urlopen`` stand-in that yields one JSON body."""

    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    """Build an ``HTTPError`` with the given status.

    Args:
        url: URL the synthetic failure is attributed to.
        code: HTTP status code to report.

    Returns:
        An ``HTTPError`` usable as a ``urlopen`` side effect.
    """
    return urllib.error.HTTPError(url, code, f"synthetic {code}", email.message.Message(), None)


def _scripted_urlopen(outcomes: list[object], attempts: list[str]) -> Callable[..., _FakeResponse]:
    """Return a ``urlopen`` stand-in that replays ``outcomes`` in order.

    Args:
        outcomes: One entry per allowed attempt. An exception is raised; any
            other value is JSON-encoded into the response body.
        attempts: Mutable list the stand-in appends each requested URL to.

    Returns:
        A callable that replaces ``urllib.request.urlopen``. It fails loudly
        if the caller attempts more requests than the script allows.
    """

    def _urlopen(url: str, **_kwargs: object) -> _FakeResponse:
        attempts.append(url)
        if len(attempts) > len(outcomes):
            message = f"fetch made {len(attempts)} attempts; script allows {len(outcomes)}"
            raise AssertionError(message)
        outcome = outcomes[len(attempts) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(json.dumps(outcome))

    return _urlopen


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


def test_fetch_retries_transient_server_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([_http_error(url, 500), {"total": 29}], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    assert inspect_trace.fetch(url) == {"total": 29}
    assert len(attempts) == 2


def test_fetch_retries_rate_limit_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([_http_error(url, 429), {"total": 29}], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    assert inspect_trace.fetch(url) == {"total": 29}
    assert len(attempts) == 2


def test_fetch_retries_connection_refused_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen(
            [
                urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
                {"ok": True},
            ],
            attempts,
        ),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    assert inspect_trace.fetch(url) == {"ok": True}
    assert len(attempts) == 2


def test_fetch_exits_after_bounded_attempts_on_persistent_server_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen(
            [_http_error(url, 500)] * inspect_trace.FETCH_MAX_ATTEMPTS,
            attempts,
        ),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", sleeps.append)

    with pytest.raises(SystemExit) as exc_info:
        inspect_trace.fetch(url)

    assert exc_info.value.code == 2
    assert len(attempts) == inspect_trace.FETCH_MAX_ATTEMPTS
    assert sleeps == [0.5, 1.0, 2.0]
    stderr = capsys.readouterr().err
    assert (
        f"{inspect_trace.FETCH_MAX_ATTEMPTS}/{inspect_trace.FETCH_MAX_ATTEMPTS} attempts" in stderr
    )
    assert "500" in stderr


def test_fetch_does_not_retry_permanent_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "http://opik/api/v1/private/traces/missing"
    attempts: list[str] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([_http_error(url, 404)], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", sleeps.append)

    with pytest.raises(SystemExit) as exc_info:
        inspect_trace.fetch(url)

    assert exc_info.value.code == 2
    assert len(attempts) == 1
    assert sleeps == []
    assert f"1/{inspect_trace.FETCH_MAX_ATTEMPTS} attempts" in capsys.readouterr().err


def test_fetch_redacts_query_credentials_when_retries_are_exhausted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_url = "http://opik/api?api_key=trace-secret&safe=yes"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen(
            [urllib.error.URLError(f"upstream echoed {secret_url}")]
            * inspect_trace.FETCH_MAX_ATTEMPTS,
            attempts,
        ),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    with pytest.raises(SystemExit) as exc_info:
        inspect_trace.fetch(secret_url)

    assert exc_info.value.code == 2
    assert len(attempts) == inspect_trace.FETCH_MAX_ATTEMPTS
    stderr = capsys.readouterr().err
    assert "trace-secret" not in stderr
    assert "safe=yes" in stderr


def test_fetch_retries_a_truncated_body_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short read is the same server blip as a 500 and must not kill a paid run."""
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen(
            [http.client.IncompleteRead(b'{"tot', 91), {"total": 29}],
            attempts,
        ),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    assert inspect_trace.fetch(url) == {"total": 29}
    assert len(attempts) == 2


def test_fetch_retries_a_connection_reset_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-response reset is transient even though it arrives as an OSError."""
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([ConnectionResetError("peer reset"), {"total": 1}], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)

    assert inspect_trace.fetch(url) == {"total": 1}
    assert len(attempts) == 2


def test_fetch_stops_retrying_once_the_process_retry_window_is_spent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The retry window keeps a degraded Opik inside run_one's subprocess cap.

    Without it a slow-but-alive server can spend more wall clock retrying than
    the caller allows, and the process is killed before it can say why.
    """
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([_http_error(url, 500)], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(inspect_trace, "_retry_window_spent", lambda: True)

    with pytest.raises(SystemExit) as exc_info:
        inspect_trace.fetch(url)

    assert exc_info.value.code == 2
    assert len(attempts) == 1
    assert "retry window is spent" in capsys.readouterr().err


def test_fetch_records_a_rescued_attempt_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A degrading Opik must leave a trace even when the retry rescues it."""
    url = "http://opik/api/v1/private/spans?trace_id=t"
    attempts: list[str] = []
    monkeypatch.setattr(
        inspect_trace.urllib.request,
        "urlopen",
        _scripted_urlopen([_http_error(url, 503), {"total": 2}], attempts),
    )
    monkeypatch.setattr(inspect_trace.time, "sleep", lambda _seconds: None)
    written: list[str] = []
    monkeypatch.setattr(inspect_trace.sys.stderr, "write", written.append)

    assert inspect_trace.fetch(url) == {"total": 2}
    assert any("retrying" in line for line in written)


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
