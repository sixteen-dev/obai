from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import run_one
import yaml


def _write_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    path.write_text(yaml.safe_dump({"test_cases": cases}, sort_keys=False))


def _write_execution_binding(
    *,
    cases_path: Path,
    run_dir: Path,
    case_id: str,
    run_id: str = "obai-e2e-test-run",
    attempt_nonce: str = "a" * 64,
) -> tuple[Path, str, str]:
    """Create the exact run_suite artifacts required by the paid helper."""
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = run_dir / "cases.snapshot.yaml"
    snapshot_path.write_bytes(cases_path.read_bytes())
    snapshot_sha256 = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    raw = yaml.safe_load(snapshot_path.read_text())
    next(entry for entry in raw["test_cases"] if entry["id"] == case_id)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "execute",
        "suite_fingerprint": snapshot_sha256,
        "cases_snapshot_path": str(snapshot_path.resolve()),
        "cases_snapshot_sha256": snapshot_sha256,
        "planned_count": len(raw["test_cases"]),
        "cases": [
            {
                "id": entry["id"],
                "fingerprint": run_one.case_contract_fingerprint(entry),
                "snapshot": entry,
            }
            for entry in raw["test_cases"]
        ],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    attempts_dir = run_dir / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    for entry in raw["test_cases"]:
        entry_id = str(entry["id"])
        entry_nonce = (
            attempt_nonce
            if entry_id == case_id
            else hashlib.sha256(f"test-attempt:{entry_id}".encode()).hexdigest()
        )
        entry_fingerprint = run_one.case_contract_fingerprint(entry)
        attempt = {
            "schema_version": 2,
            "run_id": run_id,
            "case_id": entry_id,
            "case_fingerprint": entry_fingerprint,
            "attempt_nonce": entry_nonce,
            "manifest_sha256": manifest_sha256,
            "cases_snapshot_sha256": snapshot_sha256,
        }
        attempt_path = attempts_dir / f"{entry_id}.json"
        attempt_path.write_text(json.dumps(attempt, sort_keys=True))
        attempt_sha256 = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        binding = {
            "schema_version": 1,
            "run_id": run_id,
            "case_id": entry_id,
            "case_fingerprint": entry_fingerprint,
            "attempt_nonce": entry_nonce,
            "attempt_marker_sha256": attempt_sha256,
            "manifest_sha256": manifest_sha256,
            "cases_snapshot_sha256": snapshot_sha256,
        }
        packet_path = run_dir / f"{entry_id}.json"
        if packet_path.exists():
            packet = json.loads(packet_path.read_text())
            packet.update(
                {
                    "execution_binding": binding,
                    "run_id": run_id,
                    "attempt_nonce": entry_nonce,
                    "manifest_sha256": manifest_sha256,
                    "cases_snapshot_sha256": snapshot_sha256,
                    "attempt_marker_sha256": attempt_sha256,
                }
            )
            packet_path.write_text(json.dumps(packet))
    return snapshot_path, snapshot_sha256, manifest_sha256


def _cli_result(
    response: str = "answer", *, exit_code: int = 0, session_id: str = "session"
) -> dict[str, Any]:
    return {
        "started_at": "2026-07-15T12:00:00Z",
        "latency_ms": 10,
        "exit_code": exit_code,
        "timed_out": False,
        "stdout_json": {"response": response, "session_id": session_id},
        "stdout_raw": json.dumps({"response": response, "session_id": session_id}),
        "stderr": "",
    }


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cases_path: Path,
    run_dir: Path,
    case_id: str = "T1",
    project: str = "obai-eval",
    calendar_anchor: str | None = None,
    run_id: str = "obai-e2e-test-run",
    attempt_nonce: str = "a" * 64,
    prepare_binding: bool = True,
) -> int:
    if prepare_binding:
        cases_path, cases_sha256, manifest_sha256 = _write_execution_binding(
            cases_path=cases_path,
            run_dir=run_dir,
            case_id=case_id,
            run_id=run_id,
            attempt_nonce=attempt_nonce,
        )
    else:
        cases_sha256 = hashlib.sha256(cases_path.read_bytes()).hexdigest()
        manifest_sha256 = "0" * 64
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    argv = [
        "run_one.py",
        "--id",
        case_id,
        "--run-dir",
        str(run_dir),
        "--cases",
        str(cases_path),
        "--cases-sha256",
        cases_sha256,
        "--run-id",
        run_id,
        "--attempt-nonce",
        attempt_nonce,
        "--manifest-sha256",
        manifest_sha256,
        "--opik-project",
        project,
    ]
    if calendar_anchor is not None:
        argv.extend(["--calendar-anchor", calendar_anchor])
    monkeypatch.setattr(
        sys,
        "argv",
        argv,
    )
    return run_one.main()


