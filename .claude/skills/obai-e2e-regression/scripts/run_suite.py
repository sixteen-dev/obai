#!/usr/bin/env python3
"""Plan or execute a cost-aware OBaI E2E regression suite.

Safety defaults:
  * no mode flag means dry-run (zero OBaI/provider calls),
  * the suite's default_tier is selected exactly (normally ``core``),
  * live/extended tiers require ``--allow-expensive``, and
  * immutable manifest/results files prevent stale checkpoint reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from judge_packet import JudgeResult, judge_packet
from lint_cases import CASE_ID_RE, LintIssue, lint_suite
from preflight import (
    CredentialConfigurationError,
    effective_openai_credential_identity,
    redact_sensitive_text,
)
from run_one import (
    _hash_tree,
    case_contract_fingerprint,
    runtime_environment_binding,
    runtime_source_paths,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CASES = SKILL_DIR / "cases" / "cases.yaml"
DEFAULT_RUN_ONE = SCRIPT_DIR / "run_one.py"
DEFAULT_PREFLIGHT = SCRIPT_DIR / "preflight.py"
DEFAULT_MAX_API_CALLS = 20
CASES_SNAPSHOT_NAME = "cases.snapshot.yaml"
EXIT_SUCCESS = 0
EXIT_PRODUCT_FAILURE = 1
EXIT_CONFIGURATION = 2
EXIT_INFRASTRUCTURE = 3
CHAIN_CONTINUATION_VERDICTS = frozenset({"pass", "pass_degraded", "needs_semantic_review"})
SKIPPED_VERDICT = "skipped_dependency"
# Harness statuses that mean the harness itself can no longer bind a query to
# its evidence. These stay suite-fatal on the first occurrence. Statuses scoped
# to a single case (cli_failed, async_followup_failed) are contained instead.
SUITE_FATAL_HARNESS_STATUSES = frozenset(
    {"session_mismatch", "trace_lookup_failed", "trace_evidence_failed"}
)
MAX_CONSECUTIVE_HARNESS_FAILURES = 2
MAX_HARNESS_FAILURES = 3


def _harness_abort_reason(
    *, subject: str, status: object, consecutive: int, total: int
) -> str | None:
    """Decide whether one case's harness failure must stop the paid run.

    An isolated per-case harness failure is already recorded as an inconclusive
    verdict, which floors the suite exit code at EXIT_INFRASTRUCTURE, so
    stopping the whole run adds no safety and forfeits every unrun case. Only
    statuses that invalidate the harness itself, or repeated failures, abort.
    Cost-accounting and fingerprint gates are checked separately and remain
    suite-fatal on their own.

    Args:
        subject: Human-readable subject for the message, e.g. "case C1" or
            "resumed case C1".
        status: The packet's harness_status value.
        consecutive: Harness failures in an unbroken run through this case.
        total: Harness failures seen so far in this run.

    Returns:
        The abort reason, or None to contain the failure and keep going.
    """
    # A missing status means there is no packet to judge, which is the same
    # untrustworthy-evidence condition as a fatal status.
    if status is None or status in SUITE_FATAL_HARNESS_STATUSES:
        return (
            f"{subject} ended with harness status {status!r}; "
            "refusing to spend on cases that cannot produce trustworthy evidence"
        )
    if consecutive >= MAX_CONSECUTIVE_HARNESS_FAILURES:
        return (
            f"{consecutive} consecutive harness failures through {subject} "
            f"(latest {status!r}); refusing additional paid cases"
        )
    if total > MAX_HARNESS_FAILURES:
        return (
            f"{total} harness failures in this run through {subject} "
            f"(latest {status!r}); refusing additional paid cases"
        )
    return None


class PlanError(ValueError):
    """The selected case graph cannot be planned safely."""


class ExpensivePlanError(PlanError):
    """The plan exceeds an explicit tier or between-case spending boundary."""


class ImmutableManifestError(FileExistsError):
    """An immutable run artifact already exists."""


@dataclass(frozen=True)
class SuitePlan:
    cases: list[dict[str, Any]]
    selected_tiers: tuple[str, ...]
    estimated_api_calls: int
    max_api_calls: int


def _tier(case: dict[str, Any]) -> str:
    value = case.get("tier") or case.get("test_tier")
    if isinstance(value, str):
        return value
    return "smoke" if case.get("smoke") is True else "extended"


def _case_api_calls(case: dict[str, Any]) -> int:
    value: object = case.get("estimated_api_calls")
    if value is None and isinstance(case.get("cost"), dict):
        value = case["cost"].get("estimated_api_calls")
    if value is None:
        value = 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlanError(f"case {case.get('id')!r} has invalid estimated_api_calls {value!r}")
    repeat = case.get("repeat", 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise PlanError(f"case {case.get('id')!r} has invalid repeat {repeat!r}")
    if repeat != 1:
        raise PlanError(
            f"case {case.get('id')!r} requests repeat={repeat}; this paid runner executes "
            "deduplicated cases exactly once"
        )
    return value


def _topological_order(
    selected_ids: set[str],
    cases_by_id: dict[str, dict[str, Any]],
    yaml_order: list[str],
) -> list[dict[str, Any]]:
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(case_id: str) -> None:
        if case_id in visited:
            return
        if case_id in visiting:
            raise PlanError(f"chain_from cycle includes {case_id!r}")
        visiting.add(case_id)
        parent = cases_by_id[case_id].get("chain_from")
        if isinstance(parent, str) and parent in selected_ids:
            visit(parent)
        visiting.remove(case_id)
        visited.add(case_id)
        ordered.append(cases_by_id[case_id])

    for case_id in yaml_order:
        if case_id in selected_ids:
            visit(case_id)
    return ordered


def choose_cases(
    cases: list[dict[str, Any]],
    *,
    tiers: set[str] | None = None,
    ids: set[str] | None = None,
    default_tier: str = "core",
    allow_expensive: bool = False,
    max_api_calls: int = DEFAULT_MAX_API_CALLS,
    suite_budgets: dict[str, Any] | None = None,
) -> SuitePlan:
    """Select an exact tier or ID set, close dependencies, and enforce cost ceilings."""
    enabled = [case for case in cases if not case.get("disabled")]
    cases_by_id: dict[str, dict[str, Any]] = {}
    yaml_order: list[str] = []
    for case in enabled:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise PlanError("every enabled case must have a non-empty id")
        if not CASE_ID_RE.fullmatch(case_id):
            raise PlanError(f"case id {case_id!r} contains unsafe path characters")
        if case_id in cases_by_id:
            raise PlanError(f"duplicate case id {case_id!r}")
        cases_by_id[case_id] = case
        yaml_order.append(case_id)

    if ids:
        missing = sorted(ids - cases_by_id.keys())
        if missing:
            raise PlanError(f"unknown case id(s): {', '.join(missing)}")
        selected_ids = set(ids)
    else:
        requested_tiers = set(tiers) if tiers else {default_tier}
        selected_ids = {case["id"] for case in enabled if _tier(case) in requested_tiers}
        # Legacy suites have no core tier; their smoke markers are the safest default.
        if not selected_ids and not tiers and default_tier == "core":
            selected_ids = {
                case["id"]
                for case in enabled
                if case.get("smoke") is True or _tier(case) == "smoke"
            }

    if not selected_ids:
        raise PlanError("selection contains no enabled cases")

    # Dependency closure is fail-closed: no chained child may start in a fresh session.
    frontier = list(selected_ids)
    while frontier:
        case_id = frontier.pop()
        parent = cases_by_id[case_id].get("chain_from")
        if parent is None:
            continue
        if not isinstance(parent, str) or parent not in cases_by_id:
            raise PlanError(f"case {case_id!r} has missing chain parent {parent!r}")
        if parent not in selected_ids:
            selected_ids.add(parent)
            frontier.append(parent)

    ordered = _topological_order(selected_ids, cases_by_id, yaml_order)
    selected_tiers = tuple(sorted({_tier(case) for case in ordered}))
    estimated = sum(_case_api_calls(case) for case in ordered)

    has_opt_in_tier = any(tier in {"extended", "live"} for tier in selected_tiers)
    if has_opt_in_tier and not allow_expensive:
        raise ExpensivePlanError(
            f"tier(s) {selected_tiers} require --allow-expensive because they use paid/live APIs"
        )
    if estimated > max_api_calls:
        raise ExpensivePlanError(
            f"plan estimates {estimated} billable model requests, over the between-case "
            f"limit {max_api_calls}; "
            "raise --max-api-calls explicitly"
        )

    budgets = suite_budgets if isinstance(suite_budgets, dict) else {}
    for tier in selected_tiers:
        raw_budget = budgets.get(tier)
        if not isinstance(raw_budget, dict):
            continue
        tier_cases = [case for case in ordered if _tier(case) == tier]
        max_cases = raw_budget.get("max_cases")
        max_calls = raw_budget.get("max_estimated_api_calls")
        if isinstance(max_cases, int) and not isinstance(max_cases, bool):
            if len(tier_cases) > max_cases:
                raise ExpensivePlanError(
                    f"{tier} selects {len(tier_cases)} cases, over suite budget {max_cases}"
                )
        elif max_cases is not None:
            raise PlanError(f"suite_budgets.{tier}.max_cases must be an integer")
        tier_calls = sum(_case_api_calls(case) for case in tier_cases)
        if isinstance(max_calls, int) and not isinstance(max_calls, bool):
            if tier_calls > max_calls:
                raise ExpensivePlanError(
                    f"{tier} estimates {tier_calls} model requests, over suite budget {max_calls}"
                )
        elif max_calls is not None:
            raise PlanError(f"suite_budgets.{tier}.max_estimated_api_calls must be an integer")

    return SuitePlan(ordered, selected_tiers, estimated, max_api_calls)


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def fingerprint_case(case: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(case)).hexdigest()


def _git_metadata(cwd: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    sha = run("rev-parse", "HEAD")
    dirty_output = run("status", "--porcelain")
    return {"sha": sha, "dirty": bool(dirty_output) if dirty_output is not None else None}


def _runtime_script_bindings(
    *, run_one_path: Path = DEFAULT_RUN_ONE, preflight_path: Path = DEFAULT_PREFLIGHT
) -> dict[str, dict[str, str]]:
    """Bind the exact helper programs selected for a paid run."""
    bindings: dict[str, dict[str, str]] = {}
    for label, path in (("run_one", run_one_path), ("preflight", preflight_path)):
        try:
            resolved = path.resolve(strict=True)
            payload = resolved.read_bytes()
        except OSError as exc:
            raise PlanError(f"cannot bind {label} helper {path}: {exc}") from exc
        if not resolved.is_file():
            raise PlanError(f"{label} helper is not a regular file: {resolved}")
        bindings[label] = {
            "path": str(resolved),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return bindings


def _runtime_fingerprint(
    *,
    run_one_path: Path = DEFAULT_RUN_ONE,
    preflight_path: Path = DEFAULT_PREFLIGHT,
    bind_credential: bool = True,
) -> str:
    """Hash prompt/runtime inputs and the effective key identity, never the key."""
    repo_root = SKILL_DIR.parents[2]
    helper_bindings = _runtime_script_bindings(
        run_one_path=run_one_path,
        preflight_path=preflight_path,
    )
    roots = [
        *runtime_source_paths(repo_root),
        Path(helper_bindings["run_one"]["path"]),
        Path(helper_bindings["preflight"]["path"]),
    ]
    digest = hashlib.sha256()
    digest.update(_hash_tree(roots, root=repo_root).encode("ascii"))
    digest.update(_canonical_json(runtime_environment_binding()))
    digest.update(_canonical_json(helper_bindings))
    if bind_credential:
        # Environment precedence is resolved by the same helper used in
        # preflight/run_one. The domain-separated digest is safe to fold into
        # this aggregate hash and is never serialized as a standalone secret.
        try:
            credential_identity = effective_openai_credential_identity()
        except CredentialConfigurationError as exc:
            raise PlanError(f"cannot bind the effective OpenAI credential: {exc}") from exc
        digest.update(_canonical_json(credential_identity))
    return digest.hexdigest()


def build_manifest(
    plan: SuitePlan,
    *,
    cases_path: Path,
    cases_bytes: bytes,
    mode: str = "dry_run",
    cases_snapshot_path: Path | None = None,
    run_one_path: Path = DEFAULT_RUN_ONE,
    preflight_path: Path = DEFAULT_PREFLIGHT,
    run_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    created_at = created_at or datetime.now(tz=UTC)
    if mode == "execute" and cases_snapshot_path is None:
        raise PlanError("execute manifest requires a run-bound cases snapshot")
    run_id = run_id or f"obai-e2e-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    calendar_anchor = created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    suite_fingerprint = hashlib.sha256(cases_bytes).hexdigest()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "cases_path": str(cases_path.resolve()),
        "suite_fingerprint": suite_fingerprint,
        "cases_snapshot_path": (
            str(cases_snapshot_path.resolve()) if cases_snapshot_path is not None else None
        ),
        "cases_snapshot_sha256": (suite_fingerprint if cases_snapshot_path is not None else None),
        "calendar_anchor": calendar_anchor,
        "selected_tiers": list(plan.selected_tiers),
        "planned_count": len(plan.cases),
        "estimated_api_calls": plan.estimated_api_calls,
        "estimated_model_requests": plan.estimated_api_calls,
        "between_case_model_request_limit": plan.max_api_calls,
        "hard_model_request_cap_enforced": False,
        "git": _git_metadata(Path.cwd()),
        "runtime_helpers": _runtime_script_bindings(
            run_one_path=run_one_path,
            preflight_path=preflight_path,
        ),
        "runtime_fingerprint": _runtime_fingerprint(
            run_one_path=run_one_path,
            preflight_path=preflight_path,
            bind_credential=mode == "execute",
        ),
        "cases": [
            {
                "id": case["id"],
                "tier": _tier(case),
                "estimated_api_calls": _case_api_calls(case),
                "fingerprint": fingerprint_case(case),
                "snapshot": case,
            }
            for case in plan.cases
        ],
    }


def validate_resume_manifest(
    manifest: dict[str, Any],
    plan: SuitePlan,
    *,
    cases_bytes: bytes,
    run_dir: Path | None = None,
    run_one_path: Path = DEFAULT_RUN_ONE,
    preflight_path: Path = DEFAULT_PREFLIGHT,
) -> None:
    """Fail closed unless an interrupted run is byte-for-byte the same plan."""
    expected_suite = hashlib.sha256(cases_bytes).hexdigest()
    if manifest.get("schema_version") != 1:
        raise PlanError("resume manifest schema_version is not supported")
    if manifest.get("mode") != "execute":
        raise PlanError("only an execute manifest can be resumed")
    if manifest.get("suite_fingerprint") != expected_suite:
        raise PlanError("cases YAML changed since the run manifest was created")
    if manifest.get("selected_tiers") != list(plan.selected_tiers):
        raise PlanError("selected tiers differ from the run manifest")
    if manifest.get("planned_count") != len(plan.cases):
        raise PlanError("planned case count differs from the run manifest")
    if manifest.get("estimated_api_calls") != plan.estimated_api_calls:
        raise PlanError("estimated model-request cost differs from the run manifest")
    if manifest.get("between_case_model_request_limit") != plan.max_api_calls:
        raise PlanError("between-case model-request limit differs from the run manifest")
    expected_helpers = _runtime_script_bindings(
        run_one_path=run_one_path,
        preflight_path=preflight_path,
    )
    if manifest.get("runtime_helpers") != expected_helpers:
        raise PlanError("run_one or preflight helper path/content changed since the manifest")
    if manifest.get("runtime_fingerprint") != _runtime_fingerprint(
        run_one_path=run_one_path,
        preflight_path=preflight_path,
    ):
        raise PlanError("prompt, runtime, or model configuration changed since the manifest")
    _parse_calendar_anchor(manifest.get("calendar_anchor"))
    if run_dir is None:
        snapshot_path = manifest.get("cases_snapshot_path")
        if not isinstance(snapshot_path, str):
            raise PlanError("resume manifest has no executable cases snapshot")
        run_dir = Path(snapshot_path).resolve().parent
    _validate_cases_snapshot(manifest, run_dir)

    freshness_limits: list[int] = []
    for case in plan.cases:
        if case.get("date_policy") not in {"live", "relative"}:
            continue
        freshness = case.get("freshness") if isinstance(case.get("freshness"), dict) else {}
        max_age = case.get("max_age_seconds", freshness.get("max_age_seconds"))
        if isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0:
            freshness_limits.append(max_age)
    if freshness_limits:
        created_at = manifest.get("created_at")
        try:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PlanError("resume manifest created_at is invalid") from exc
        age = (datetime.now(tz=UTC) - created.astimezone(UTC)).total_seconds()
        if age > min(freshness_limits):
            raise PlanError(
                "resume manifest is older than the selected cases' shortest freshness SLA; "
                "start a new run"
            )

    recorded = manifest.get("cases")
    if not isinstance(recorded, list):
        raise PlanError("resume manifest has no case snapshots")
    recorded_pairs = [
        (item.get("id"), item.get("fingerprint")) for item in recorded if isinstance(item, dict)
    ]
    expected_pairs = [(case["id"], fingerprint_case(case)) for case in plan.cases]
    if recorded_pairs != expected_pairs:
        raise PlanError("case order or fingerprints differ from the run manifest")


def _parse_calendar_anchor(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PlanError("resume manifest has no suite calendar anchor")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanError("resume manifest calendar anchor is invalid") from exc
    if parsed.tzinfo is None:
        raise PlanError("resume manifest calendar anchor has no timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_cases_snapshot(manifest: dict[str, Any], run_dir: Path) -> Path:
    """Validate the exact executable suite snapshot bound to an execute manifest."""
    expected_path = (run_dir / CASES_SNAPSHOT_NAME).resolve()
    recorded_path = manifest.get("cases_snapshot_path")
    if not isinstance(recorded_path, str) or Path(recorded_path).resolve() != expected_path:
        raise PlanError("execute manifest cases snapshot path is missing or outside the run")
    recorded_sha = manifest.get("cases_snapshot_sha256")
    if not isinstance(recorded_sha, str) or recorded_sha != manifest.get("suite_fingerprint"):
        raise PlanError("execute manifest cases snapshot SHA-256 is invalid")
    try:
        snapshot_bytes = expected_path.read_bytes()
    except OSError as exc:
        raise PlanError(f"cannot read executable cases snapshot: {exc}") from exc
    if hashlib.sha256(snapshot_bytes).hexdigest() != recorded_sha:
        raise PlanError("executable cases snapshot SHA-256 mismatch")
    return expected_path


def _fsync_directory(path: Path) -> None:
    """Persist a just-published directory entry before paid work can start."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory_durable(path: Path) -> None:
    """Create a directory tree and persist every newly published entry."""
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:  # pragma: no cover - a filesystem root exists
            break
        cursor = parent

    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        # Persist the directory inode and, critically, its name in the parent.
        _fsync_directory(directory)
        _fsync_directory(directory.parent)


