from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import run_suite
from run_suite import (
    CHAIN_CONTINUATION_VERDICTS,
    EXIT_CONFIGURATION,
    EXIT_INFRASTRUCTURE,
    EXIT_PRODUCT_FAILURE,
    EXIT_SUCCESS,
    ExpensivePlanError,
    ImmutableManifestError,
    PlanError,
    build_manifest,
    choose_cases,
    exit_code_for_summary,
    fingerprint_case,
    observed_model_requests,
    validate_resume_manifest,
    write_immutable_json,
)


def _case(case_id: str, **extra: object) -> dict:
    return {
        "id": case_id,
        "feature": f"feature_{case_id.lower()}",
        "query": f"Query for {case_id}",
        "tier": "core",
        "estimated_api_calls": 3,
        "expected_outcome": "success",
        **extra,
    }


def _test_attempt(case: dict, *, run_id: str, nonce: str = "a" * 64) -> dict:
    return run_suite._attempt_payload(
        case,
        run_id=run_id,
        attempt_nonce=nonce,
        manifest_sha256="0" * 64,
        cases_snapshot_sha256="0" * 64,
    )


@pytest.fixture(autouse=True)
def _stable_test_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep runtime-fingerprint tests independent of developer credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key-not-for-api-use")


def test_default_selection_prefers_core() -> None:
    cases = [_case("C1"), _case("S1", tier="smoke", smoke=True), _case("E1", tier="extended")]

    plan = choose_cases(cases)

    assert [case["id"] for case in plan.cases] == ["C1"]
    assert plan.estimated_api_calls == 3


def test_default_selection_falls_back_to_legacy_smoke() -> None:
    cases = [
        {"id": "S1", "feature": "smoke", "query": "Smoke", "smoke": True},
        {"id": "E1", "feature": "other", "query": "Other"},
    ]

    plan = choose_cases(cases)

    assert [case["id"] for case in plan.cases] == ["S1"]


def test_planner_rejects_unsafe_case_id() -> None:
    with pytest.raises(PlanError, match="unsafe path characters"):
        choose_cases([_case("../outside")])


def test_extended_and_live_require_explicit_cost_authorization() -> None:
    cases = [_case("E1", tier="extended", estimated_api_calls=4)]

    with pytest.raises(ExpensivePlanError):
        choose_cases(cases, tiers={"extended"})

    plan = choose_cases(cases, tiers={"extended"}, allow_expensive=True)
    assert [case["id"] for case in plan.cases] == ["E1"]


def test_expensive_authorization_never_bypasses_api_call_cap() -> None:
    cases = [_case("L1", tier="live", estimated_api_calls=4)]

    with pytest.raises(ExpensivePlanError):
        choose_cases(
            cases,
            tiers={"live"},
            allow_expensive=True,
            max_api_calls=3,
        )


def test_dependency_parent_is_included_and_ordered() -> None:
    cases = [
        _case("P1", tier="smoke"),
        _case("C1", chain_from="P1"),
    ]

    plan = choose_cases(cases)

    assert [case["id"] for case in plan.cases] == ["P1", "C1"]


def test_semantic_review_parent_still_allows_stateful_child_execution() -> None:
    assert "needs_semantic_review" in CHAIN_CONTINUATION_VERDICTS
    assert "fail_product" not in CHAIN_CONTINUATION_VERDICTS
    assert "inconclusive_harness" not in CHAIN_CONTINUATION_VERDICTS


def test_observed_model_requests_counts_unique_llm_spans_across_polls() -> None:
    packet = {
        "trace": {
            "id": "initial",
            "spans": [
                {"id": "llm-1", "type": "llm"},
                {"id": "tool-1", "type": "tool"},
            ],
        },
        "followup": {
            "polls": [
                {
                    "trace": {
                        "id": "poll-1",
                        "spans": [
                            {"id": "llm-2", "type": "llm"},
                            {"id": "tool-2", "type": "tool"},
                        ],
                    }
                }
            ]
        },
    }

    assert observed_model_requests(packet) == 2


def test_observed_model_requests_rejects_zero_llm_trace() -> None:
    assert observed_model_requests({"trace": {"id": "trace", "spans": []}}) is None
    assert (
        observed_model_requests(
            {
                "trace": {
                    "id": "trace",
                    "spans": [{"id": "tool-only", "type": "tool"}],
                }
            }
        )
        is None
    )