def test_main_refuses_unbound_direct_invocation_before_paid_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = run_dir / "cases.snapshot.yaml"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    snapshot.write_bytes(cases.read_bytes())
    calls = 0

    def forbidden_cli(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("paid CLI must not execute")

    monkeypatch.setattr(run_one, "run_cli", forbidden_cli)

    with pytest.raises(SystemExit, match="execute manifest"):
        _run_main(
            monkeypatch,
            cases_path=snapshot,
            run_dir=run_dir,
            prepare_binding=False,
        )

    assert calls == 0


def test_main_refuses_attempt_nonce_mismatch_before_paid_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    snapshot, snapshot_sha, manifest_sha = _write_execution_binding(
        cases_path=cases,
        run_dir=run_dir,
        case_id="T1",
        attempt_nonce="b" * 64,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paid CLI must not execute")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_one.py",
            "--id",
            "T1",
            "--run-dir",
            str(run_dir),
            "--cases",
            str(snapshot),
            "--cases-sha256",
            snapshot_sha,
            "--run-id",
            "obai-e2e-test-run",
            "--attempt-nonce",
            "c" * 64,
            "--manifest-sha256",
            manifest_sha,
        ],
    )

    with pytest.raises(SystemExit, match="attempt marker binding mismatch"):
        run_one.main()


def test_main_rejects_snapshot_outside_run_directory_before_paid_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "outside.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    _write_execution_binding(cases_path=cases, run_dir=run_dir, case_id="T1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paid CLI must not execute")
        ),
    )
    manifest_sha = hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_one.py",
            "--id",
            "T1",
            "--run-dir",
            str(run_dir),
            "--cases",
            str(cases),
            "--cases-sha256",
            hashlib.sha256(cases.read_bytes()).hexdigest(),
            "--run-id",
            "obai-e2e-test-run",
            "--attempt-nonce",
            "a" * 64,
            "--manifest-sha256",
            manifest_sha,
        ],
    )

    with pytest.raises(SystemExit, match="inside the run directory"):
        run_one.main()


def test_main_rechecks_runtime_fingerprint_immediately_before_paid_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    fingerprints = iter(["before", "after"])
    monkeypatch.setattr(run_one, "input_fingerprint", lambda **_kwargs: next(fingerprints))
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paid CLI must not execute")
        ),
    )

    with pytest.raises(SystemExit, match="runtime, credential, or model configuration changed"):
        _run_main(monkeypatch, cases_path=cases, run_dir=run_dir)


