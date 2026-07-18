#!/usr/bin/env python3
"""Finalize offline semantic review without making any OBaI or provider calls.

The paid runner's deterministic results remain immutable. This script validates
an evidence-backed review for every ``needs_semantic_review`` case and writes a
separate ``reviewed-results.json`` artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import run_suite
import yaml
from run_suite import (
    EXIT_CONFIGURATION,
    ImmutableManifestError,
    exit_code_for_summary,
    write_immutable_json,
)


class ReviewError(ValueError):
    """A semantic-review artifact is incomplete or does not match the run."""


_MISSING = object()
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _dig(value: object, path: str) -> object:
    """Resolve a conservative dot/bracket path without evaluating expressions."""
    if not path:
        return value
    current = value
    for token in re.findall(r"[^.\[\]]+", path):
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _span_ids(packet: dict[str, Any]) -> set[str]:
    """Collect span IDs from the initial and any async follow-up traces."""
    traces: list[object] = [packet.get("trace")]
    followup = packet.get("followup")
    if isinstance(followup, dict):
        traces.append(followup.get("trace"))
        polls = followup.get("polls")
        if isinstance(polls, list):
            traces.extend(poll.get("trace") for poll in polls if isinstance(poll, dict))
    ids: set[str] = set()
    for trace in traces:
        if not isinstance(trace, dict) or not isinstance(trace.get("spans"), list):
            continue
        for span in trace["spans"]:
            if isinstance(span, dict) and isinstance(span.get("id"), str) and span["id"]:
                ids.add(span["id"])
    return ids


def _load_bound_packets(
    preliminary: dict[str, Any], run_dir: Path
) -> dict[str, tuple[dict[str, Any], str]]:
    """Load only packets whose path, run, identity, and digest are bound by results."""
    run_dir = run_dir.resolve()
    raw_results = preliminary.get("results")
    if not isinstance(raw_results, list):
        raise ReviewError("preliminary results must contain a results list")
    run_id = preliminary.get("run_id")
    packets: dict[str, tuple[dict[str, Any], str]] = {}
    for result in raw_results:
        if not isinstance(result, dict) or result.get("verdict") == "skipped_dependency":
            continue
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReviewError("preliminary case result has no case_id")
        if result.get("run_id") != run_id:
            raise ReviewError(f"preliminary result run_id mismatch for {case_id}")
        digest = result.get("packet_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ReviewError(f"preliminary result for {case_id} has no valid packet SHA-256")
        packet_path = result.get("packet_path")
        if not isinstance(packet_path, str):
            raise ReviewError(f"preliminary result for {case_id} has no packet path")
        resolved = Path(packet_path).resolve()
        if not resolved.is_relative_to(run_dir) or resolved.parent != run_dir:
            raise ReviewError(f"packet path for {case_id} is outside the run directory")
        try:
            packet_bytes = resolved.read_bytes()
            packet = json.loads(packet_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReviewError(f"cannot load packet for {case_id}: {exc}") from exc
        if hashlib.sha256(packet_bytes).hexdigest() != digest:
            raise ReviewError(f"packet SHA-256 mismatch for {case_id}")
        if not isinstance(packet, dict) or packet.get("id") != case_id:
            raise ReviewError(f"packet identity mismatch for {case_id}")
        packets[case_id] = (packet, digest)
    return packets


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{path} root must be an object")
    return value


def _authenticated_manifest_plan(run_dir: Path, run_id: str) -> run_suite.SuitePlan:
    """Reconstruct and authenticate the immutable plan used by the paid run."""
    manifest = _load_object(run_dir / "manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("mode") != "execute":
        raise ReviewError("run manifest is not a supported execute manifest")
    if manifest.get("run_id") != run_id:
        raise ReviewError("run manifest run_id does not match preliminary results")
    try:
        run_suite._parse_calendar_anchor(manifest.get("calendar_anchor"))
        snapshot_path = run_suite._validate_cases_snapshot(manifest, run_dir)
        snapshot = yaml.safe_load(snapshot_path.read_bytes())
    except (OSError, run_suite.PlanError, yaml.YAMLError) as exc:
        raise ReviewError(f"run manifest or cases snapshot is invalid: {exc}") from exc
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("test_cases"), list):
        raise ReviewError("run-bound cases snapshot has no test_cases list")

    snapshot_cases: dict[str, dict[str, Any]] = {}
    for raw_case in snapshot["test_cases"]:
        if not isinstance(raw_case, dict):
            raise ReviewError("run-bound cases snapshot contains a non-object case")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in snapshot_cases:
            raise ReviewError("run-bound cases snapshot has invalid or duplicate case IDs")
        snapshot_cases[case_id] = raw_case

    recorded = manifest.get("cases")
    planned_count = manifest.get("planned_count")
    if (
        not isinstance(recorded, list)
        or isinstance(planned_count, bool)
        or not isinstance(planned_count, int)
        or planned_count < 1
        or len(recorded) != planned_count
    ):
        raise ReviewError("run manifest has an invalid planned case list")

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        for entry in recorded:
            if not isinstance(entry, dict) or not isinstance(entry.get("snapshot"), dict):
                raise ReviewError("run manifest contains an invalid case snapshot")
            case = entry["snapshot"]
            case_id = case.get("id")
            if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
                raise ReviewError("run manifest contains invalid or duplicate case IDs")
            seen_ids.add(case_id)
            if entry.get("id") != case_id or snapshot_cases.get(case_id) != case:
                raise ReviewError(f"run manifest case snapshot mismatch for {case_id}")
            fingerprint = run_suite.fingerprint_case(case)
            if entry.get("fingerprint") != fingerprint:
                raise ReviewError(f"run manifest case fingerprint mismatch for {case_id}")
            if entry.get("tier") != run_suite._tier(case):
                raise ReviewError(f"run manifest case tier mismatch for {case_id}")
            if entry.get("estimated_api_calls") != run_suite._case_api_calls(case):
                raise ReviewError(f"run manifest case cost mismatch for {case_id}")
            cases.append(case)
    except run_suite.PlanError as exc:
        raise ReviewError(f"run manifest case contract is invalid: {exc}") from exc

    selected_tiers = manifest.get("selected_tiers")
    estimated = manifest.get("estimated_api_calls")
    max_api_calls = manifest.get("between_case_model_request_limit")
    expected_tiers = sorted({run_suite._tier(case) for case in cases})
    expected_estimate = sum(run_suite._case_api_calls(case) for case in cases)
    if selected_tiers != expected_tiers:
        raise ReviewError("run manifest selected tiers do not match its cases")
    if (
        isinstance(estimated, bool)
        or not isinstance(estimated, int)
        or estimated != expected_estimate
        or manifest.get("estimated_model_requests") != expected_estimate
    ):
        raise ReviewError("run manifest estimated cost does not match its cases")
    if (
        isinstance(max_api_calls, bool)
        or not isinstance(max_api_calls, int)
        or max_api_calls < expected_estimate
    ):
        raise ReviewError("run manifest has an invalid model-request limit")
    return run_suite.SuitePlan(
        cases=cases,
        selected_tiers=tuple(selected_tiers),
        estimated_api_calls=estimated,
        max_api_calls=max_api_calls,
    )


def _authenticate_preliminary(preliminary: dict[str, Any], *, run_dir: Path, run_id: str) -> None:
    """Bind preliminary results to manifest, attempts, packets, and re-judging."""
    plan = _authenticated_manifest_plan(run_dir, run_id)
    try:
        run_suite._validate_existing_results(
            preliminary,
            plan,
            run_id=run_id,
            run_dir=run_dir,
        )
    except run_suite.PlanError as exc:
        raise ReviewError(f"preliminary results are not authenticated by judgments: {exc}") from exc


def _review_map(review_artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reviews = review_artifact.get("reviews")
    if not isinstance(reviews, list):
        raise ReviewError("semantic review must contain a reviews list")
    mapped: dict[str, dict[str, Any]] = {}
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ReviewError(f"reviews[{index}] must be an object")
        case_id = review.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReviewError(f"reviews[{index}].case_id must be a non-empty string")
        if case_id in mapped:
            raise ReviewError(f"duplicate semantic review for {case_id}")
        mapped[case_id] = review
    return mapped


def _validate_case_review(
    result: dict[str, Any],
    review: dict[str, Any],
    packets: dict[str, tuple[dict[str, Any], str]],
) -> tuple[str, dict[str, Any]]:
    case_id = str(result.get("case_id"))
    case_fingerprint = result.get("case_fingerprint")
    if not isinstance(case_fingerprint, str) or not case_fingerprint:
        raise ReviewError(f"preliminary result for {case_id} has no case fingerprint")
    review_fingerprint = review.get("case_fingerprint")
    if not isinstance(review_fingerprint, str) or not review_fingerprint:
        raise ReviewError(f"semantic review for {case_id} has no case fingerprint")
    if review_fingerprint != case_fingerprint:
        raise ReviewError(f"semantic review fingerprint mismatch for {case_id}")
    packet_entry = packets.get(case_id)
    if packet_entry is None:
        raise ReviewError(f"semantic review for {case_id} has no bound packet")
    review_packet_sha = review.get("packet_sha256")
    if review_packet_sha != packet_entry[1]:
        raise ReviewError(f"semantic review packet SHA-256 mismatch for {case_id}")
    summary = review.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 20:
        raise ReviewError(f"semantic review for {case_id} needs a substantive summary")

    expected_assertions = result.get("unexecuted_assertions")
    if not isinstance(expected_assertions, list) or not all(
        isinstance(assertion, str) and assertion for assertion in expected_assertions
    ):
        raise ReviewError(f"preliminary result for {case_id} has invalid pending assertions")
    decisions = review.get("assertions")
    if not isinstance(decisions, list):
        raise ReviewError(f"semantic review for {case_id} needs an assertions list")

    decision_map: dict[str, dict[str, Any]] = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise ReviewError(f"{case_id} assertions[{index}] must be an object")
        assertion = decision.get("assertion")
        if not isinstance(assertion, str) or not assertion:
            raise ReviewError(f"{case_id} assertions[{index}] needs an assertion string")
        if assertion in decision_map:
            raise ReviewError(f"{case_id} has duplicate decision for {assertion!r}")
        status = decision.get("status")
        evidence = decision.get("evidence")
        if status not in {"pass", "fail", "inconclusive"}:
            raise ReviewError(
                f"{case_id} decision {assertion!r} needs status pass/fail/inconclusive"
            )
        if not isinstance(evidence, dict):
            raise ReviewError(f"{case_id} decision {assertion!r} needs structured evidence")
        analysis = evidence.get("analysis")
        references = evidence.get("references")
        if not isinstance(analysis, str) or len(analysis.strip()) < 20:
            raise ReviewError(
                f"{case_id} decision {assertion!r} needs substantive evidence analysis"
            )
        if not isinstance(references, list) or not references:
            raise ReviewError(
                f"{case_id} decision {assertion!r} needs at least one packet reference"
            )
        for ref_index, reference in enumerate(references):
            if not isinstance(reference, dict):
                raise ReviewError(
                    f"{case_id} decision {assertion!r} reference {ref_index} must be an object"
                )
            reference_case = reference.get("case_id")
            referenced = packets.get(reference_case) if isinstance(reference_case, str) else None
            if referenced is None:
                raise ReviewError(f"{case_id} decision {assertion!r} references an unbound packet")
            referenced_packet, referenced_sha = referenced
            if reference.get("packet_sha256") != referenced_sha:
                raise ReviewError(f"{case_id} decision {assertion!r} has a packet SHA-256 mismatch")
            json_path = reference.get("json_path")
            if not isinstance(json_path, str) or not json_path:
                raise ReviewError(
                    f"{case_id} decision {assertion!r} reference {ref_index} needs json_path"
                )
            if _dig(referenced_packet, json_path) is _MISSING:
                raise ReviewError(
                    f"{case_id} decision {assertion!r} cites missing path "
                    f"{reference_case}:{json_path}"
                )
            span_id = reference.get("span_id")
            if span_id is not None and (
                not isinstance(span_id, str) or span_id not in _span_ids(referenced_packet)
            ):
                raise ReviewError(
                    f"{case_id} decision {assertion!r} cites unknown span {span_id!r}"
                )
        decision_map[assertion] = decision

    expected_set = set(expected_assertions)
    observed_set = set(decision_map)
    if observed_set != expected_set:
        missing = sorted(expected_set - observed_set)
        extra = sorted(observed_set - expected_set)
        raise ReviewError(
            f"semantic review assertion mismatch for {case_id}; missing={missing}, extra={extra}"
        )

    statuses = {decision_map[assertion]["status"] for assertion in expected_assertions}
    any_failed = "fail" in statuses
    any_inconclusive = "inconclusive" in statuses
    if any_failed:
        verdict = "fail_product"
        reason = "one or more evidence-backed semantic assertions failed"
    elif any_inconclusive:
        verdict = "inconclusive_missing_evidence"
        reason = "one or more semantic assertions lacked sufficient captured evidence"
    elif result.get("observed_outcome") == "success":
        verdict = "pass"
        reason = "deterministic and evidence-backed semantic checks passed"
    else:
        verdict = "pass_degraded"
        reason = "accepted degraded outcome passed evidence-backed semantic checks"

    semantic_review = {
        "summary": summary.strip(),
        "assertions": [decision_map[assertion] for assertion in expected_assertions],
    }
    return verdict, {"reason": reason, "semantic_review": semantic_review}


def finalize_results(
    preliminary: dict[str, Any], review_artifact: dict[str, Any], *, run_dir: Path
) -> dict[str, Any]:
    if preliminary.get("schema_version") != 1:
        raise ReviewError("preliminary results schema_version must be 1")
    if review_artifact.get("schema_version") != 2:
        raise ReviewError("semantic review schema_version must be 2")
    run_id = preliminary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ReviewError("preliminary results run_id must be a non-empty string")
    review_run_id = review_artifact.get("run_id")
    if not isinstance(review_run_id, str) or not review_run_id:
        raise ReviewError("semantic review run_id must be a non-empty string")
    if review_run_id != run_id:
        raise ReviewError("semantic review run_id does not match preliminary results")
    _authenticate_preliminary(preliminary, run_dir=run_dir.resolve(), run_id=run_id)
    reviews = _review_map(review_artifact)
    packets = _load_bound_packets(preliminary, run_dir)
    raw_results = preliminary.get("results")
    if not isinstance(raw_results, list):
        raise ReviewError("preliminary results must contain a results list")

    finalized: list[dict[str, Any]] = []
    pending_ids: set[str] = set()
    known_ids: set[str] = set()
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise ReviewError("preliminary results contains a non-object case")
        result = dict(raw_result)
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReviewError("preliminary case result has no case_id")
        if case_id in known_ids:
            raise ReviewError(f"preliminary results contains duplicate case {case_id}")
        known_ids.add(case_id)
        if result.get("verdict") == "needs_semantic_review":
            pending_ids.add(case_id)
            review = reviews.get(case_id)
            if review is None:
                raise ReviewError(f"missing semantic review for {case_id}")
            verdict, additions = _validate_case_review(result, review, packets)
            result["deterministic_verdict"] = "needs_semantic_review"
            result["verdict"] = verdict
            result.update(additions)
        finalized.append(result)

    extra_reviews = sorted(set(reviews) - pending_ids)
    if extra_reviews:
        unknown = sorted(set(extra_reviews) - known_ids)
        detail = f"unknown={unknown}" if unknown else f"not_pending={extra_reviews}"
        raise ReviewError(f"semantic review contains cases that are not pending: {detail}")

    output = dict(preliminary)
    output["schema_version"] = 2
    output["preliminary_schema_version"] = preliminary.get("schema_version")
    output["reviewed_at"] = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    output["semantic_review_complete"] = True
    output["reviewed_case_count"] = len(pending_ids)
    output["results"] = finalized
    output["verdict_counts"] = dict(Counter(str(result.get("verdict")) for result in finalized))
    output["exit_code"] = exit_code_for_summary(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    reviews_path = args.reviews or (run_dir / "semantic_reviews.json")
    output_path = args.out or (run_dir / "reviewed-results.json")
    try:
        preliminary = _load_object(run_dir / "results.json")
        review_artifact = _load_object(reviews_path)
        reviewed = finalize_results(preliminary, review_artifact, run_dir=run_dir)
        write_immutable_json(output_path, reviewed)
    except (ReviewError, ImmutableManifestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION

    print(json.dumps(reviewed, indent=2, sort_keys=True))
    return int(reviewed["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