def _write_runner_packet(
    command: list[str],
    *,
    case: dict,
    response: str,
    llm_spans: int,
    harness_status: str = "completed",
) -> None:
    case_id = command[command.index("--id") + 1]
    run_dir = Path(command[command.index("--run-dir") + 1])
    attempt_bytes = (run_dir / "attempts" / f"{case_id}.json").read_bytes()
    attempt = json.loads(attempt_bytes)
    execution_binding = run_suite._attempt_execution_binding(
        attempt,
        attempt_bytes=attempt_bytes,
    )
    claims_dir = run_dir / "claims"
    claims_dir.mkdir(exist_ok=True)
    (claims_dir / f"{case_id}.json").write_text(
        json.dumps(execution_binding, sort_keys=True, separators=(",", ":")) + "\n"
    )
    packet = {
        "id": case_id,
        "case_fingerprint": run_suite.case_contract_fingerprint(case),
        "input_fingerprint": f"input-{case_id}",
        "execution_binding": execution_binding,
        "run_id": execution_binding["run_id"],
        "attempt_nonce": execution_binding["attempt_nonce"],
        "manifest_sha256": execution_binding["manifest_sha256"],
        "cases_snapshot_sha256": execution_binding["cases_snapshot_sha256"],
        "attempt_marker_sha256": execution_binding["attempt_marker_sha256"],
        "harness_status": harness_status,
        "harness_exit_code": 0,
        "latency_ms": 10,
        "cli": {
            "exit_code": 0,
            "timed_out": False,
            "stdout_json": {"response": response},
            "stderr": "",
        },
        "trace": {
            "id": f"trace-{case_id}",
            "spans": [
                {"id": f"{case_id}-llm-{index}", "type": "llm", "name": "Response"}
                for index in range(llm_spans)
            ],
        },
        "final_response": response,
    }
    (run_dir / f"{case_id}.json").write_text(json.dumps(packet))


def test_execute_continues_real_chain_through_semantic_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [
        _case("P1", assertions={"manual_assertions": ["review parent"]}),
        _case("C1", chain_from="P1"),
    ]
    plan = choose_cases(cases, max_api_calls=6)
    cases_by_id = {case["id"]: case for case in cases}

    def fake_run(command: list[str], **_kwargs: object) -> object:
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="completed answer",
            llm_spans=2,
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-1",
        cases_path=tmp_path / "cases.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
    )

    assert summary["attempted_count"] == 2
    assert [result["verdict"] for result in summary["results"]] == [
        "needs_semantic_review",
        "pass",
    ]
    assert summary["observed_model_requests"] == 4


def test_execute_passes_one_calendar_anchor_only_to_root_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("P1"), _case("C1", chain_from="P1"), _case("R2")]
    plan = choose_cases(cases, max_api_calls=9)
    cases_by_id = {case["id"]: case for case in cases}
    observed_commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> object:
        observed_commands.append(command)
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="completed answer",
            llm_spans=2,
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-anchor",
        cases_path=tmp_path / "cases.snapshot.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
        calendar_anchor="2026-07-16T03:30:00Z",
    )

    assert summary["attempted_count"] == 3
    by_id = {command[command.index("--id") + 1]: command for command in observed_commands}
    for root_id in ("P1", "R2"):
        command = by_id[root_id]
        assert command[command.index("--calendar-anchor") + 1] == "2026-07-16T03:30:00Z"
    assert "--calendar-anchor" not in by_id["C1"]


def test_execute_stops_before_next_case_when_observed_usage_would_exceed_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("C1"), _case("C2")]
    plan = choose_cases(cases, max_api_calls=6)
    cases_by_id = {case["id"]: case for case in cases}

    def fake_run(command: list[str], **_kwargs: object) -> object:
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="completed answer",
            llm_spans=4,
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-1",
        cases_path=tmp_path / "cases.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
    )

    assert summary["attempted_count"] == 1
    assert summary["observed_model_requests"] == 4
    assert summary["missing_case_ids"] == ["C2"]
    assert "exceeding between-case limit" in summary["abort_reason"]


def _execute_with_harness_statuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    statuses: dict[str, str],
    case_ids: list[str],
) -> dict:
    """Run a plan where named cases come back with a given harness status."""
    cases = [_case(case_id) for case_id in case_ids]
    plan = choose_cases(cases, max_api_calls=500)
    cases_by_id = {case["id"]: case for case in cases}

    def fake_run(command: list[str], **_kwargs: object) -> object:
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="completed answer",
            llm_spans=2,
            harness_status=statuses.get(case_id, "completed"),
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    return run_suite._execute_plan(
        plan,
        run_id="run-1",
        cases_path=tmp_path / "cases.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
    )


def test_execute_contains_isolated_async_followup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # One case's async follow-up failing does not make the remaining paid cases
    # untrustworthy: the case is already judged inconclusive, which floors the
    # suite exit code. Aborting only forfeited the unrun cases.
    summary = _execute_with_harness_statuses(
        monkeypatch,
        tmp_path,
        {"C2": "async_followup_failed"},
        ["C1", "C2", "C3", "C4"],
    )

    assert summary["attempted_count"] == 4
    assert summary["missing_case_ids"] == []
    assert summary["abort_reason"] is None
    assert summary["harness_failures"] == [
        {"case_id": "C2", "harness_status": "async_followup_failed"}
    ]