def test_consumed_attempt_nonce_blocks_duplicate_call_after_runner_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    calls = 0

    def crashing_cli(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated crash after model call started")

    monkeypatch.setattr(run_one, "run_cli", crashing_cli)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _run_main(monkeypatch, cases_path=cases, run_dir=run_dir)

    with pytest.raises(SystemExit, match="attempt nonce was already consumed"):
        _run_main(monkeypatch, cases_path=cases, run_dir=run_dir)

    assert calls == 1


def test_reusable_packet_must_match_exact_execution_binding() -> None:
    case = {"id": "T1"}
    binding = {
        "schema_version": 1,
        "run_id": "run-a",
        "case_id": "T1",
        "case_fingerprint": "case-fingerprint",
        "attempt_nonce": "a" * 64,
        "attempt_marker_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "cases_snapshot_sha256": "3" * 64,
    }
    packet = {
        "input_fingerprint": "same",
        "harness_status": "completed",
        "execution_binding": binding,
    }

    assert run_one.cache_is_reusable(
        case,
        packet,
        "same",
        execution_binding=binding,
    )
    assert not run_one.cache_is_reusable(
        case,
        packet,
        "same",
        execution_binding={**binding, "attempt_nonce": "b" * 64},
    )


def test_opik_defaults_match_obai_environment_and_strip_sdk_api_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://sdk-fallback:5173/api/")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "legacy-project")
    monkeypatch.delenv("OPIK_URL", raising=False)
    monkeypatch.delenv("OPIK_OBAI_PROJECT_NAME", raising=False)

    assert run_one.configured_opik_url() == "http://sdk-fallback:5173"
    assert run_one.configured_opik_project() == "legacy-project"

    monkeypatch.setenv("OPIK_URL", "http://obai-setting:5173/")
    monkeypatch.setenv("OPIK_OBAI_PROJECT_NAME", "obai-project")

    assert run_one.configured_opik_url() == "http://obai-setting:5173"
    assert run_one.configured_opik_project() == "obai-project"


def test_runtime_tree_hash_includes_server_configs_and_ignores_caches(tmp_path: Path) -> None:
    server = tmp_path / "src" / "specialist-server"
    server.mkdir(parents=True)
    config = server / "pyproject.toml"
    config.write_text("version = '1'\n")
    cache = server / ".mypy_cache" / "state.json"
    cache.parent.mkdir()
    cache.write_text('{"noise": 1}')

    first = run_one._hash_tree([tmp_path / "src"], root=tmp_path)
    config.write_text("version = '2'\n")
    second = run_one._hash_tree([tmp_path / "src"], root=tmp_path)
    cache.write_text('{"noise": 2}')
    third = run_one._hash_tree([tmp_path / "src"], root=tmp_path)

    assert first != second
    assert second == third


def test_main_submits_and_resolves_the_same_unique_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "Analyze AAPL"}])
    observed: dict[str, str] = {}

    def fake_run_cli(query: str, session: str, **_kwargs: Any) -> dict[str, Any]:
        observed["submitted"] = query
        return _cli_result(session_id=session)

    def fake_find(marker: str, **_kwargs: Any) -> tuple[str, int]:
        observed["looked_up"] = marker
        return "trace-1", 1

    monkeypatch.setattr(run_one, "run_cli", fake_run_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", fake_find)
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "trace evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace-1"}, "spans": []},
    )

    assert _run_main(monkeypatch, cases_path=cases, run_dir=run_dir) == 0
    packet = json.loads((run_dir / "T1.json").read_text())

    assert packet["marker"] in observed["submitted"]
    assert observed["looked_up"] == packet["marker"]
    assert packet["marked_query"] == observed["submitted"]
    assert packet["query"] == "Analyze AAPL"


def test_relative_query_materializes_calendar_once_and_preserves_live_now() -> None:
    case = {
        "date_policy": "relative",
        "timezone": "America/New_York",
    }
    context = run_one.materialize_calendar_context(
        case,
        now=datetime(2026, 7, 16, 3, 30, tzinfo=UTC),
    )
    submitted = run_one.materialize_query("Use today's data through tomorrow.", context)

    assert context == {
        "timezone": "America/New_York",
        "today": "2026-07-15",
        "tomorrow": "2026-07-16",
        "current_year": 2026,
    }
    assert "today=2026-07-15" in submitted
    assert "tomorrow=2026-07-16" in submitted
    assert "`Now` and `latest` still require fresh data" in submitted


def test_relative_query_uses_suite_timezone_fallback(tmp_path: Path) -> None:
    """A lint-valid suite timezone is executable without per-case duplication."""
    cases = tmp_path / "cases.snapshot.yaml"
    cases.write_text(
        "suite:\n"
        "  timezone: America/New_York\n"
        "test_cases:\n"
        "  - id: T1\n"
        "    query: Use today's close.\n"
        "    date_policy: relative\n"
    )
    case = run_one.load_case(cases, "T1")

    context = run_one.materialize_calendar_context(
        case,
        now=datetime(2026, 7, 16, 3, 30, tzinfo=UTC),
        default_timezone=run_one.load_suite_timezone(cases),
    )

    assert context == {
        "timezone": "America/New_York",
        "today": "2026-07-15",
        "tomorrow": "2026-07-16",
        "current_year": 2026,
    }


def test_relative_child_inherits_parent_calendar_context() -> None:
    context = {
        "timezone": "America/New_York",
        "today": "2026-07-15",
        "tomorrow": "2026-07-16",
        "current_year": 2026,
    }

    inherited = run_one.materialize_calendar_context(
        {"date_policy": "live", "timezone": "America/New_York"},
        {"calendar_context": context},
        now=datetime(2026, 7, 17, 4, 0, tzinfo=UTC),
    )

    assert inherited == context