def write_immutable_bytes(path: Path, payload: bytes, *, read_only: bool = False) -> None:
    """Atomically publish bytes without replacing an existing run artifact."""
    _ensure_directory_durable(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ImmutableManifestError(f"refusing to overwrite {path}") from exc
        if read_only:
            path.chmod(0o444)
            # chmod changes inode metadata after the temporary file was
            # flushed, so persist the final mode as part of publication.
            with path.open("rb") as published:
                os.fsync(published.fileno())
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write_immutable_json(path: Path, payload: object) -> None:
    """Atomically publish JSON without replacing an existing run artifact."""
    encoded = (json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n").encode(
        "utf-8"
    )
    write_immutable_bytes(path, encoded)


def exit_code_for_summary(summary: dict[str, Any]) -> int:
    counts = summary.get("verdict_counts", {})
    if not isinstance(counts, dict):
        return EXIT_INFRASTRUCTURE
    if any(counts.get(verdict, 0) for verdict in ("fail_product", "needs_semantic_review")):
        return EXIT_PRODUCT_FAILURE
    if not summary.get("complete"):
        return EXIT_INFRASTRUCTURE
    if any(str(verdict).startswith("inconclusive_") and count for verdict, count in counts.items()):
        return EXIT_INFRASTRUCTURE
    return EXIT_SUCCESS


def _result_from_runner_failure(
    case: dict[str, Any],
    packet: dict[str, Any] | None,
    returncode: int,
    stderr: str,
) -> dict[str, Any]:
    if packet is not None:
        judged = judge_packet(case, packet)
        result = judged.to_dict()
    else:
        result = JudgeResult(
            case_id=str(case["id"]),
            verdict="inconclusive_harness",
            expected_outcome=str(case.get("expected_outcome", "success")),
            observed_outcome="unknown",
            reason="run_one produced no packet",
        ).to_dict()
    result["verdict"] = "inconclusive_harness"
    result["reason"] = f"run_one exited {returncode}; partial packet evidence preserved"
    result["runner_exit_code"] = returncode
    result["runner_stderr"] = redact_sensitive_text(stderr)[-2000:]
    return result


def _read_packet_bytes(packet_path: Path) -> tuple[dict[str, Any], bytes]:
    """Read one packet once so its parsed content and digest bind the same bytes."""
    try:
        packet_bytes = packet_path.read_bytes()
        packet = json.loads(packet_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read packet {packet_path}: {exc}") from exc
    if not isinstance(packet, dict):
        raise PlanError(f"packet {packet_path} is not an object")
    return packet, packet_bytes


def _validated_packet_path(*, case_id: str, run_dir: Path, packet_path: object) -> Path:
    """Require the exact per-case packet path and keep it inside the run directory."""
    if not isinstance(packet_path, str) or not packet_path:
        raise PlanError(f"judgment for {case_id} has no packet path")
    expected = (run_dir / f"{case_id}.json").resolve()
    resolved = Path(packet_path).resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise PlanError(f"judgment packet for {case_id} escapes the run directory") from exc
    if resolved != expected:
        raise PlanError(f"judgment packet path mismatch for {case_id}")
    if not resolved.is_file():
        raise PlanError(f"resume judgment has no preserved packet for {case_id}")
    return resolved


def _validated_claim_sha256(
    *, run_dir: Path, case_id: str, expected_execution_binding: dict[str, Any]
) -> str:
    """Require run_one's atomic nonce-consumption claim for an executed case."""
    expected_path = (run_dir / "claims" / f"{case_id}.json").resolve()
    try:
        expected_path.relative_to(run_dir.resolve())
        claim_bytes = expected_path.read_bytes()
        claim = json.loads(claim_bytes)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PlanError(f"paid execution claim is missing or invalid for {case_id}: {exc}") from exc
    if claim != expected_execution_binding:
        raise PlanError(f"paid execution claim binding mismatch for {case_id}")
    return hashlib.sha256(claim_bytes).hexdigest()


def _packet_binding(
    case: dict[str, Any],
    *,
    run_id: str,
    packet_path: Path,
    packet: dict[str, Any],
    packet_bytes: bytes,
    expected_execution_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the provenance fields that make a judgment packet-specific."""
    case_id = str(case["id"])
    if packet.get("id") != case_id:
        raise PlanError(f"packet id mismatch for {case_id}")
    expected_packet_case_fingerprint = case_contract_fingerprint(case)
    if packet.get("case_fingerprint") != expected_packet_case_fingerprint:
        raise PlanError(f"packet case fingerprint mismatch for {case_id}")
    if expected_execution_binding is not None:
        if packet.get("execution_binding") != expected_execution_binding:
            raise PlanError(f"packet execution binding mismatch for {case_id}")
        top_level_binding = {
            "run_id": "run_id",
            "attempt_nonce": "attempt_nonce",
            "manifest_sha256": "manifest_sha256",
            "cases_snapshot_sha256": "cases_snapshot_sha256",
            "attempt_marker_sha256": "attempt_marker_sha256",
        }
        for packet_key, binding_key in top_level_binding.items():
            if packet.get(packet_key) != expected_execution_binding.get(binding_key):
                raise PlanError(f"packet {packet_key} binding mismatch for {case_id}")
        claim_sha256 = _validated_claim_sha256(
            run_dir=packet_path.parent,
            case_id=case_id,
            expected_execution_binding=expected_execution_binding,
        )
    packet_input_fingerprint = packet.get("input_fingerprint")
    if expected_execution_binding is not None and (
        not isinstance(packet_input_fingerprint, str) or not packet_input_fingerprint
    ):
        raise PlanError(f"bound packet has no input fingerprint for {case_id}")
    if packet_input_fingerprint is not None and (
        not isinstance(packet_input_fingerprint, str) or not packet_input_fingerprint
    ):
        raise PlanError(f"packet input fingerprint is invalid for {case_id}")
    provenance = {
        "run_id": run_id,
        "packet_path": str(packet_path.resolve()),
        "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "packet_id": case_id,
        "packet_case_fingerprint": expected_packet_case_fingerprint,
        "packet_input_fingerprint": packet_input_fingerprint,
        "case_fingerprint": fingerprint_case(case),
    }
    if expected_execution_binding is not None:
        provenance["packet_execution_binding"] = expected_execution_binding
        provenance["attempt_nonce"] = expected_execution_binding["attempt_nonce"]
        provenance["manifest_sha256"] = expected_execution_binding["manifest_sha256"]
        provenance["cases_snapshot_sha256"] = expected_execution_binding["cases_snapshot_sha256"]
        provenance["attempt_marker_sha256"] = expected_execution_binding["attempt_marker_sha256"]
        provenance["execution_claim_sha256"] = claim_sha256
    return provenance


def _judgment_from_packet(
    case: dict[str, Any],
    *,
    run_id: str,
    packet_path: Path,
    packet: dict[str, Any],
    packet_bytes: bytes,
    runner_exit_code: int = 0,
    runner_stderr: str = "",
    recovered_from_attempt: bool = False,
    expected_execution_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge a packet and attach immutable provenance and recomputed usage."""
    if runner_exit_code:
        result = _result_from_runner_failure(case, packet, runner_exit_code, runner_stderr)
    else:
        result = judge_packet(case, packet).to_dict()
        result["runner_exit_code"] = 0
    result.update(
        _packet_binding(
            case,
            run_id=run_id,
            packet_path=packet_path,
            packet=packet,
            packet_bytes=packet_bytes,
            expected_execution_binding=expected_execution_binding,
        )
    )
    result.update(_packet_result_metadata(case, packet))
    if recovered_from_attempt:
        result["recovered_from_attempt"] = True
    return result


def _load_resume_judgment(
    case: dict[str, Any],
    judgment_path: Path,
    *,
    run_id: str,
    run_dir: Path,
    expected_skip: tuple[str, str] | None = None,
    expected_execution_binding: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    if not judgment_path.exists():
        return None
    try:
        result = json.loads(judgment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot resume invalid judgment {judgment_path}: {exc}") from exc
    if not isinstance(result, dict):
        raise PlanError(f"resume judgment {judgment_path} is not an object")
    if result.get("run_id") != run_id:
        raise PlanError(f"resume judgment run_id mismatch for {case.get('id')}")
    if result.get("case_id") != case.get("id"):
        raise PlanError(f"resume judgment id mismatch in {judgment_path}")
    if result.get("case_fingerprint") != fingerprint_case(case):
        raise PlanError(f"resume judgment fingerprint mismatch for {case.get('id')}")
    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in {
        "pass",
        "pass_degraded",
        "fail_product",
        "needs_semantic_review",
        "inconclusive_provider",
        "inconclusive_harness",
        "inconclusive_missing_evidence",
        SKIPPED_VERDICT,
    }:
        raise PlanError(f"resume judgment has invalid verdict for {case.get('id')}")
    if verdict == SKIPPED_VERDICT:
        if expected_skip is None:
            raise PlanError(
                f"resume judgment for {case.get('id')} is skipped but its current "
                "dependency conditions permit execution"
            )
        parent, reason = expected_skip
        deterministic_skip = _skipped_dependency_result(
            case, run_id=run_id, parent=parent, reason=reason
        )
        if result != deterministic_skip:
            raise PlanError(f"resume skipped judgment is stale for {case.get('id')}")
        return result, None
    if expected_skip is not None:
        raise PlanError(
            f"resume judgment for {case.get('id')} executed despite its current "
            "dependency conditions"
        )

    case_id = str(case["id"])
    resolved_packet = _validated_packet_path(
        case_id=case_id, run_dir=run_dir, packet_path=result.get("packet_path")
    )
    packet, packet_bytes = _read_packet_bytes(resolved_packet)
    binding = _packet_binding(
        case,
        run_id=run_id,
        packet_path=resolved_packet,
        packet=packet,
        packet_bytes=packet_bytes,
        expected_execution_binding=expected_execution_binding,
    )
    if any(result.get(key) != value for key, value in binding.items()):
        raise PlanError(f"resume judgment packet binding mismatch for {case_id}")

    runner_exit_code = result.get("runner_exit_code")
    if isinstance(runner_exit_code, bool) or not isinstance(runner_exit_code, int):
        raise PlanError(f"resume judgment has invalid runner exit code for {case_id}")
    runner_stderr = result.get("runner_stderr", "")
    if not isinstance(runner_stderr, str):
        raise PlanError(f"resume judgment has invalid runner stderr for {case_id}")
    if runner_exit_code:
        deterministic = _result_from_runner_failure(case, packet, runner_exit_code, runner_stderr)
    else:
        deterministic = judge_packet(case, packet).to_dict()
        deterministic["runner_exit_code"] = 0
    if any(result.get(key) != value for key, value in deterministic.items()):
        raise PlanError(f"resume deterministic judgment mismatch for {case_id}")

    packet_metadata = _packet_result_metadata(case, packet)
    if any(result.get(key) != value for key, value in packet_metadata.items()):
        raise PlanError(f"resume packet metadata mismatch for {case_id}")
    recovered = result.get("recovered_from_attempt") is True
    if "recovered_from_attempt" in result and not recovered:
        raise PlanError(f"resume recovery marker is invalid for {case_id}")
    exact_result = _judgment_from_packet(
        case,
        run_id=run_id,
        packet_path=resolved_packet,
        packet=packet,
        packet_bytes=packet_bytes,
        runner_exit_code=runner_exit_code,
        runner_stderr=runner_stderr,
        recovered_from_attempt=recovered,
        expected_execution_binding=expected_execution_binding,
    )
    if result != exact_result:
        raise PlanError(f"resume judgment contains stale or unbound fields for {case_id}")
    return result, packet


def observed_model_requests(packet: dict[str, Any]) -> int | None:
    """Count unique billable model spans across the initial and async traces.

    Opik's OpenAI Agents integration records every generation/response request
    as a span whose type is ``llm``. Missing span bundles or unstable IDs make
    runtime cost accounting unavailable, so callers can fail closed.
    """
    traces: list[dict[str, Any]] = []
    primary = packet.get("trace")
    if not isinstance(primary, dict) or not isinstance(primary.get("spans"), list):
        return None
    traces.append(primary)

    followup = packet.get("followup")
    if isinstance(followup, dict):
        polls = followup.get("polls")
        if polls is not None and not isinstance(polls, list):
            return None
        for poll in polls or []:
            if not isinstance(poll, dict):
                return None
            trace = poll.get("trace")
            if not isinstance(trace, dict) or not isinstance(trace.get("spans"), list):
                return None
            traces.append(trace)

    seen: set[tuple[str, str]] = set()
    count = 0
    for trace in traces:
        trace_id = trace.get("id")
        if not isinstance(trace_id, str) or not trace_id:
            return None
        for span in trace["spans"]:
            if not isinstance(span, dict):
                return None
            span_id = span.get("id")
            span_type = span.get("type")
            if not isinstance(span_id, str) or not span_id or not isinstance(span_type, str):
                return None
            identity = (trace_id, span_id)
            if identity in seen:
                continue
            seen.add(identity)
            if span_type.lower() == "llm":
                count += 1
    # Every accepted CLI turn invokes at least the model guardrail. A trace
    # with zero LLM spans is an eventually-consistent or broken snapshot, not
    # evidence that a paid request cost zero.
    return count if count > 0 else None


def _packet_result_metadata(case: dict[str, Any], packet: dict[str, Any] | None) -> dict[str, Any]:
    """Expose stable packet metadata needed by reports and cost accounting."""
    metadata: dict[str, Any] = {"feature": case.get("feature", "")}
    if packet is None:
        metadata["observed_model_requests"] = None
        return metadata
    metadata["latency_ms"] = packet.get("latency_ms")
    trace = packet.get("trace")
    metadata["trace_id"] = trace.get("id") if isinstance(trace, dict) else None
    followup = packet.get("followup")
    if isinstance(followup, dict):
        metadata["followup_wait_seconds"] = followup.get("wait_seconds")
        metadata["followup_latency_ms"] = followup.get("latency_ms")
        followup_trace = followup.get("trace")
        metadata["followup_trace_id"] = (
            followup_trace.get("id") if isinstance(followup_trace, dict) else None
        )
    metadata["observed_model_requests"] = observed_model_requests(packet)
    return metadata


def _skipped_dependency_result(
    case: dict[str, Any], *, run_id: str, parent: str, reason: str
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "case_id": str(case["id"]),
        "verdict": SKIPPED_VERDICT,
        "expected_outcome": str(case.get("expected_outcome", "success")),
        "observed_outcome": "not_run",
        "checks_passed": [],
        "checks_failed": [],
        "missing_evidence": [],
        "observed_tools": [],
        "observed_skills": [],
        "unexecuted_assertions": [],
        "reason": reason,
        "dependency_parent": parent,
        "runner_exit_code": None,
        "packet_path": None,
        "case_fingerprint": fingerprint_case(case),
        "feature": case.get("feature", ""),
        "observed_model_requests": 0,
    }


def _dependency_skip(
    case: dict[str, Any],
    *,
    verdict_by_id: dict[str, str],
    observed_outcome_by_id: dict[str, str],
) -> tuple[str, str] | None:
    """Return the current deterministic dependency skip decision, if any."""
    parent = case.get("chain_from")
    if not isinstance(parent, str):
        return None
    if verdict_by_id.get(parent) not in CHAIN_CONTINUATION_VERDICTS:
        return parent, f"dependency {parent} did not complete successfully"
    required_parent_outcomes = case.get("chain_requires_parent_outcomes")
    if isinstance(required_parent_outcomes, list) and (
        observed_outcome_by_id.get(parent) not in required_parent_outcomes
    ):
        reason = (
            f"dependency {parent} outcome {observed_outcome_by_id.get(parent)!r} "
            f"is outside {required_parent_outcomes!r}"
        )
        return parent, reason
    return None


def _attempt_payload(
    case: dict[str, Any],
    *,
    run_id: str,
    attempt_nonce: str,
    manifest_sha256: str,
    cases_snapshot_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "run_id": run_id,
        "case_id": str(case["id"]),
        "case_fingerprint": fingerprint_case(case),
        "attempt_nonce": attempt_nonce,
        "manifest_sha256": manifest_sha256,
        "cases_snapshot_sha256": cases_snapshot_sha256,
    }


def _attempt_execution_binding(payload: dict[str, Any], *, attempt_bytes: bytes) -> dict[str, Any]:
    """Return the exact packet provenance run_one must echo."""
    return {
        "schema_version": 1,
        "run_id": payload["run_id"],
        "case_id": payload["case_id"],
        "case_fingerprint": payload["case_fingerprint"],
        "attempt_nonce": payload["attempt_nonce"],
        "attempt_marker_sha256": hashlib.sha256(attempt_bytes).hexdigest(),
        "manifest_sha256": payload["manifest_sha256"],
        "cases_snapshot_sha256": payload["cases_snapshot_sha256"],
    }


def _load_attempt(
    case: dict[str, Any],
    attempt_path: Path,
    *,
    run_id: str,
    manifest_sha256: str,
    cases_snapshot_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Validate an immutable pre-call marker and return payload plus packet binding."""
    if not attempt_path.exists():
        return None
    try:
        attempt_bytes = attempt_path.read_bytes()
        payload = json.loads(attempt_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot resume invalid attempt {attempt_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanError(f"resume attempt is not an object for {case.get('id')}")
    nonce = payload.get("attempt_nonce")
    nonce_valid = (
        isinstance(nonce, str)
        and 32 <= len(nonce) <= 128
        and all(character in "0123456789abcdef" for character in nonce)
    )
    if not nonce_valid:
        raise PlanError(f"resume attempt nonce is invalid for {case.get('id')}")
    expected = _attempt_payload(
        case,
        run_id=run_id,
        attempt_nonce=nonce,
        manifest_sha256=manifest_sha256,
        cases_snapshot_sha256=cases_snapshot_sha256,
    )
    if payload != expected:
        raise PlanError(f"resume attempt binding mismatch for {case.get('id')}")
    return payload, _attempt_execution_binding(payload, attempt_bytes=attempt_bytes)


def _execute_plan(
    plan: SuitePlan,
    *,
    run_id: str,
    cases_path: Path,
    run_dir: Path,
    run_one_path: Path,
    cases_sha256: str | None = None,
    manifest_sha256: str | None = None,
    preflight_path: Path = DEFAULT_PREFLIGHT,
    expected_runtime_fingerprint: str | None = None,
    calendar_anchor: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    judgments_dir = run_dir / "judgments"
    _ensure_directory_durable(judgments_dir)
    attempts_dir = run_dir / "attempts"
    _ensure_directory_durable(attempts_dir)
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    verdict_by_id: dict[str, str] = {}
    observed_outcome_by_id: dict[str, str] = {}
    attempted = 0
    resumed = 0
    observed_request_total = 0
    abort_reason: str | None = None
    model_request_accounting_complete = True
    harness_failures: list[dict[str, Any]] = []
    consecutive_harness_failures = 0
    effective_cases_sha256 = cases_sha256 or ("0" * 64)
    effective_manifest_sha256 = manifest_sha256 or ("0" * 64)

    if manifest_sha256 is not None:
        try:
            actual_manifest_sha256 = hashlib.sha256(
                (run_dir / "manifest.json").read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise PlanError(f"cannot read execute manifest before paid execution: {exc}") from exc
        if actual_manifest_sha256 != manifest_sha256:
            raise PlanError("execute manifest SHA-256 changed before paid execution")

    for case in plan.cases:
        case_id = str(case["id"])
        judgment_path = judgments_dir / f"{case_id}.json"
        attempt_path = attempts_dir / f"{case_id}.json"
        packet_path = run_dir / f"{case_id}.json"
        skip_decision = _dependency_skip(
            case,
            verdict_by_id=verdict_by_id,
            observed_outcome_by_id=observed_outcome_by_id,
        )
        loaded_attempt = (
            _load_attempt(
                case,
                attempt_path,
                run_id=run_id,
                manifest_sha256=effective_manifest_sha256,
                cases_snapshot_sha256=effective_cases_sha256,
            )
            if resume and attempt_path.exists()
            else None
        )

        if resume and judgment_path.exists():
            loaded = _load_resume_judgment(
                case,
                judgment_path,
                run_id=run_id,
                run_dir=run_dir,
                expected_skip=skip_decision,
                expected_execution_binding=(loaded_attempt[1] if loaded_attempt else None),
            )
            if loaded is not None:
                prior_result, prior_packet = loaded
                if prior_result["verdict"] != SKIPPED_VERDICT and loaded_attempt is None:
                    raise PlanError(f"resume judgment has no attempt marker for {case_id}")
                if prior_result["verdict"] == SKIPPED_VERDICT and loaded_attempt is not None:
                    raise PlanError(f"resume skipped judgment has an attempt marker for {case_id}")
                results.append(prior_result)
                verdict_by_id[case_id] = str(prior_result["verdict"])
                observed_outcome_by_id[case_id] = str(
                    prior_result.get("observed_outcome", "unknown")
                )
                if prior_result["verdict"] == SKIPPED_VERDICT:
                    skipped.append(
                        {
                            "id": case_id,
                            "reason": str(prior_result.get("reason", "")),
                        }
                    )
                prior_requests = prior_result.get("observed_model_requests")
                if prior_result["verdict"] != SKIPPED_VERDICT:
                    if (
                        isinstance(prior_requests, bool)
                        or not isinstance(prior_requests, int)
                        or prior_requests < 0
                    ):
                        model_request_accounting_complete = False
                        abort_reason = (
                            f"resumed case {case_id} has no trustworthy observed model-request "
                            "count; refusing additional paid cases"
                        )
                        break
                    observed_request_total += prior_requests
                    resumed += 1
                    prior_status = (
                        prior_packet.get("harness_status") if prior_packet else None
                    )
                    # Mirror the live containment policy: a resumed run must not
                    # re-abort on an isolated failure the original run contained.
                    if prior_status == "completed":
                        consecutive_harness_failures = 0
                    else:
                        harness_failures.append(
                            {"case_id": case_id, "harness_status": prior_status}
                        )
                        consecutive_harness_failures += 1
                        abort_reason = _harness_abort_reason(
                            subject=f"resumed case {case_id}",
                            status=prior_status,
                            consecutive=consecutive_harness_failures,
                            total=len(harness_failures),
                        )
                        if abort_reason:
                            break
                    if observed_request_total > plan.max_api_calls:
                        abort_reason = (
                            f"resumed observed model requests reached {observed_request_total}, "
                            f"exceeding between-case limit {plan.max_api_calls}; refusing "
                            "additional paid cases"
                        )
                        break
                continue

        if skip_decision is not None:
            parent, reason = skip_decision
            skipped.append({"id": case_id, "reason": reason})
            result = _skipped_dependency_result(case, run_id=run_id, parent=parent, reason=reason)
            results.append(result)
            verdict_by_id[case_id] = SKIPPED_VERDICT
            observed_outcome_by_id[case_id] = "not_run"
            write_immutable_json(judgment_path, result)
            continue

        if resume and loaded_attempt is not None:
            # A paid call may already have happened. Never launch it again. A complete,
            # attributable packet can be judged locally; every other state fails closed.
            try:
                resolved_packet = _validated_packet_path(
                    case_id=case_id,
                    run_dir=run_dir,
                    packet_path=str(packet_path.resolve()),
                )
                packet, packet_bytes = _read_packet_bytes(resolved_packet)
                if packet.get("harness_status") != "completed":
                    raise PlanError(
                        f"recovery packet harness status is {packet.get('harness_status')!r}"
                    )
                result = _judgment_from_packet(
                    case,
                    run_id=run_id,
                    packet_path=resolved_packet,
                    packet=packet,
                    packet_bytes=packet_bytes,
                    recovered_from_attempt=True,
                    expected_execution_binding=loaded_attempt[1],
                )
                recovered_requests = result.get("observed_model_requests")
                if (
                    isinstance(recovered_requests, bool)
                    or not isinstance(recovered_requests, int)
                    or recovered_requests < 0
                ):
                    raise PlanError("recovery packet has no trustworthy model-request count")
            except PlanError as exc:
                abort_reason = (
                    f"case {case_id} has a prior paid attempt but no valid completed packet "
                    f"({exc}); refusing to rerun it"
                )
                break
            write_immutable_json(judgment_path, result)
            results.append(result)
            verdict_by_id[case_id] = str(result["verdict"])
            observed_outcome_by_id[case_id] = str(result.get("observed_outcome", "unknown"))
            observed_request_total += recovered_requests
            resumed += 1
            if observed_request_total > plan.max_api_calls:
                abort_reason = (
                    f"recovered observed model requests reached {observed_request_total}, "
                    f"exceeding between-case limit {plan.max_api_calls}; refusing additional "
                    "paid cases"
                )
                break
            continue

        if resume and packet_path.exists():
            abort_reason = (
                f"case {case_id} has a packet without a matching pre-call attempt marker; "
                "refusing to execute or reuse it"
            )
            break

        if not resume and (judgment_path.exists() or attempt_path.exists() or packet_path.exists()):
            abort_reason = (
                f"case {case_id} already has run artifacts; refusing to risk duplicate spend"
            )
            break

        if manifest_sha256 is not None:
            try:
                current_manifest_sha256 = hashlib.sha256(
                    (run_dir / "manifest.json").read_bytes()
                ).hexdigest()
            except OSError as exc:
                abort_reason = f"cannot read execute manifest before {case_id}: {exc}"
                break
            if current_manifest_sha256 != manifest_sha256:
                abort_reason = f"execute manifest changed before {case_id}; refusing paid execution"
                break

        if expected_runtime_fingerprint is not None:
            try:
                current_runtime_fingerprint = _runtime_fingerprint(
                    run_one_path=run_one_path,
                    preflight_path=preflight_path,
                )
            except PlanError as exc:
                abort_reason = f"cannot validate runtime before {case_id}: {exc}"
                break
            if current_runtime_fingerprint != expected_runtime_fingerprint:
                abort_reason = (
                    f"runtime, helper, model configuration, or effective credential changed "
                    f"before {case_id}; refusing paid execution"
                )
                break

        if cases_sha256 is not None:
            try:
                executable_cases_sha = hashlib.sha256(cases_path.read_bytes()).hexdigest()
            except OSError as exc:
                abort_reason = f"cannot read executable cases snapshot before {case_id}: {exc}"
                break
            if executable_cases_sha != cases_sha256:
                abort_reason = (
                    f"executable cases snapshot changed before {case_id}; refusing paid execution"
                )
                break

        next_estimate = _case_api_calls(case)
        if observed_request_total + next_estimate > plan.max_api_calls:
            abort_reason = (
                f"observed {observed_request_total} model requests; case {case_id} could add "
                f"an estimated {next_estimate}, exceeding between-case limit {plan.max_api_calls}"
            )
            break

        attempt_nonce = secrets.token_hex(32)
        attempt_payload = _attempt_payload(
            case,
            run_id=run_id,
            attempt_nonce=attempt_nonce,
            manifest_sha256=effective_manifest_sha256,
            cases_snapshot_sha256=effective_cases_sha256,
        )
        try:
            write_immutable_json(attempt_path, attempt_payload)
        except ImmutableManifestError:
            abort_reason = (
                f"case {case_id} already has an attempt marker; refusing to risk duplicate spend"
            )
            break
        loaded_new_attempt = _load_attempt(
            case,
            attempt_path,
            run_id=run_id,
            manifest_sha256=effective_manifest_sha256,
            cases_snapshot_sha256=effective_cases_sha256,
        )
        if loaded_new_attempt is None:  # pragma: no cover - immutable write just succeeded
            raise PlanError(f"attempt marker vanished before paid execution for {case_id}")
        execution_binding = loaded_new_attempt[1]
        attempted += 1
        command = [
            sys.executable,
            str(run_one_path),
            "--id",
            case_id,
            "--run-dir",
            str(run_dir),
            "--cases",
            str(cases_path),
            "--cases-sha256",
            effective_cases_sha256,
            "--run-id",
            run_id,
            "--attempt-nonce",
            attempt_nonce,
            "--manifest-sha256",
            effective_manifest_sha256,
        ]
        if not case.get("chain_from") and calendar_anchor is not None:
            command.extend(["--calendar-anchor", calendar_anchor])
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        packet: dict[str, Any] | None = None
        packet_bytes: bytes | None = None
        if packet_path.exists():
            try:
                packet, packet_bytes = _read_packet_bytes(packet_path)
            except PlanError:
                packet = None

        if packet is None or packet_bytes is None:
            result = _result_from_runner_failure(
                case, None, completed.returncode, "packet missing or invalid"
            )
            result["run_id"] = run_id
            result["case_fingerprint"] = fingerprint_case(case)
            result.update(_packet_result_metadata(case, None))
        else:
            try:
                result = _judgment_from_packet(
                    case,
                    run_id=run_id,
                    packet_path=packet_path,
                    packet=packet,
                    packet_bytes=packet_bytes,
                    runner_exit_code=completed.returncode,
                    runner_stderr=completed.stderr,
                    expected_execution_binding=execution_binding,
                )
            except PlanError as exc:
                result = _result_from_runner_failure(case, None, completed.returncode, str(exc))
                result["run_id"] = run_id
                result["case_fingerprint"] = fingerprint_case(case)
                result.update(_packet_result_metadata(case, None))
        results.append(result)
        verdict_by_id[case_id] = str(result["verdict"])
        observed_outcome_by_id[case_id] = str(result.get("observed_outcome", "unknown"))
        # A packetless result is a run summary, not a judgment. Leaving only the
        # pre-call attempt marker makes a future resume fail closed without paying twice.
        if packet is not None and packet_bytes is not None and "packet_sha256" in result:
            write_immutable_json(judgment_path, result)

        observed_requests = result.get("observed_model_requests")
        if (
            isinstance(observed_requests, bool)
            or not isinstance(observed_requests, int)
            or observed_requests < 0
        ):
            model_request_accounting_complete = False
            abort_reason = (
                f"case {case_id} has no trustworthy observed model-request count; "
                "refusing additional paid cases"
            )
            break
        observed_request_total += observed_requests
        packet_harness_status = packet.get("harness_status") if packet else None
        if packet_harness_status == "completed":
            consecutive_harness_failures = 0
        else:
            harness_failures.append(
                {"case_id": case_id, "harness_status": packet_harness_status}
            )
            consecutive_harness_failures += 1
            abort_reason = _harness_abort_reason(
                subject=f"case {case_id}",
                status=packet_harness_status,
                consecutive=consecutive_harness_failures,
                total=len(harness_failures),
            )
            if abort_reason:
                break
        if observed_request_total > plan.max_api_calls:
            abort_reason = (
                f"observed model requests reached {observed_request_total}, exceeding "
                f"between-case limit {plan.max_api_calls}; refusing additional paid cases"
            )
            break

    planned_ids = [str(case["id"]) for case in plan.cases]
    completed_ids = [str(result["case_id"]) for result in results]
    missing_ids = [case_id for case_id in planned_ids if case_id not in completed_ids]
    verdict_counts = dict(Counter(str(result["verdict"]) for result in results))
    complete = not missing_ids and len(results) == len(plan.cases) and abort_reason is None
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "execute",
        "status": "complete" if complete else "incomplete",
        "planned_count": len(plan.cases),
        "attempted_count": attempted,
        "resumed_count": resumed,
        "packet_count": sum(
            Path(packet_path).exists()
            for result in results
            if isinstance((packet_path := result.get("packet_path")), str)
        ),
        "judged_count": len(results),
        "completed_case_ids": completed_ids,
        "missing_case_ids": missing_ids,
        "skipped": skipped,
        "complete": complete,
        "estimated_api_calls": plan.estimated_api_calls,
        "estimated_model_requests": plan.estimated_api_calls,
        "observed_model_requests": observed_request_total,
        "between_case_model_request_limit": plan.max_api_calls,
        "hard_model_request_cap_enforced": False,
        "model_request_accounting_complete": model_request_accounting_complete,
        "abort_reason": abort_reason,
        "harness_failures": harness_failures,
        "verdict_counts": verdict_counts,
        "results": results,
    }
    summary["exit_code"] = exit_code_for_summary(summary)
    return summary


def _validate_existing_results(
    summary: dict[str, Any],
    plan: SuitePlan,
    *,
    run_id: str,
    run_dir: Path,
) -> None:
    """Authenticate a finalized summary before an idempotent resume returns it."""
    manifest_path = run_dir / "manifest.json"
    strict_execution_binding = manifest_path.exists()
    if strict_execution_binding:
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest_object = json.loads(manifest_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanError(f"cannot authenticate existing execute manifest: {exc}") from exc
        if not isinstance(manifest_object, dict):
            raise PlanError("existing execute manifest is not an object")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        cases_snapshot_sha256 = manifest_object.get("cases_snapshot_sha256")
        if not isinstance(cases_snapshot_sha256, str):
            raise PlanError("existing execute manifest has no cases snapshot SHA-256")
    else:
        # Unit-level deterministic-summary validation can operate without a
        # filesystem manifest. The CLI/finalizer always take the strict branch.
        manifest_sha256 = "0" * 64
        cases_snapshot_sha256 = "0" * 64

    stored_results = summary.get("results")
    if not isinstance(stored_results, list) or len(stored_results) > len(plan.cases):
        raise PlanError("existing results contain an invalid result list")
    verdict_by_id: dict[str, str] = {}
    observed_outcome_by_id: dict[str, str] = {}
    validated_results: list[dict[str, Any]] = []
    observed_total = 0

    for case, stored_result in zip(plan.cases, stored_results, strict=False):
        if not isinstance(stored_result, dict):
            raise PlanError("existing results contain a non-object judgment")
        case_id = str(case["id"])
        if stored_result.get("case_id") != case_id:
            raise PlanError(f"existing result order mismatch at {case_id}")
        skip_decision = _dependency_skip(
            case,
            verdict_by_id=verdict_by_id,
            observed_outcome_by_id=observed_outcome_by_id,
        )
        attempt_path = run_dir / "attempts" / f"{case_id}.json"
        loaded_attempt = (
            _load_attempt(
                case,
                attempt_path,
                run_id=run_id,
                manifest_sha256=manifest_sha256,
                cases_snapshot_sha256=cases_snapshot_sha256,
            )
            if attempt_path.exists()
            else None
        )
        loaded = _load_resume_judgment(
            case,
            run_dir / "judgments" / f"{case_id}.json",
            run_id=run_id,
            run_dir=run_dir,
            expected_skip=skip_decision,
            expected_execution_binding=(loaded_attempt[1] if loaded_attempt else None),
        )
        if loaded is None:
            raise PlanError(f"existing result for {case_id} has no bound judgment")
        validated, _packet = loaded
        if stored_result != validated:
            raise PlanError(f"existing result differs from its judgment for {case_id}")
        if validated["verdict"] != SKIPPED_VERDICT and loaded_attempt is None:
            raise PlanError(f"existing result has no attempt marker for {case_id}")
        if validated["verdict"] == SKIPPED_VERDICT and loaded_attempt is not None:
            raise PlanError(f"existing skipped result has an attempt marker for {case_id}")
        observed = validated.get("observed_model_requests")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            raise PlanError(f"existing result has invalid observed usage for {case_id}")
        observed_total += observed
        validated_results.append(validated)
        verdict_by_id[case_id] = str(validated["verdict"])
        observed_outcome_by_id[case_id] = str(validated.get("observed_outcome", "unknown"))

    completed_ids = [str(result["case_id"]) for result in validated_results]
    missing_ids = [str(case["id"]) for case in plan.cases[len(validated_results) :]]
    skipped_entries = [
        {"id": str(result["case_id"]), "reason": str(result.get("reason", ""))}
        for result in validated_results
        if result["verdict"] == SKIPPED_VERDICT
    ]
    non_skipped_count = sum(result["verdict"] != SKIPPED_VERDICT for result in validated_results)
    attempted = summary.get("attempted_count")
    resumed = summary.get("resumed_count")
    if (
        isinstance(attempted, bool)
        or not isinstance(attempted, int)
        or attempted < 0
        or isinstance(resumed, bool)
        or not isinstance(resumed, int)
        or resumed < 0
        or attempted + resumed != non_skipped_count
    ):
        raise PlanError("existing results have inconsistent attempted/resumed counts")

    expected_judgment_names = {f"{case_id}.json" for case_id in completed_ids}
    expected_attempt_names = {
        f"{result['case_id']}.json"
        for result in validated_results
        if result["verdict"] != SKIPPED_VERDICT
    }
    expected_claim_names = set(expected_attempt_names)
    actual_judgment_names = {
        path.name for path in (run_dir / "judgments").glob("*.json") if path.is_file()
    }
    actual_attempt_names = {
        path.name for path in (run_dir / "attempts").glob("*.json") if path.is_file()
    }
    actual_claim_names = {
        path.name for path in (run_dir / "claims").glob("*.json") if path.is_file()
    }
    if actual_judgment_names != expected_judgment_names:
        raise PlanError("existing results do not exactly cover the run judgments")
    if actual_attempt_names != expected_attempt_names:
        raise PlanError("existing results do not exactly cover the paid attempt markers")
    if actual_claim_names != expected_claim_names:
        raise PlanError("existing results do not exactly cover the consumed paid attempts")

    complete = (
        not missing_ids
        and len(validated_results) == len(plan.cases)
        and summary.get("abort_reason") is None
    )
    verdict_counts = dict(Counter(str(result["verdict"]) for result in validated_results))
    expected_exit_code = exit_code_for_summary(
        {"complete": complete, "verdict_counts": verdict_counts}
    )
    expected_fields: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "execute",
        "status": "complete" if complete else "incomplete",
        "planned_count": len(plan.cases),
        "packet_count": non_skipped_count,
        "completed_case_ids": completed_ids,
        "missing_case_ids": missing_ids,
        "judged_count": len(validated_results),
        "skipped": skipped_entries,
        "complete": complete,
        "estimated_api_calls": plan.estimated_api_calls,
        "estimated_model_requests": plan.estimated_api_calls,
        "observed_model_requests": observed_total,
        "between_case_model_request_limit": plan.max_api_calls,
        "hard_model_request_cap_enforced": False,
        "model_request_accounting_complete": True,
        "verdict_counts": verdict_counts,
        "exit_code": expected_exit_code,
    }
    if any(summary.get(key) != value for key, value in expected_fields.items()):
        raise PlanError("existing results summary does not match its bound judgments")


def _dry_run_summary(plan: SuitePlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "dry_run",
        "status": "planned_not_executed",
        "planned_count": len(plan.cases),
        "attempted_count": 0,
        "resumed_count": 0,
        "packet_count": 0,
        "judged_count": 0,
        "completed_case_ids": [],
        "missing_case_ids": [str(case["id"]) for case in plan.cases],
        "skipped": [],
        "complete": False,
        "estimated_api_calls": plan.estimated_api_calls,
        "estimated_model_requests": plan.estimated_api_calls,
        "observed_model_requests": 0,
        "between_case_model_request_limit": plan.max_api_calls,
        "hard_model_request_cap_enforced": False,
        "model_request_accounting_complete": None,
        "abort_reason": None,
        "verdict_counts": {},
        "results": [],
        "exit_code": EXIT_SUCCESS,
    }


def _default_run_dir() -> Path:
    now = datetime.now(tz=UTC)
    return (
        SKILL_DIR
        / "runs"
        / now.strftime("%Y-%m-%d")
        / (now.strftime("%H%M%S") + f"-{uuid.uuid4().hex[:6]}")
    )


def _print_lint_issues(issues: list[LintIssue]) -> None:
    for issue in issues:
        location = f" [{issue.case_id}]" if issue.case_id else ""
        print(f"{issue.severity.upper()} {issue.code}{location}: {issue.message}", file=sys.stderr)


def _run_preflight(path: Path) -> str | None:
    """Run the zero-model-call readiness check before paid execution."""
    completed = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return None
    detail = redact_sensitive_text(completed.stderr or completed.stdout).strip()
    return detail or f"preflight exited {completed.returncode}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--tier", action="append", choices=("core", "smoke", "live"))
    parser.add_argument("--id", action="append", dest="case_ids")
    parser.add_argument(
        "--max-api-calls",
        type=int,
        help=(
            "Between-case billable model-request stop limit (legacy option name). It cannot "
            "interrupt an in-flight nested agent; actual Opik LLM spans are checked after it."
        ),
    )
    parser.add_argument("--allow-expensive", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted execute run after exact manifest/fingerprint validation",
    )
    parser.add_argument("--run-one", type=Path, default=DEFAULT_RUN_ONE)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Actually invoke run_one.py")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only; make zero OBaI/model/provider calls",
    )
    args = parser.parse_args()

    if args.resume and (not args.execute or args.run_dir is None):
        print("ERROR: --resume requires --execute and an explicit --run-dir", file=sys.stderr)
        return EXIT_CONFIGURATION

    try:
        cases_bytes = args.cases.read_bytes()
        raw = yaml.safe_load(cases_bytes)
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: cannot load {args.cases}: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    if not isinstance(raw, dict) or not isinstance(raw.get("test_cases"), list):
        print("ERROR: cases YAML must contain a test_cases list", file=sys.stderr)
        return EXIT_CONFIGURATION

    issues = lint_suite(raw)
    _print_lint_issues(issues)
    if any(issue.severity == "error" for issue in issues):
        return EXIT_CONFIGURATION
    if args.execute and any(issue.severity == "warning" for issue in issues):
        print(
            "ERROR: paid execution requires strict case lint with zero warnings",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION

    suite_config = raw.get("suite") if isinstance(raw.get("suite"), dict) else {}
    default_tier = raw.get("default_tier", suite_config.get("default_tier", "core"))
    if not isinstance(default_tier, str):
        print("ERROR: default_tier must be a string", file=sys.stderr)
        return EXIT_CONFIGURATION
    explicit_max_calls = args.max_api_calls
    if explicit_max_calls is not None and explicit_max_calls < 0:
        print("ERROR: --max-api-calls must be non-negative", file=sys.stderr)
        return EXIT_CONFIGURATION

    # First close the requested case graph. When the user did not provide a limit,
    # use a provisional planning ceiling and then tighten the dry-run plan to the
    # exact closed-plan estimate. This prevents a smoke or surgical --id run from
    # silently inheriting the much larger default-core allowance.
    planning_cap = explicit_max_calls if explicit_max_calls is not None else sys.maxsize

    try:
        suite_budgets = raw.get("suite_budgets")
        if not isinstance(suite_budgets, dict):
            suite_budgets = {
                tier: {
                    "max_cases": suite_config.get(f"{tier}_max_cases"),
                    "max_estimated_api_calls": suite_config.get(f"{tier}_max_api_calls"),
                }
                for tier in ("core", "smoke", "extended", "live")
                if suite_config.get(f"{tier}_max_cases") is not None
                or suite_config.get(f"{tier}_max_api_calls") is not None
            }
        plan = choose_cases(
            raw["test_cases"],
            tiers=set(args.tier) if args.tier else None,
            ids=set(args.case_ids) if args.case_ids else None,
            default_tier=default_tier,
            allow_expensive=args.allow_expensive,
            max_api_calls=planning_cap,
            suite_budgets=suite_budgets,
        )
    except PlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    if any(tier in {"live", "extended"} for tier in plan.selected_tiers) and (
        explicit_max_calls is None
    ):
        print(
            "ERROR: live/extended selections require an explicit --max-api-calls "
            "between-case billable model-request limit",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    if args.execute and explicit_max_calls is None:
        print(
            "ERROR: every paid execution requires an explicit --max-api-calls between-case "
            "limit; one in-flight nested agent can still overshoot it",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    if explicit_max_calls is None:
        plan = SuitePlan(
            cases=plan.cases,
            selected_tiers=plan.selected_tiers,
            estimated_api_calls=plan.estimated_api_calls,
            max_api_calls=plan.estimated_api_calls,
        )

    run_dir = args.run_dir or _default_run_dir()
    execution_mode = "execute" if args.execute else "dry_run"
    manifest_path = run_dir / "manifest.json"
    results_path = run_dir / "results.json"
    snapshot_path = (run_dir / CASES_SNAPSHOT_NAME).resolve()
    executable_cases_path = args.cases.resolve()
    if args.resume:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest root is not an object")
            validate_resume_manifest(
                manifest,
                plan,
                cases_bytes=cases_bytes,
                run_dir=run_dir,
                run_one_path=args.run_one,
                preflight_path=args.preflight,
            )
            executable_cases_path = _validate_cases_snapshot(manifest, run_dir)
        except (OSError, ValueError, json.JSONDecodeError, PlanError) as exc:
            print(f"ERROR: cannot resume: {exc}", file=sys.stderr)
            return EXIT_CONFIGURATION
        if results_path.exists():
            try:
                existing_results = json.loads(results_path.read_text(encoding="utf-8"))
                if not isinstance(existing_results, dict):
                    raise ValueError("results root is not an object")
                if existing_results.get("run_id") != manifest.get("run_id"):
                    raise ValueError("results run_id does not match manifest")
                _validate_existing_results(
                    existing_results,
                    plan,
                    run_id=str(manifest["run_id"]),
                    run_dir=run_dir,
                )
                exit_code = int(existing_results.get("exit_code", EXIT_INFRASTRUCTURE))
            except (OSError, ValueError, json.JSONDecodeError, PlanError) as exc:
                print(f"ERROR: existing results are invalid: {exc}", file=sys.stderr)
                return EXIT_CONFIGURATION
            print(json.dumps(existing_results, indent=2, sort_keys=True))
            return exit_code
    else:
        try:
            manifest = build_manifest(
                plan,
                cases_path=args.cases,
                cases_bytes=cases_bytes,
                mode=execution_mode,
                cases_snapshot_path=snapshot_path if args.execute else None,
                run_one_path=args.run_one,
                preflight_path=args.preflight,
            )
            if args.execute:
                write_immutable_bytes(snapshot_path, cases_bytes, read_only=True)
                executable_cases_path = snapshot_path
            write_immutable_json(manifest_path, manifest)
        except (ImmutableManifestError, PlanError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_CONFIGURATION

    if args.execute:
        try:
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        except OSError as exc:
            print(f"ERROR: cannot bind execute manifest: {exc}", file=sys.stderr)
            return EXIT_CONFIGURATION
        preflight_error = _run_preflight(args.preflight)
        if preflight_error:
            print(
                f"ERROR: preflight failed before any paid case: {preflight_error}", file=sys.stderr
            )
            return EXIT_INFRASTRUCTURE
        try:
            summary = _execute_plan(
                plan,
                run_id=str(manifest["run_id"]),
                cases_path=executable_cases_path,
                run_dir=run_dir,
                run_one_path=args.run_one,
                cases_sha256=str(manifest["cases_snapshot_sha256"]),
                manifest_sha256=manifest_sha256,
                preflight_path=args.preflight,
                expected_runtime_fingerprint=str(manifest["runtime_fingerprint"]),
                calendar_anchor=str(manifest["calendar_anchor"]),
                resume=args.resume,
            )
        except PlanError as exc:
            print(f"ERROR: cannot execute or resume safely: {exc}", file=sys.stderr)
            return EXIT_CONFIGURATION
    else:
        summary = _dry_run_summary(plan)
    summary["run_id"] = manifest["run_id"]
    summary["manifest_path"] = str(manifest_path)
    try:
        write_immutable_json(results_path, summary)
    except ImmutableManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION

    print(json.dumps(summary, indent=2, sort_keys=True))
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