def test_execute_aborts_on_session_mismatch_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A session mismatch means the harness itself cannot be trusted to bind a
    # query to its trace, so it stays suite-fatal on the first occurrence.
    summary = _execute_with_harness_statuses(
        monkeypatch,
        tmp_path,
        {"C2": "session_mismatch"},
        ["C1", "C2", "C3"],
    )

    assert summary["attempted_count"] == 2
    assert summary["missing_case_ids"] == ["C3"]
    assert "session_mismatch" in summary["abort_reason"]


def test_execute_aborts_after_consecutive_harness_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Two in a row is the systemic signal; one is not.
    summary = _execute_with_harness_statuses(
        monkeypatch,
        tmp_path,
        {"C2": "async_followup_failed", "C3": "cli_failed"},
        ["C1", "C2", "C3", "C4"],
    )

    assert summary["attempted_count"] == 3
    assert summary["missing_case_ids"] == ["C4"]
    assert "consecutive harness failures" in summary["abort_reason"]


def test_execute_resets_consecutive_counter_after_a_clean_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Non-adjacent isolated failures must not accumulate into a false abort.
    summary = _execute_with_harness_statuses(
        monkeypatch,
        tmp_path,
        {"C1": "async_followup_failed", "C3": "cli_failed"},
        ["C1", "C2", "C3", "C4"],
    )

    assert summary["attempted_count"] == 4
    assert summary["abort_reason"] is None
    assert len(summary["harness_failures"]) == 2


def test_execute_rechecks_cases_snapshot_before_each_paid_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("C1"), _case("C2")]
    plan = choose_cases(cases, max_api_calls=6)
    cases_by_id = {case["id"]: case for case in cases}
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    snapshot.parent.mkdir()
    snapshot_bytes = b"immutable selected cases"
    snapshot.write_bytes(snapshot_bytes)

    def fake_run(command: list[str], **_kwargs: object) -> object:
        assert Path(command[command.index("--cases") + 1]) == snapshot
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="completed answer",
            llm_spans=2,
        )
        snapshot.write_bytes(b"changed after first paid case")
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-snapshot",
        cases_path=snapshot,
        cases_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )

    assert summary["attempted_count"] == 1
    assert summary["missing_case_ids"] == ["C2"]
    assert "cases snapshot" in summary["abort_reason"]


def test_parent_outcome_gate_records_child_as_skipped_without_paid_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [
        _case("P1", acceptable_outcomes=["data_unavailable"]),
        _case(
            "C1",
            chain_from="P1",
            chain_requires_parent_outcomes=["success"],
        ),
    ]
    plan = choose_cases(cases, max_api_calls=6)
    cases_by_id = {case["id"]: case for case in cases}

    def fake_run(command: list[str], **_kwargs: object) -> object:
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=cases_by_id[case_id],
            response="No directly resolving market exists in the returned sample.",
            llm_spans=2,
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-1",
        cases_path=tmp_path / "cases.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
    )

    assert summary["attempted_count"] == 1
    assert summary["complete"] is True
    assert summary["results"][1]["verdict"] == "skipped_dependency"
    assert summary["skipped"][0]["id"] == "C1"


def test_judgment_binds_attempt_run_and_exact_packet_before_subprocess_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"

    def fake_run(command: list[str], **_kwargs: object) -> object:
        attempt = json.loads((run_dir / "attempts" / "C1.json").read_text())
        assert attempt == run_suite._attempt_payload(
            case,
            run_id="run-bind",
            attempt_nonce=command[command.index("--attempt-nonce") + 1],
            manifest_sha256=command[command.index("--manifest-sha256") + 1],
            cases_snapshot_sha256=command[command.index("--cases-sha256") + 1],
        )
        _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-bind",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )

    packet_path = (run_dir / "C1.json").resolve()
    packet_bytes = packet_path.read_bytes()
    judgment = json.loads((run_dir / "judgments" / "C1.json").read_text())
    assert summary["run_id"] == "run-bind"
    assert judgment["run_id"] == "run-bind"
    assert judgment["packet_path"] == str(packet_path)
    assert judgment["packet_sha256"] == hashlib.sha256(packet_bytes).hexdigest()
    assert judgment["packet_id"] == "C1"
    assert judgment["packet_case_fingerprint"] == run_suite.case_contract_fingerprint(case)
    assert judgment["packet_input_fingerprint"] == "input-C1"
    assert judgment["case_fingerprint"] == fingerprint_case(case)
    assert judgment["observed_model_requests"] == 2
    assert (
        judgment["attempt_nonce"]
        == json.loads((run_dir / "attempts" / "C1.json").read_text())["attempt_nonce"]
    )
    assert (
        judgment["execution_claim_sha256"]
        == hashlib.sha256((run_dir / "claims" / "C1.json").read_bytes()).hexdigest()
    )