def test_root_main_uses_explicit_suite_calendar_anchor_in_packet_and_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(
        cases,
        [
            {
                "id": "T1",
                "query": "Use today's close through tomorrow.",
                "date_policy": "relative",
                "timezone": "America/New_York",
            }
        ],
    )
    observed: dict[str, str] = {}

    def fake_run_cli(query: str, session: str, **_kwargs: Any) -> dict[str, Any]:
        observed["query"] = query
        return _cli_result(session_id=session)

    monkeypatch.setattr(run_one, "run_cli", fake_run_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace-1", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace-1"}, "spans": []},
    )

    assert (
        _run_main(
            monkeypatch,
            cases_path=cases,
            run_dir=run_dir,
            calendar_anchor="2026-07-16T03:30:00Z",
        )
        == 0
    )
    packet = json.loads((run_dir / "T1.json").read_text())
    assert packet["calendar_context"]["today"] == "2026-07-15"
    assert packet["calendar_context"]["tomorrow"] == "2026-07-16"
    assert packet["submitted_query"] in observed["query"]
    assert "today=2026-07-15" in packet["submitted_query"]


def test_existing_packet_fails_closed_when_case_query_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    calls: list[str] = []

    def fake_run_cli(query: str, session: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(query)
        return _cli_result(session_id=session)

    monkeypatch.setattr(run_one, "run_cli", fake_run_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace-1", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace-1"}, "spans": []},
    )

    _write_cases(cases, [{"id": "T1", "query": "old query"}])
    assert _run_main(monkeypatch, cases_path=cases, run_dir=run_dir) == 0
    _write_cases(cases, [{"id": "T1", "query": "new query"}])
    with pytest.raises(SystemExit, match="refusing a duplicate model call"):
        _run_main(monkeypatch, cases_path=cases, run_dir=run_dir)

    assert len(calls) == 1
    assert "old query" in calls[0]


def test_existing_packet_fails_closed_when_runner_config_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "query"}])
    calls = 0

    def fake_run_cli(_query: str, session: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _cli_result(session_id=session)

    monkeypatch.setattr(run_one, "run_cli", fake_run_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace-1", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace-1"}, "spans": []},
    )

    assert _run_main(monkeypatch, cases_path=cases, run_dir=run_dir, project="project-a") == 0
    with pytest.raises(SystemExit, match="refusing a duplicate model call"):
        _run_main(monkeypatch, cases_path=cases, run_dir=run_dir, project="project-b")

    assert calls == 1


