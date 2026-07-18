from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import run_suite
import yaml
from finalize_review import ReviewError, finalize_results


@pytest.fixture(autouse=True)
def _stable_test_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key-not-for-api-use")


def _preliminary(run_dir: Path) -> dict:
    case = {
        "id": "T1",
        "feature": "semantic arithmetic review",
        "query": "Recompute the captured value.",
        "tier": "core",
        "estimated_api_calls": 1,
        "expected_outcome": "success",
        "assertions": {"manual_assertions": ["verify arithmetic"]},
    }
    plan = run_suite.choose_cases([case], max_api_calls=1)
    cases_bytes = yaml.safe_dump(
        {"default_tier": "core", "test_cases": [case]}, sort_keys=False
    ).encode()
    snapshot_path = run_dir / run_suite.CASES_SNAPSHOT_NAME
    snapshot_path.write_bytes(cases_bytes)
    manifest = run_suite.build_manifest(
        plan,
        cases_path=run_dir / "source-cases.yaml",
        cases_bytes=cases_bytes,
        mode="execute",
        cases_snapshot_path=snapshot_path,
        run_id="run-1",
    )
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    cases_sha256 = hashlib.sha256(cases_bytes).hexdigest()

    attempt = run_suite._attempt_payload(
        case,
        run_id="run-1",
        attempt_nonce="a" * 64,
        manifest_sha256=manifest_sha256,
        cases_snapshot_sha256=cases_sha256,
    )
    attempt_path = run_dir / "attempts" / "T1.json"
    run_suite.write_immutable_json(attempt_path, attempt)
    attempt_bytes = attempt_path.read_bytes()
    execution_binding = run_suite._attempt_execution_binding(
        attempt,
        attempt_bytes=attempt_bytes,
    )
    claims_dir = run_dir / "claims"
    claims_dir.mkdir()
    (claims_dir / "T1.json").write_text(
        json.dumps(execution_binding, sort_keys=True, separators=(",", ":")) + "\n"
    )

    packet = {
        "id": "T1",
        "case_fingerprint": run_suite.case_contract_fingerprint(case),
        "input_fingerprint": "input-T1",
        "execution_binding": execution_binding,
        "run_id": execution_binding["run_id"],
        "attempt_nonce": execution_binding["attempt_nonce"],
        "manifest_sha256": execution_binding["manifest_sha256"],
        "cases_snapshot_sha256": execution_binding["cases_snapshot_sha256"],
        "attempt_marker_sha256": execution_binding["attempt_marker_sha256"],
        "harness_status": "completed",
        "harness_exit_code": 0,
        "latency_ms": 10,
        "cli": {
            "exit_code": 0,
            "timed_out": False,
            "stdout_json": {"response": "The recomputed value is 12."},
            "stderr": "",
        },
        "final_response": "The recomputed value is 12.",
        "trace": {
            "id": "trace-1",
            "spans": [
                {
                    "id": "span-1",
                    "type": "llm",
                    "name": "Response",
                    "output": {"recomputed_value": 12},
                }
            ],
        },
    }
    packet_path = run_dir / "T1.json"
    packet_bytes = json.dumps(packet, sort_keys=True).encode()
    packet_path.write_bytes(packet_bytes)
    judgment = run_suite._judgment_from_packet(
        case,
        run_id="run-1",
        packet_path=packet_path,
        packet=packet,
        packet_bytes=packet_bytes,
        expected_execution_binding=execution_binding,
    )
    assert judgment["verdict"] == "needs_semantic_review"
    (run_dir / "judgments").mkdir()
    (run_dir / "judgments" / "T1.json").write_text(json.dumps(judgment))
    preliminary = {
        "schema_version": 1,
        "run_id": "run-1",
        "mode": "execute",
        "status": "complete",
        "planned_count": 1,
        "attempted_count": 1,
        "resumed_count": 0,
        "packet_count": 1,
        "judged_count": 1,
        "completed_case_ids": ["T1"],
        "missing_case_ids": [],
        "skipped": [],
        "complete": True,
        "estimated_api_calls": 1,
        "estimated_model_requests": 1,
        "observed_model_requests": 1,
        "between_case_model_request_limit": 1,
        "hard_model_request_cap_enforced": False,
        "model_request_accounting_complete": True,
        "abort_reason": None,
        "results": [judgment],
        "verdict_counts": {"needs_semantic_review": 1},
        "exit_code": run_suite.EXIT_PRODUCT_FAILURE,
    }
    (run_dir / "results.json").write_text(json.dumps(preliminary))
    return preliminary