def test_execute_stops_before_next_case_when_runtime_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("C1"), _case("C2")]
    plan = choose_cases(cases, max_api_calls=6)
    by_id = {case["id"]: case for case in cases}
    fingerprints = iter(["stable", "changed"])
    monkeypatch.setattr(
        run_suite,
        "_runtime_fingerprint",
        lambda **_kwargs: next(fingerprints),
    )

    def fake_run(command: list[str], **_kwargs: object) -> object:
        case_id = command[command.index("--id") + 1]
        _write_runner_packet(
            command,
            case=by_id[case_id],
            response="completed answer",
            llm_spans=2,
        )
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-runtime",
        cases_path=tmp_path / "cases.yaml",
        run_dir=tmp_path / "run",
        run_one_path=tmp_path / "run_one.py",
        expected_runtime_fingerprint="stable",
    )

    assert summary["attempted_count"] == 1
    assert summary["missing_case_ids"] == ["C2"]
    assert "runtime, helper, model configuration" in summary["abort_reason"]


def test_resume_rejects_foreign_packet_even_when_case_contract_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"
    write_immutable_json(
        run_dir / "attempts" / "C1.json",
        _test_attempt(case, run_id="current-run"),
    )
    command = ["runner", "--id", "C1", "--run-dir", str(run_dir)]
    _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
    packet_path = run_dir / "C1.json"
    packet = json.loads(packet_path.read_text())
    packet["run_id"] = "foreign-run"
    packet["execution_binding"]["run_id"] = "foreign-run"
    packet_path.write_text(json.dumps(packet))
    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("foreign packet recovery must remain offline")
        ),
    )

    summary = run_suite._execute_plan(
        plan,
        run_id="current-run",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert summary["attempted_count"] == 0
    assert summary["resumed_count"] == 0
    assert "packet execution binding mismatch" in summary["abort_reason"]


@pytest.mark.parametrize("tamper_target", ["packet", "judgment", "run_id"])
def test_resume_rejects_tampered_packet_or_stale_judgment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tamper_target: str
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"

    def fake_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    run_suite._execute_plan(
        plan,
        run_id="run-resume",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )

    judgment_path = run_dir / "judgments" / "C1.json"
    if tamper_target == "packet":
        packet = json.loads((run_dir / "C1.json").read_text())
        packet["final_response"] = "tampered after judgment"
        (run_dir / "C1.json").write_text(json.dumps(packet))
    else:
        judgment = json.loads(judgment_path.read_text())
        if tamper_target == "judgment":
            judgment["reason"] = "tampered deterministic result"
        else:
            judgment["run_id"] = "another-run"
        judgment_path.write_text(json.dumps(judgment))

    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resume must reject before invoking the paid runner")
        ),
    )
    with pytest.raises(PlanError, match="resume|judgment|binding"):
        run_suite._execute_plan(
            plan,
            run_id="run-resume",
            cases_path=tmp_path / "cases.yaml",
            run_dir=run_dir,
            run_one_path=tmp_path / "run_one.py",
            resume=True,
        )