@pytest.mark.parametrize(
    "parent_packet",
    [
        None,
        {
            "session_id": "parent-session",
            "cli": {"exit_code": 1, "timed_out": False, "stdout_json": None},
            "trace": {"id": None},
        },
    ],
)
def test_chain_dependency_missing_or_failed_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parent_packet: dict[str, Any] | None,
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_cases(
        cases,
        [
            {"id": "PARENT", "query": "first turn"},
            {"id": "CHILD", "query": "follow up", "chain_from": "PARENT"},
        ],
    )
    if parent_packet is not None:
        (run_dir / "PARENT.json").write_text(json.dumps(parent_packet))

    def forbidden_cli(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("child query must not execute")

    monkeypatch.setattr(run_one, "run_cli", forbidden_cli)

    assert (
        _run_main(
            monkeypatch,
            cases_path=cases,
            run_dir=run_dir,
            case_id="CHILD",
        )
        == 2
    )
    packet = json.loads((run_dir / "CHILD.json").read_text())
    assert packet["harness_status"] == "dependency_failed"
    assert packet["chain_from"] == "PARENT"


def test_successful_chain_reuses_the_verified_parent_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    parent_case = {"id": "PARENT", "query": "first turn"}
    _write_cases(
        cases,
        [parent_case, {"id": "CHILD", "query": "follow up", "chain_from": "PARENT"}],
    )
    monkeypatch.setattr(
        run_one,
        "input_fingerprint",
        lambda *, case, **_kwargs: f"fingerprint-{case['id']}",
    )
    parent = {
        "id": "PARENT",
        "input_fingerprint": "fingerprint-PARENT",
        "harness_status": "completed",
        "harness_exit_code": 0,
        "session_id": "verified-parent-session",
        "cli": {
            "exit_code": 0,
            "timed_out": False,
            "stdout_json": {
                "response": "parent answer",
                "session_id": "verified-parent-session",
            },
        },
        "trace": {"id": "parent-trace"},
    }
    (run_dir / "PARENT.json").write_text(json.dumps(parent))
    observed_session: list[str] = []

    def fake_run_cli(_query: str, session: str, **_kwargs: Any) -> dict[str, Any]:
        observed_session.append(session)
        return _cli_result(session_id=session)

    monkeypatch.setattr(run_one, "run_cli", fake_run_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("child-trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "child-trace"}, "spans": []},
    )

    assert (
        _run_main(
            monkeypatch,
            cases_path=cases,
            run_dir=run_dir,
            case_id="CHILD",
        )
        == 0
    )
    assert observed_session == ["verified-parent-session"]


def test_checkpoint_rejects_wrong_identity_stale_verdict_and_failed_async() -> None:
    base = {
        "id": "PARENT",
        "input_fingerprint": "current",
        "harness_status": "completed",
        "session_id": "session",
        "cli": {
            "exit_code": 0,
            "timed_out": False,
            "stdout_json": {"response": "answer", "session_id": "session"},
        },
        "trace": {"id": "trace"},
    }

    assert "parent id" in str(
        run_one._checkpoint_failure(
            {**base, "id": "OTHER"},
            expected_case_id="PARENT",
            expected_input_fingerprint="current",
        )
    )
    assert "fail_product" in str(run_one._checkpoint_failure({**base, "verdict": "fail_product"}))
    assert "async job status" in str(
        run_one._checkpoint_failure({**base, "followup": {"status": "failed"}})
    )
    missing_session_evidence = {
        **base,
        "cli": {
            **base["cli"],
            "stdout_json": {"response": "answer"},
        },
    }
    assert "missing session_id" in str(run_one._checkpoint_failure(missing_session_evidence))


def test_live_checkpoint_respects_case_freshness_ttl() -> None:
    case = {"date_policy": "live", "max_age_seconds": 300}
    packet = {
        "input_fingerprint": "same",
        "harness_status": "completed",
        "ended_at": "2026-07-15T12:00:00Z",
    }

    assert run_one.cache_is_reusable(
        case,
        packet,
        "same",
        now=run_one.datetime.fromisoformat("2026-07-15T12:04:59+00:00"),
    )
    assert not run_one.cache_is_reusable(
        case,
        packet,
        "same",
        now=run_one.datetime.fromisoformat("2026-07-15T12:05:01+00:00"),
    )


def test_ambiguous_trace_is_an_infrastructure_failure_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(cases, [{"id": "T1", "query": "query"}])
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda _query, session, **_k: _cli_result(session_id=session),
    )
    monkeypatch.setattr(
        run_one,
        "find_trace_by_marker",
        lambda **_k: (_ for _ in ()).throw(
            run_one.TraceLookupError("marker matched trace-a and trace-b")
        ),
    )

    assert _run_main(monkeypatch, cases_path=cases, run_dir=run_dir) == 2
    packet = json.loads((run_dir / "T1.json").read_text())
    assert packet["harness_status"] == "trace_lookup_failed"
    assert "trace-a and trace-b" in packet["trace"]["lookup_error"]


def test_run_cli_preserves_raw_stdout_even_when_json_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = '{"response":"full final response"}\n'
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=raw, stderr=""),
    )

    cli = run_one.run_cli("query", "session")

    assert cli["stdout_raw"] == raw