def _reviews(preliminary: dict, *, status: str = "pass") -> dict:
    packet_sha256 = preliminary["results"][0]["packet_sha256"]
    case_fingerprint = preliminary["results"][0]["case_fingerprint"]
    return {
        "schema_version": 2,
        "run_id": "run-1",
        "reviews": [
            {
                "case_id": "T1",
                "case_fingerprint": case_fingerprint,
                "packet_sha256": packet_sha256,
                "summary": "Recomputed against the captured specialist payload.",
                "assertions": [
                    {
                        "assertion": "manual_assertions[0]:verify arithmetic",
                        "status": status,
                        "evidence": {
                            "analysis": "The captured specialist value 12 reconciles to the final value 12.",
                            "references": [
                                {
                                    "case_id": "T1",
                                    "packet_sha256": packet_sha256,
                                    "json_path": "trace.spans[0].output.recomputed_value",
                                    "span_id": "span-1",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def test_complete_evidence_backed_review_can_finalize_pass(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    result = finalize_results(preliminary, _reviews(preliminary), run_dir=tmp_path)

    assert result["results"][0]["verdict"] == "pass"
    assert result["results"][0]["deterministic_verdict"] == "needs_semantic_review"
    assert result["semantic_review_complete"] is True
    assert result["exit_code"] == 0


def test_failed_semantic_assertion_becomes_product_failure(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    result = finalize_results(preliminary, _reviews(preliminary, status="fail"), run_dir=tmp_path)

    assert result["results"][0]["verdict"] == "fail_product"
    assert result["exit_code"] == 1


def test_insufficient_semantic_evidence_stays_inconclusive(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    result = finalize_results(
        preliminary, _reviews(preliminary, status="inconclusive"), run_dir=tmp_path
    )

    assert result["results"][0]["verdict"] == "inconclusive_missing_evidence"
    assert result["exit_code"] == 3


def test_missing_or_mismatched_review_fails_closed(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    missing = {"schema_version": 2, "run_id": "run-1", "reviews": []}
    with pytest.raises(ReviewError, match="missing semantic review"):
        finalize_results(preliminary, missing, run_dir=tmp_path)

    mismatched = _reviews(preliminary)
    mismatched["reviews"][0]["case_fingerprint"] = "other"
    with pytest.raises(ReviewError, match="fingerprint mismatch"):
        finalize_results(preliminary, mismatched, run_dir=tmp_path)


@pytest.mark.parametrize("run_id", [None, ""])
def test_missing_preliminary_run_id_fails_closed(tmp_path: Path, run_id: object) -> None:
    preliminary = _preliminary(tmp_path)
    preliminary["run_id"] = run_id

    with pytest.raises(ReviewError, match="preliminary results run_id"):
        finalize_results(preliminary, _reviews(preliminary), run_dir=tmp_path)


@pytest.mark.parametrize("fingerprint", [None, ""])
def test_missing_case_fingerprint_fails_closed(tmp_path: Path, fingerprint: object) -> None:
    preliminary = _preliminary(tmp_path)
    preliminary["results"][0]["case_fingerprint"] = fingerprint

    with pytest.raises(ReviewError, match="case fingerprint|judgment"):
        finalize_results(preliminary, _reviews(preliminary), run_dir=tmp_path)


def test_duplicate_preliminary_case_fails_closed(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    preliminary["results"].append(dict(preliminary["results"][0]))

    with pytest.raises(ReviewError, match="invalid result list|duplicate case T1"):
        finalize_results(preliminary, _reviews(preliminary), run_dir=tmp_path)


def test_free_form_evidence_string_cannot_finalize(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    reviews = _reviews(preliminary)
    reviews["reviews"][0]["assertions"][0]["evidence"] = "x"

    with pytest.raises(ReviewError, match="structured evidence"):
        finalize_results(preliminary, reviews, run_dir=tmp_path)


def test_invented_packet_path_or_span_cannot_finalize(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    reviews = _reviews(preliminary)
    reference = reviews["reviews"][0]["assertions"][0]["evidence"]["references"][0]
    reference["json_path"] = "trace.spans[0].output.not_real"

    with pytest.raises(ReviewError, match="cites missing path"):
        finalize_results(preliminary, reviews, run_dir=tmp_path)

    reviews = _reviews(preliminary)
    reference = reviews["reviews"][0]["assertions"][0]["evidence"]["references"][0]
    reference["span_id"] = "invented-span"
    with pytest.raises(ReviewError, match="unknown span"):
        finalize_results(preliminary, reviews, run_dir=tmp_path)


def test_packet_tampering_after_judgment_cannot_finalize(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    reviews = _reviews(preliminary)
    Path(preliminary["results"][0]["packet_path"]).write_text("{}")

    with pytest.raises(ReviewError, match="packet|SHA-256 mismatch"):
        finalize_results(preliminary, reviews, run_dir=tmp_path)


def test_tampered_preliminary_pending_verdict_cannot_finalize_green(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    preliminary["results"][0]["verdict"] = "pass"
    preliminary["results"][0]["unexecuted_assertions"] = []
    preliminary["verdict_counts"] = {"pass": 1}
    preliminary["exit_code"] = 0
    empty_reviews = {"schema_version": 2, "run_id": "run-1", "reviews": []}

    with pytest.raises(ReviewError, match="judgment|authenticated"):
        finalize_results(preliminary, empty_reviews, run_dir=tmp_path)


def test_tampered_result_and_judgment_are_recomputed_before_finalize(tmp_path: Path) -> None:
    preliminary = _preliminary(tmp_path)
    result = preliminary["results"][0]
    result["verdict"] = "pass"
    result["unexecuted_assertions"] = []
    result["reason"] = "tampered to look green"
    preliminary["verdict_counts"] = {"pass": 1}
    preliminary["exit_code"] = 0
    (tmp_path / "judgments" / "T1.json").write_text(json.dumps(result))
    empty_reviews = {"schema_version": 2, "run_id": "run-1", "reviews": []}

    with pytest.raises(ReviewError, match="deterministic judgment|stale|authenticated"):
        finalize_results(preliminary, empty_reviews, run_dir=tmp_path)


@pytest.mark.parametrize("target", ["manifest_case", "cases_snapshot"])
def test_tampered_manifest_or_cases_snapshot_cannot_finalize(tmp_path: Path, target: str) -> None:
    preliminary = _preliminary(tmp_path)
    if target == "manifest_case":
        manifest_path = tmp_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["cases"][0]["fingerprint"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
    else:
        (tmp_path / run_suite.CASES_SNAPSHOT_NAME).write_text("test_cases: []\n")

    with pytest.raises(ReviewError, match="manifest|snapshot|fingerprint"):
        finalize_results(preliminary, _reviews(preliminary), run_dir=tmp_path)