def test_finalized_results_are_revalidated_against_bound_judgments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"

    def fake_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-final",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    run_suite._validate_existing_results(summary, plan, run_id="run-final", run_dir=run_dir)

    summary["results"][0]["reason"] = "tampered finalized summary"
    with pytest.raises(PlanError, match="differs from its judgment"):
        run_suite._validate_existing_results(summary, plan, run_id="run-final", run_dir=run_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exit_code", 0),
        ("complete", False),
        ("status", "incomplete"),
        ("observed_model_requests", 999),
        ("hard_model_request_cap_enforced", True),
    ],
)
def test_finalized_summary_fields_cannot_override_bound_judgments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    case = _case("C1", assertions={"manual_assertions": ["review"]})
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"

    def fake_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    summary = run_suite._execute_plan(
        plan,
        run_id="run-summary-bind",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    assert summary["exit_code"] == EXIT_PRODUCT_FAILURE
    summary[field] = value

    with pytest.raises(PlanError, match="summary|match"):
        run_suite._validate_existing_results(
            summary,
            plan,
            run_id="run-summary-bind",
            run_dir=run_dir,
        )


def test_resume_attempt_without_packet_fails_closed_and_never_reruns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"
    write_immutable_json(
        run_dir / "attempts" / "C1.json",
        _test_attempt(case, run_id="run-crash"),
    )
    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a prior attempt must never be paid for twice")
        ),
    )

    summary = run_suite._execute_plan(
        plan,
        run_id="run-crash",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert summary["attempted_count"] == 0
    assert summary["resumed_count"] == 0
    assert summary["missing_case_ids"] == ["C1"]
    assert "prior paid attempt" in summary["abort_reason"]
    assert "refusing to rerun" in summary["abort_reason"]


def test_resume_recovers_completed_packet_offline_without_duplicate_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _case("C1")
    plan = choose_cases([case], max_api_calls=3)
    run_dir = tmp_path / "run"
    write_immutable_json(
        run_dir / "attempts" / "C1.json",
        _test_attempt(case, run_id="run-recover"),
    )
    command = ["runner", "--id", "C1", "--run-dir", str(run_dir)]
    _write_runner_packet(command, case=case, response="completed answer", llm_spans=2)
    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed packet recovery must be offline")
        ),
    )

    summary = run_suite._execute_plan(
        plan,
        run_id="run-recover",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert summary["complete"] is True
    assert summary["attempted_count"] == 0
    assert summary["resumed_count"] == 1
    assert summary["observed_model_requests"] == 2
    assert summary["results"][0]["recovered_from_attempt"] is True
    assert (run_dir / "judgments" / "C1.json").exists()


def test_resume_noncompleted_packet_aborts_before_next_paid_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("C1"), _case("C2")]
    plan = choose_cases(cases, max_api_calls=6)
    run_dir = tmp_path / "run"

    def first_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(
            command,
            case=cases[0],
            response="partial answer",
            llm_spans=2,
            # A suite-fatal status: an isolated per-case harness failure is now
            # contained, so the setup needs a status that still stops the run.
            harness_status="session_mismatch",
        )
        return run_suite.subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

    monkeypatch.setattr(run_suite.subprocess, "run", first_run)
    initial = run_suite._execute_plan(
        plan,
        run_id="run-failed",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    assert initial["attempted_count"] == 1

    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resume must abort before the next paid case")
        ),
    )
    resumed = run_suite._execute_plan(
        plan,
        run_id="run-failed",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert resumed["attempted_count"] == 0
    assert resumed["resumed_count"] == 1
    assert resumed["missing_case_ids"] == ["C2"]
    assert "resumed case C1 ended with harness status 'session_mismatch'" in resumed["abort_reason"]


def test_resume_usage_over_limit_aborts_before_next_paid_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [_case("C1"), _case("C2")]
    plan = choose_cases(cases, max_api_calls=6)
    run_dir = tmp_path / "run"

    def first_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=cases[0], response="completed answer", llm_spans=7)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", first_run)
    run_suite._execute_plan(
        plan,
        run_id="run-over",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resume must enforce observed usage before spending")
        ),
    )

    resumed = run_suite._execute_plan(
        plan,
        run_id="run-over",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert resumed["attempted_count"] == 0
    assert resumed["observed_model_requests"] == 7
    assert resumed["missing_case_ids"] == ["C2"]
    assert "resumed observed model requests reached 7" in resumed["abort_reason"]


def test_resume_count_excludes_loaded_dependency_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [
        _case("P1", acceptable_outcomes=["data_unavailable"]),
        _case("C1", chain_from="P1", chain_requires_parent_outcomes=["success"]),
    ]
    plan = choose_cases(cases, max_api_calls=6)
    run_dir = tmp_path / "run"

    def first_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=cases[0], response="No results found.", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", first_run)
    initial = run_suite._execute_plan(
        plan,
        run_id="run-skip-count",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    assert initial["attempted_count"] == 1
    assert initial["verdict_counts"]["skipped_dependency"] == 1

    monkeypatch.setattr(
        run_suite.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a complete resume must stay offline")
        ),
    )
    resumed = run_suite._execute_plan(
        plan,
        run_id="run-skip-count",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
        resume=True,
    )

    assert resumed["attempted_count"] == 0
    assert resumed["resumed_count"] == 1
    run_suite._validate_existing_results(
        resumed,
        plan,
        run_id="run-skip-count",
        run_dir=run_dir,
    )


def test_resume_revalidates_skipped_dependency_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [
        _case("P1", acceptable_outcomes=["data_unavailable"]),
        _case("C1", chain_from="P1", chain_requires_parent_outcomes=["success"]),
    ]
    plan = choose_cases(cases, max_api_calls=6)
    run_dir = tmp_path / "run"

    def first_run(command: list[str], **_kwargs: object) -> object:
        _write_runner_packet(command, case=cases[0], response="No results found.", llm_spans=2)
        return run_suite.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(run_suite.subprocess, "run", first_run)
    run_suite._execute_plan(
        plan,
        run_id="run-skip",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )
    child_judgment_path = run_dir / "judgments" / "C1.json"
    child_judgment = json.loads(child_judgment_path.read_text())
    child_judgment["reason"] = "stale dependency decision"
    child_judgment_path.write_text(json.dumps(child_judgment))

    with pytest.raises(PlanError, match="skipped judgment is stale"):
        run_suite._execute_plan(
            plan,
            run_id="run-skip",
            cases_path=tmp_path / "cases.yaml",
            run_dir=run_dir,
            run_one_path=tmp_path / "run_one.py",
            resume=True,
        )


def test_case_fingerprint_is_stable_and_content_sensitive() -> None:
    original = _case("C1")
    same_different_order = dict(reversed(list(original.items())))
    changed = {**original, "query": "Changed query"}

    assert fingerprint_case(original) == fingerprint_case(same_different_order)
    assert fingerprint_case(original) != fingerprint_case(changed)


def test_manifest_snapshots_cases_and_fingerprints() -> None:
    cases = [_case("C1")]
    plan = choose_cases(cases)

    manifest = build_manifest(plan, cases_path=Path("cases.yaml"), cases_bytes=b"test")

    assert manifest["planned_count"] == 1
    assert manifest["estimated_api_calls"] == 3
    assert manifest["cases"][0]["snapshot"] == cases[0]
    assert manifest["cases"][0]["fingerprint"] == fingerprint_case(cases[0])


def test_execute_main_uses_immutable_run_bound_cases_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases_path = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    original_bytes = json.dumps({"default_tier": "core", "test_cases": [_case("C1")]}).encode()
    cases_path.write_bytes(original_bytes)
    captured: dict[str, object] = {}

    def fake_execute(plan: run_suite.SuitePlan, **kwargs: object) -> dict:
        cases_path.write_text(json.dumps({"test_cases": [_case("C1", query="MUTATED")]}))
        snapshot = Path(str(kwargs["cases_path"]))
        captured.update(kwargs)
        assert snapshot == (run_dir / "cases.snapshot.yaml").resolve()
        assert snapshot.read_bytes() == original_bytes
        assert kwargs["cases_sha256"] == hashlib.sha256(original_bytes).hexdigest()
        summary = run_suite._dry_run_summary(plan)
        summary["mode"] = "execute"
        return summary

    monkeypatch.setattr(run_suite, "_run_preflight", lambda _path: None)
    monkeypatch.setattr(run_suite, "_execute_plan", fake_execute)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--execute",
            "--max-api-calls",
            "3",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert run_suite.main() == EXIT_SUCCESS
    manifest = json.loads((run_dir / "manifest.json").read_text())
    snapshot_path = run_dir / "cases.snapshot.yaml"
    assert manifest["cases_snapshot_path"] == str(snapshot_path.resolve())
    assert manifest["cases_snapshot_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert snapshot_path.stat().st_mode & 0o222 == 0
    with pytest.raises(ImmutableManifestError):
        run_suite.write_immutable_bytes(snapshot_path, b"replacement")
    assert captured["calendar_anchor"] == manifest["calendar_anchor"]


def test_resume_manifest_rejects_tampered_cases_snapshot(tmp_path: Path) -> None:
    cases_bytes = b'{"test_cases": []}'
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    run_suite.write_immutable_bytes(snapshot, cases_bytes)
    plan = choose_cases([_case("C1")])
    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=cases_bytes,
        cases_snapshot_path=snapshot,
        mode="execute",
    )

    validate_resume_manifest(manifest, plan, cases_bytes=cases_bytes, run_dir=run_dir)
    snapshot.write_bytes(b"tampered")

    with pytest.raises(PlanError, match="snapshot"):
        validate_resume_manifest(manifest, plan, cases_bytes=cases_bytes, run_dir=run_dir)


def test_manifest_writer_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    write_immutable_json(path, {"version": 1})

    with pytest.raises(ImmutableManifestError):
        write_immutable_json(path, {"version": 2})

    assert json.loads(path.read_text()) == {"version": 1}


def test_immutable_writer_fsyncs_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(run_suite, "_fsync_directory", lambda path: synced.append(path))

    path = tmp_path / "attempts" / "C1.json"
    write_immutable_json(path, {"case_id": "C1"})

    assert synced == [path.parent, tmp_path, path.parent]


def test_attempt_directory_entry_is_fsynced_before_paid_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Creating attempts/ must be durable before its nonce can authorize spend."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events: list[tuple[str, Path | None]] = []

    monkeypatch.setattr(
        run_suite,
        "_fsync_directory",
        lambda path: events.append(("fsync", Path(path))),
    )

    class Completed:
        returncode = 2
        stdout = ""
        stderr = "offline runner failure"

    def fake_run(*_args: object, **_kwargs: object) -> Completed:
        events.append(("subprocess", None))
        return Completed()

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)

    run_suite._execute_plan(
        choose_cases([_case("C1")], max_api_calls=3),
        run_id="obai-e2e-20260716T120000Z-12345678",
        cases_path=tmp_path / "cases.yaml",
        run_dir=run_dir,
        run_one_path=tmp_path / "run_one.py",
    )

    paid_start = events.index(("subprocess", None))
    assert ("fsync", run_dir) in events[:paid_start]