def test_run_cli_forces_inline_llm_scoring_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One canonical case cannot trigger an unbudgeted completeness judge."""
    captured_env: dict[str, str] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess([], 0, stdout='{"response":"ok"}', stderr="")

    monkeypatch.setenv("ENABLE_INLINE_SCORING", "true")
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://legacy:5173/api")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "legacy-project")
    monkeypatch.delenv("OPIK_URL", raising=False)
    monkeypatch.delenv("OPIK_OBAI_PROJECT_NAME", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_one.run_cli("query", "session")

    assert captured_env["ENABLE_INLINE_SCORING"] == "false"
    assert captured_env["OPIK_URL"] == "http://legacy:5173"
    assert captured_env["OPIK_URL_OVERRIDE"] == "http://legacy:5173/api"
    assert captured_env["OPIK_OBAI_PROJECT_NAME"] == "legacy-project"
    binding = run_one.runtime_environment_binding()["public"]
    assert binding["ENABLE_INLINE_SCORING"] == "false"
    assert binding["OPIK_URL"] == "http://legacy:5173"


def test_run_cli_redacts_subprocess_stderr_before_packet_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_stderr = (
        "failed https://trace-user:trace-pass@opik/path?api_key=query-secret "
        "OPENAI_API_KEY=sk-subprocess"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr=raw_stderr,
        ),
    )

    cli = run_one.run_cli("query", "session")

    assert "trace-user" not in cli["stderr"]
    assert "trace-pass" not in cli["stderr"]
    assert "query-secret" not in cli["stderr"]
    assert "sk-subprocess" not in cli["stderr"]


def test_write_packet_fsyncs_file_and_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_fsync = run_one.os.fsync
    fsynced_modes: list[int] = []

    def recording_fsync(fd: int) -> None:
        fsynced_modes.append(run_one.os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(run_one.os, "fsync", recording_fsync)

    run_one._write_packet(tmp_path / "packet.json", {"ok": True})

    assert any(run_one.stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(run_one.stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_load_case_rejects_path_traversal_id(tmp_path: Path) -> None:
    cases = tmp_path / "cases.yaml"
    _write_cases(cases, [{"id": "../outside", "query": "query"}])

    with pytest.raises(SystemExit, match="Unsafe case id"):
        run_one.load_case(cases, "../outside")


def test_load_case_verifies_executable_snapshot_hash_before_query(tmp_path: Path) -> None:
    cases = tmp_path / "cases.snapshot.yaml"
    _write_cases(cases, [{"id": "T1", "query": "query"}])

    with pytest.raises(SystemExit, match="snapshot SHA-256 mismatch"):
        run_one.load_case(cases, "T1", expected_sha256="0" * 64)


def test_build_packet_copies_full_final_response() -> None:
    cli = _cli_result("untruncated final answer")
    packet = run_one.build_packet(
        case={"id": "T1", "query": "query"},
        cli=cli,
        marker="marker",
        marked_query="marked",
        session_id="session",
        trace_id="trace",
        lookup_attempts=1,
        curated="evidence",
        followup=None,
        input_fingerprint="fingerprint",
        raw_trace_evidence={"trace": {"id": "trace"}, "spans": []},
        execution_binding={
            "schema_version": 1,
            "run_id": "run",
            "case_id": "T1",
            "case_fingerprint": "case-fingerprint",
            "attempt_nonce": "a" * 64,
            "attempt_marker_sha256": "1" * 64,
            "manifest_sha256": "2" * 64,
            "cases_snapshot_sha256": "3" * 64,
        },
    )

    assert packet["final_response"] == "untruncated final answer"
    assert packet["input_fingerprint"] == "fingerprint"
    assert packet["run_id"] == "run"
    assert packet["attempt_nonce"] == "a" * 64
    assert packet["manifest_sha256"] == "2" * 64


def test_build_packet_rejects_terminal_cli_session_mismatch() -> None:
    packet = run_one.build_packet(
        case={"id": "CHILD", "query": "follow up", "chain_from": "PARENT"},
        cli=_cli_result("answer", session_id="wrong-session"),
        marker="marker",
        marked_query="marked",
        session_id="expected-parent-session",
        trace_id="trace",
        lookup_attempts=1,
        curated="evidence",
        followup=None,
        input_fingerprint="fingerprint",
        raw_trace_evidence={"trace": {"id": "trace"}, "spans": []},
    )

    assert packet["harness_status"] == "session_mismatch"
    assert packet["harness_exit_code"] == 2
    assert "wrong-session" in packet["session_validation_error"]


def test_expected_hub_rejection_exit_one_is_product_evidence() -> None:
    cli = _cli_result("", exit_code=1)
    cli["stdout_json"] = {
        "response": "",
        "session_id": "session",
        "guardrail_rejected": True,
        "error": {
            "type": "guardrail_rejection",
            "message": "Query not related to financial topics.",
        },
    }
    packet = run_one.build_packet(
        case={
            "id": "GUARD",
            "query": "What's the weather?",
            "expected_outcome": "hub_reject",
            "expect_rejection": True,
        },
        cli=cli,
        marker="marker",
        marked_query="marked",
        session_id="session",
        trace_id="trace",
        lookup_attempts=1,
        curated="guardrail evidence",
        followup=None,
        input_fingerprint="fingerprint",
        raw_trace_evidence={"trace": {"id": "trace"}, "spans": []},
    )

    assert packet["harness_status"] == "completed"
    assert packet["harness_exit_code"] == 0


def test_fetch_raw_trace_evidence_is_structured_and_uses_selected_opik_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inspect_script = tmp_path / "inspect_trace.py"
    inspect_script.write_text("# placeholder")
    observed: list[str] = []

    def fake_subprocess(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.extend(argv)
        payload = {"trace": {"id": "trace-id"}, "spans": [{"name": "options_analysis"}]}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess)
    evidence = run_one.fetch_raw_trace_evidence(
        "trace-id",
        inspect_script,
        base_url="http://custom-opik",
        project="custom-project",
    )

    assert evidence["spans"][0]["name"] == "options_analysis"
    assert "--raw" in observed
    assert observed[-4:] == ["--url", "http://custom-opik", "--project", "custom-project"]


def test_fetch_raw_trace_evidence_rejects_wrong_trace_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inspect_script = tmp_path / "inspect_trace.py"
    inspect_script.write_text("# placeholder")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"trace": {"id": "wrong"}, "spans": []}),
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="different or missing trace id"):
        run_one.fetch_raw_trace_evidence(
            "expected",
            inspect_script,
            base_url="http://opik",
            project="project",
        )


def test_async_followup_polls_until_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            _cli_result("Status: running\nJob ID: bt_1234\nPoll again."),
            _cli_result("Status: completed\nJob ID: bt_1234\nVerdict: reject"),
        ]
    )
    monkeypatch.setattr(run_one, "run_cli", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda _trace_id, _inspect_script, **_k: {
            "trace": {"id": "trace"},
            "spans": [],
        },
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monotonic_values = iter([0.0, 0.0, 1.0, 1.0, 2.0, 2.0])
    monkeypatch.setattr(run_one.time, "monotonic", lambda: next(monotonic_values, 2.0))

    followup = run_one.run_async_followup(
        job_id="bt_1234",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
    )

    assert followup["status"] == "completed"
    assert followup["timed_out"] is False
    assert len(followup["polls"]) == 2
    assert all(poll["marker"] in poll["marked_query"] for poll in followup["polls"])


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("job_id: crypto_123\nEstimated Time: 5 seconds", "crypto_123"),
        (
            "Job ID: 550e8400-e29b-41d4-a716-446655440000\nEstimated Time: 5 seconds",
            "550e8400-e29b-41d4-a716-446655440000",
        ),
    ],
)
def test_async_job_parser_accepts_realistic_full_ids(response: str, expected: str) -> None:
    assert run_one.extract_async_job(response) == (expected, 5)


def test_async_job_parser_rejects_ambiguous_ids() -> None:
    response = "job_id: crypto_123; retry job id: crypto_456"
    assert run_one.extract_async_job(response) == (None, 0)


def test_async_job_parser_accepts_markdown_label_form() -> None:
    # The product emits a markdown label with the value on the next line and no
    # colon; it must still be recognized as the job id.
    response = "Status\nWalk-forward job is running.\n\nJob ID  \nbt_3c133f5d\n"
    assert run_one.extract_async_job_ids(response) == ["bt_3c133f5d"]


def test_async_job_parser_rejects_label_without_value() -> None:
    # A bare label followed by prose (no colon, no next-line value) is not an id.
    assert run_one.extract_async_job_ids("Job ID is running; no id assigned yet") == []


def test_async_followup_rejects_response_for_another_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_a, **_k: _cli_result("Status: completed\njob_id: crypto_other"),
    )
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="crypto_expected",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=1,
    )

    assert followup["status"] == "job_id_mismatch"
    assert followup["polls"][0]["job_id_matches"] is False


def test_async_followup_rejects_cli_session_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_a, **_k: _cli_result(
            "Status: completed\njob_id: crypto_expected",
            session_id="another-session",
        ),
    )
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="crypto_expected",
        eta_s=1,
        session_id="expected-session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=1,
    )

    assert followup["status"] == "session_id_mismatch"
    assert followup["timed_out"] is False
    assert followup["poll_limit_reached"] is False
    assert followup["evidence_complete"] is False
    assert followup["polls"][0]["session_id_matches"] is False


def test_async_followup_requires_result_to_echo_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_a, **_k: _cli_result("Status: completed\nFull stored result"),
    )
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="crypto_expected",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=1,
    )

    assert followup["status"] == "job_id_missing"
    assert followup["polls"][0]["job_id_matches"] is False


def test_async_followup_uses_case_typed_poll_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_queries: list[str] = []

    def fake_cli(query: str, *_args: object, **_kwargs: object) -> dict[str, Any]:
        observed_queries.append(query)
        return _cli_result("Status: completed\njob_id: crypto_123\nHit rate: 50%")

    monkeypatch.setattr(run_one, "run_cli", fake_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="crypto_123",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=1,
        poll_prompt="Inspect crypto backtest {job_id}; return hit rate.",
    )

    assert followup["status"] == "completed"
    assert "Inspect crypto backtest crypto_123" in observed_queries[0]
    assert "walk-forward" not in observed_queries[0]


def test_initial_terminal_async_result_avoids_redundant_paid_poll(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    _write_cases(
        cases,
        [
            {
                "id": "T1",
                "query": "run async work",
                "expect_async_job": True,
                "cost": {"max_async_polls": 2},
            }
        ],
    )
    cli_calls = 0

    def fake_cli(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal cli_calls
        cli_calls += 1
        return _cli_result(
            "Status: completed\njob_id: crypto_123\nFull result",
            session_id=str(_args[1]),
        )

    monkeypatch.setattr(run_one, "run_cli", fake_cli)
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )

    assert _run_main(monkeypatch, cases_path=cases, run_dir=run_dir) == 0
    packet = json.loads((run_dir / "T1.json").read_text())
    assert cli_calls == 1
    assert packet["followup"]["initial_terminal"] is True
    assert packet["followup"]["status"] == "completed"


def test_async_followup_stops_at_paid_poll_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_a, **_k: _cli_result("Status: running\nJob ID: bt_1234\nPoll again."),
    )
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(run_one, "fetch_raw_trace_evidence", lambda *_a, **_k: {})
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="bt_1234",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=2,
    )

    assert followup["status"] == "running"
    assert followup["timed_out"] is True
    assert followup["poll_limit_reached"] is True
    assert len(followup["polls"]) == 2


def test_async_followup_revalidates_authorization_before_each_paid_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = 0

    def authorization_check() -> None:
        nonlocal checks
        checks += 1

    monkeypatch.setattr(
        run_one,
        "run_cli",
        lambda *_a, **_k: _cli_result("Status: running\nJob ID: bt_1234\nPoll again."),
    )
    monkeypatch.setattr(run_one, "find_trace_by_marker", lambda **_k: ("trace", 1))
    monkeypatch.setattr(run_one, "fetch_curated_trace", lambda *_a, **_k: "evidence")
    monkeypatch.setattr(
        run_one,
        "fetch_raw_trace_evidence",
        lambda *_a, **_k: {"trace": {"id": "trace"}, "spans": []},
    )
    monkeypatch.setattr(run_one.time, "sleep", lambda _s: None)
    monkeypatch.setattr(run_one.time, "monotonic", lambda: 0.0)

    followup = run_one.run_async_followup(
        job_id="bt_1234",
        eta_s=1,
        session_id="session",
        base_url="http://opik",
        project="project",
        inspect_script=Path("inspect.py"),
        max_polls=2,
        authorization_check=authorization_check,
    )

    assert len(followup["polls"]) == 2
    assert checks == 2


def test_fetch_curated_trace_passes_selected_opik_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inspect_script = tmp_path / "inspect_trace.py"
    inspect_script.write_text("# placeholder")
    observed: list[str] = []

    def fake_subprocess(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess)
    assert (
        run_one.fetch_curated_trace(
            "trace-id",
            inspect_script,
            base_url="http://custom-opik",
            project="custom-project",
        )
        == "ok"
    )

    assert observed[-4:] == ["--url", "http://custom-opik", "--project", "custom-project"]