def test_execute_manifest_binds_selected_helper_paths_and_bytes(tmp_path: Path) -> None:
    case = _case("C1")
    plan = choose_cases([case])
    cases_bytes = b"suite-v1"
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    write_immutable_json(snapshot, {"suite": "v1"})
    cases_bytes = snapshot.read_bytes()
    run_one_path = tmp_path / "custom_run_one.py"
    preflight_path = tmp_path / "custom_preflight.py"
    run_one_path.write_text("# runner v1\n")
    preflight_path.write_text("# preflight v1\n")
    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=cases_bytes,
        cases_snapshot_path=snapshot,
        mode="execute",
        run_one_path=run_one_path,
        preflight_path=preflight_path,
    )

    assert manifest["runtime_helpers"]["run_one"]["path"] == str(run_one_path.resolve())
    validate_resume_manifest(
        manifest,
        plan,
        cases_bytes=cases_bytes,
        run_dir=run_dir,
        run_one_path=run_one_path,
        preflight_path=preflight_path,
    )

    run_one_path.write_text("# runner v2\n")
    with pytest.raises(PlanError, match="helper path/content"):
        validate_resume_manifest(
            manifest,
            plan,
            cases_bytes=cases_bytes,
            run_dir=run_dir,
            run_one_path=run_one_path,
            preflight_path=preflight_path,
        )


def test_execute_manifest_binds_the_scoring_contract(tmp_path: Path) -> None:
    """judge_packet.py decides every deterministic verdict, so name it.

    It was reachable only through the whole-tree runtime fingerprint, which
    proves *something* under scripts/ moved but not what. Editing it after a
    run was finalized surfaced as "resume deterministic judgment mismatch
    for CORE-CRYPTO-BOUNDARY" -- a case name, with no hint that the scoring
    contract itself had changed underneath the stored judgments.
    """
    plan = choose_cases([_case("C1")])
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    write_immutable_json(snapshot, {"suite": "v1"})

    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=snapshot.read_bytes(),
        cases_snapshot_path=snapshot,
        mode="execute",
    )

    binding = manifest["runtime_helpers"]["judge_packet"]
    assert binding["path"] == str(run_suite.DEFAULT_JUDGE_PACKET.resolve())
    assert (
        binding["sha256"] == hashlib.sha256(run_suite.DEFAULT_JUDGE_PACKET.read_bytes()).hexdigest()
    )


def test_scoring_contract_edit_is_named_in_the_resume_error(tmp_path: Path) -> None:
    """A changed judge must fail the recheck by name, not by symptom."""
    plan = choose_cases([_case("C1")])
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    write_immutable_json(snapshot, {"suite": "v1"})
    cases_bytes = snapshot.read_bytes()
    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=cases_bytes,
        cases_snapshot_path=snapshot,
        mode="execute",
    )
    manifest["runtime_helpers"]["judge_packet"]["sha256"] = "0" * 64

    with pytest.raises(PlanError, match=r"helper path/content changed.*: judge_packet$"):
        validate_resume_manifest(
            manifest,
            plan,
            cases_bytes=cases_bytes,
            run_dir=run_dir,
        )


def test_runtime_fingerprint_changes_with_effective_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-a")
    first = run_suite._runtime_fingerprint()
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-b")

    assert run_suite._runtime_fingerprint() != first


@pytest.mark.parametrize(
    "key",
    [
        "LANGCACHE_ENABLED",
        "LANGCACHE_SERVER_URL",
        "TOOL_CACHE_TTL",
        "OPIK_ENABLED",
        "OPIK_URL",
        "OPENAI_BASE_URL",
    ],
)
def test_runtime_fingerprint_binds_active_nonsecret_environment(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    monkeypatch.delenv(key, raising=False)
    first = run_suite._runtime_fingerprint(bind_credential=False)
    mutated_value = "http://offline-mutated:5173" if key == "OPIK_URL" else "offline-mutated-value"
    monkeypatch.setenv(key, mutated_value)

    assert run_suite._runtime_fingerprint(bind_credential=False) != first


def test_runtime_fingerprint_binds_preferences_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    preferences = tmp_path / ".obai" / "preferences.json"
    preferences.parent.mkdir()
    preferences.write_text('{"risk_tolerance":"moderate"}')
    first = run_suite._runtime_fingerprint(bind_credential=False)
    preferences.write_text('{"risk_tolerance":"conservative"}')

    assert run_suite._runtime_fingerprint(bind_credential=False) != first


def test_runtime_fingerprint_binds_cli_managed_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI-loaded model and Opik settings cannot evade resume binding."""
    monkeypatch.setenv("HOME", str(tmp_path))
    env_file = tmp_path / ".obai" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("SPECIALIST_MODEL=model-a\nOPIK_URL=http://one:5173\n")
    first = run_suite._runtime_fingerprint(bind_credential=False)
    env_file.write_text("SPECIALIST_MODEL=model-b\nOPIK_URL=http://two:5173\n")

    assert run_suite._runtime_fingerprint(bind_credential=False) != first


def test_secret_runtime_environment_is_bound_without_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-serialize-this-langcache-key"
    monkeypatch.setenv("LANGCACHE_API_KEY", secret)
    first = run_suite.runtime_environment_binding()
    rendered = json.dumps(first, sort_keys=True)

    assert secret not in rendered
    assert "LANGCACHE_API_KEY" in first["secret_digests"]
    monkeypatch.setenv("LANGCACHE_API_KEY", secret + "-changed")
    assert run_suite.runtime_environment_binding() != first


def test_resume_manifest_requires_identical_suite_and_case_fingerprints(tmp_path: Path) -> None:
    cases = [_case("C1")]
    plan = choose_cases(cases)
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    run_suite.write_immutable_bytes(snapshot, b"suite-v1")
    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=b"suite-v1",
        cases_snapshot_path=snapshot,
        mode="execute",
    )

    validate_resume_manifest(manifest, plan, cases_bytes=b"suite-v1", run_dir=run_dir)

    with pytest.raises(PlanError):
        validate_resume_manifest(manifest, plan, cases_bytes=b"suite-v2", run_dir=run_dir)


def test_resume_rejects_live_manifest_older_than_case_ttl(tmp_path: Path) -> None:
    cases = [
        _case(
            "L1",
            tier="live",
            date_policy="live",
            max_age_seconds=300,
        )
    ]
    plan = choose_cases(
        cases,
        tiers={"live"},
        allow_expensive=True,
        max_api_calls=10,
    )
    run_dir = tmp_path / "run"
    snapshot = run_dir / "cases.snapshot.yaml"
    run_suite.write_immutable_bytes(snapshot, b"suite-v1")
    manifest = build_manifest(
        plan,
        cases_path=tmp_path / "cases.yaml",
        cases_bytes=b"suite-v1",
        cases_snapshot_path=snapshot,
        mode="execute",
    )
    manifest["created_at"] = "2000-01-01T00:00:00Z"

    with pytest.raises(PlanError, match="freshness SLA"):
        validate_resume_manifest(manifest, plan, cases_bytes=b"suite-v1", run_dir=run_dir)


def test_dry_run_makes_no_execution_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    cases_path.write_text(
        json.dumps(
            {
                "default_tier": "core",
                "test_cases": [_case("C1")],
            }
        )
    )

    def forbidden_execute(*args: object, **kwargs: object) -> dict:
        raise AssertionError("dry-run must not invoke the paid runner")

    monkeypatch.setattr(run_suite, "_execute_plan", forbidden_execute)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--dry-run",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert run_suite.main() == EXIT_SUCCESS
    results = json.loads((run_dir / "results.json").read_text())
    assert results["attempted_count"] == 0
    assert results["estimated_api_calls"] == 3
    assert results["between_case_model_request_limit"] == 3
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["calendar_anchor"].endswith("Z")
    assert manifest["cases_snapshot_path"] is None
    assert not (run_dir / "cases.snapshot.yaml").exists()


def test_implicit_cap_matches_selected_smoke_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases_path = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    cases_path.write_text(
        json.dumps(
            {
                "default_tier": "core",
                "test_cases": [
                    _case("C1", estimated_api_calls=12),
                    _case("S1", tier="smoke", estimated_api_calls=4),
                ],
            }
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--dry-run",
            "--tier",
            "smoke",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert run_suite.main() == EXIT_SUCCESS
    results = json.loads((run_dir / "results.json").read_text())
    assert results["estimated_model_requests"] == 4
    assert results["between_case_model_request_limit"] == 4


def test_live_main_requires_explicit_model_request_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases_path = tmp_path / "cases.yaml"
    cases_path.write_text(
        json.dumps({"default_tier": "core", "test_cases": [_case("L1", tier="live")]})
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--dry-run",
            "--tier",
            "live",
            "--allow-expensive",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(tmp_path / "run"),
        ],
    )

    assert run_suite.main() == EXIT_CONFIGURATION


def test_every_paid_execution_requires_explicit_between_case_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases_path = tmp_path / "cases.yaml"
    cases_path.write_text(json.dumps({"default_tier": "core", "test_cases": [_case("C1")]}))
    monkeypatch.setattr(
        run_suite,
        "_run_preflight",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("configuration must fail before preflight")
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--execute",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(tmp_path / "run"),
        ],
    )

    assert run_suite.main() == EXIT_CONFIGURATION


def test_execute_stops_on_preflight_before_any_paid_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases_path = tmp_path / "cases.yaml"
    run_dir = tmp_path / "run"
    cases_path.write_text(json.dumps({"default_tier": "core", "test_cases": [_case("C1")]}))
    monkeypatch.setattr(run_suite, "_run_preflight", lambda _path: "Opik is down")
    monkeypatch.setattr(
        run_suite,
        "_execute_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paid execution must not start")
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_suite.py",
            "--execute",
            "--max-api-calls",
            "3",
            "--cases",
            str(cases_path),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert run_suite.main() == EXIT_INFRASTRUCTURE


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ({"complete": True, "verdict_counts": {"pass": 2}}, EXIT_SUCCESS),
        ({"complete": True, "verdict_counts": {"fail_product": 1}}, EXIT_PRODUCT_FAILURE),
        ({"complete": False, "verdict_counts": {"pass": 1}}, EXIT_INFRASTRUCTURE),
        (
            {"complete": True, "verdict_counts": {"inconclusive_provider": 1}},
            EXIT_INFRASTRUCTURE,
        ),
        (
            {
                "complete": False,
                "verdict_counts": {"fail_product": 1, "inconclusive_harness": 1},
            },
            EXIT_PRODUCT_FAILURE,
        ),
    ],
)
def test_summary_exit_codes_are_meaningful(summary: dict, expected: int) -> None:
    assert exit_code_for_summary(summary) == expected
