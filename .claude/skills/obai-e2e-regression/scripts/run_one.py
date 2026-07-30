#!/usr/bin/env python3
"""Drive one e2e regression case end-to-end.

Steps:
  1. Load case from cases.yaml.
  2. Mint a unique marker and append it to the submitted query.
  3. Run `uv run obai query --json --session <id>` as a subprocess.
  4. Resolve the matching Opik trace via marker + start_time filter.
  5. Run the curated inspect_trace.py for a human-readable trace view.
  6. Write a single JSON packet to <run_dir>/<id>.json AND echo to stdout.

Idempotent only when the checkpoint fingerprint still matches the case,
runner configuration, prompt sources, and chain dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CASES = SKILL_DIR / "cases" / "cases.yaml"
INSPECT_TRACE_DEFAULT = SCRIPT_DIR / "inspect_trace.py"

CLI_TIMEOUT_S = 900.0
# Span indexing is eventually consistent.  This must exceed the inspector's
# bounded fetch/retry window so complete evidence is not killed mid-retry.
INSPECT_TIMEOUT_S = 45.0

ASYNC_ETA_RE = re.compile(r"Estimated\s*Time:?\s*~?\s*(\d+)\s*seconds?", re.IGNORECASE)
ASYNC_BUFFER_S = 30
ASYNC_MAX_WAIT_S = 600
ASYNC_DEFAULT_ETA_S = 60
ASYNC_FOLLOWUP_TEMPLATE = (
    "Check job {job_id} and echo that same value under the label `Job ID:`. "
    "Begin with exactly one of: "
    "Status: queued, "
    "Status: running, Status: completed, or Status: failed. If completed, "
    "return the full stored result requested in the prior turn without "
    "starting or recomputing a job."
)

sys.path.insert(0, str(SCRIPT_DIR))
from preflight import (  # noqa: E402
    CredentialConfigurationError,
    configured_opik_project,
    configured_opik_url,
    effective_openai_credential_identity,
    effective_regression_environment,
    normalize_opik_url,
    redact_sensitive_text,
)
from judge_packet import ASYNC_JOB_ID_RE  # noqa: E402
from resolve_trace import TraceLookupError, find_trace_by_marker  # noqa: E402

CACHE_SCHEMA_VERSION = 3
MARKER_TEMPLATE = "[OBaI regression correlation: {marker}. Do not repeat this marker.]"
ASYNC_MAX_POLLS = 2
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "expired", "not_found"}
PENDING_JOB_STATUSES = {"queued", "running", "pending", "in_progress"}
ASYNC_HARNESS_FAILURE_STATUSES = {
    "cli_failed",
    "job_id_missing",
    "job_id_mismatch",
    "session_id_missing",
    "session_id_mismatch",
}
ASYNC_STATUS_RE = re.compile(
    r"\bstatus\b[^a-z0-9_]{0,20}"
    r"(queued|running|pending|in[_ -]?progress|completed|failed|cancelled|expired|not[_ -]?found)\b",
    re.IGNORECASE,
)
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ATTEMPT_NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")


def _valid_calendar_context(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("timezone"), str)
        and isinstance(value.get("today"), str)
        and isinstance(value.get("tomorrow"), str)
        and isinstance(value.get("current_year"), int)
    )


def materialize_calendar_context(
    case: dict[str, Any],
    dependency_packet: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    default_timezone: str | None = None,
) -> dict[str, Any] | None:
    """Pin calendar-relative terms once per root session chain.

    Live ``now``/``latest`` data still resolves at each retrieval, but calendar
    words such as today/tomorrow/current year must not change halfway through a
    multi-turn chain that crosses local midnight.
    """
    if case.get("date_policy") not in {"live", "relative"}:
        return None
    if dependency_packet is not None:
        inherited = dependency_packet.get("calendar_context")
        if not _valid_calendar_context(inherited):
            raise ValueError("relative/live chain parent has no valid calendar_context")
        return dict(inherited)

    timezone = case.get("timezone") or default_timezone
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("relative/live case needs a non-empty IANA timezone")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone {timezone!r}") from exc
    local_now = now.astimezone(zone) if now is not None else datetime.now(tz=zone)
    today = local_now.date()
    return {
        "timezone": timezone,
        "today": today.isoformat(),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "current_year": today.year,
    }


def parse_calendar_anchor(value: str | None) -> datetime | None:
    """Parse a timezone-aware suite anchor and normalize it to UTC."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("calendar anchor must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("calendar anchor must include a timezone offset")
    return parsed.astimezone(UTC)


def materialize_query(query: str, context: dict[str, Any] | None) -> str:
    if context is None:
        return query.rstrip()
    return (
        f"{query.rstrip()}\n\n"
        "[Regression calendar context: interpret calendar-relative terms using "
        f"today={context['today']} and tomorrow={context['tomorrow']} in "
        f"{context['timezone']}; current year={context['current_year']}. Keep this "
        "calendar context fixed for the session chain. `Now` and `latest` still "
        "require fresh data at this turn's retrieval time.]"
    )


def load_case(
    cases_path: Path,
    case_id: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if not CASE_ID_RE.fullmatch(case_id):
        raise SystemExit(f"Unsafe case id {case_id!r}")
    cases_bytes = cases_path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(cases_bytes).hexdigest() != expected_sha256:
        raise SystemExit("Executable cases snapshot SHA-256 mismatch")
    raw = yaml.safe_load(cases_bytes)
    if not isinstance(raw, dict) or not isinstance(raw.get("test_cases"), list):
        raise SystemExit(f"Invalid cases file {cases_path}: expected test_cases list")
    for entry in raw["test_cases"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") == case_id:
            if not isinstance(entry.get("query"), str) or not entry["query"].strip():
                raise SystemExit(f"Case {case_id!r} has no non-empty query")
            parent = entry.get("chain_from")
            if parent is not None and (
                not isinstance(parent, str) or not CASE_ID_RE.fullmatch(parent)
            ):
                raise SystemExit(f"Unsafe chain_from id {parent!r} in case {case_id!r}")
            return entry
    msg = f"Case '{case_id}' not found in {cases_path}"
    raise SystemExit(msg)


def load_suite_timezone(
    cases_path: Path,
    *,
    expected_sha256: str | None = None,
) -> str | None:
    """Return the top-level/nested suite timezone bound to the snapshot."""
    cases_bytes = cases_path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(cases_bytes).hexdigest() != expected_sha256:
        raise SystemExit("Executable cases snapshot SHA-256 mismatch")
    raw = yaml.safe_load(cases_bytes)
    if not isinstance(raw, dict):
        raise SystemExit(f"Invalid cases file {cases_path}: expected mapping")
    nested_suite = raw.get("suite") if isinstance(raw.get("suite"), dict) else {}
    timezone = raw.get("timezone", nested_suite.get("timezone"))
    if timezone is None:
        return None
    if not isinstance(timezone, str) or not timezone.strip():
        raise SystemExit("Suite timezone must be a non-empty IANA timezone string")
    return timezone


def run_cli(
    query: str,
    session_id: str,
    *,
    timeout_s: float = CLI_TIMEOUT_S,
) -> dict[str, Any]:
    t0 = datetime.now(tz=UTC)
    started = time.perf_counter()
    timed_out = False
    cli_environment = effective_regression_environment()
    # Inline completeness scoring can invoke an undeclared external LLM judge.
    # The canonical gate has its own deterministic judge and cost accounting.
    cli_environment["ENABLE_INLINE_SCORING"] = "false"
    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "obai",
                "query",
                query,
                "--json",
                "--session",
                session_id,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=cli_environment,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        stdout = (
            raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, bytes) else raw_out
        )
        prior_err = (
            raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, bytes) else raw_err
        )
        stderr = prior_err + f"\n[TIMEOUT after {timeout_s}s]"
        exit_code = -1
        timed_out = True

    latency_ms = int((time.perf_counter() - started) * 1000)

    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError:
        parsed = None

    return {
        "started_at": t0.isoformat().replace("+00:00", "Z"),
        "latency_ms": latency_ms,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_json": parsed,
        "stdout_raw": stdout,
        "stderr": redact_sensitive_text(stderr or "")[-2000:],
    }


def fetch_curated_trace(
    trace_id: str,
    inspect_script: Path,
    *,
    base_url: str,
    project: str,
) -> str:
    base_url = normalize_opik_url(base_url)
    if not inspect_script.exists():
        return f"[inspect_trace.py not found at {inspect_script}]"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(inspect_script),
                trace_id,
                "--url",
                base_url,
                "--project",
                project,
            ],
            capture_output=True,
            text=True,
            timeout=INSPECT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"[inspect_trace.py timed out after {INSPECT_TIMEOUT_S}s]"

    if result.returncode != 0:
        safe_stderr = redact_sensitive_text(result.stderr.strip())
        return f"[inspect_trace.py exit={result.returncode}]\n{safe_stderr[:1000]}"
    return result.stdout


def fetch_raw_trace_evidence(
    trace_id: str,
    inspect_script: Path,
    *,
    base_url: str,
    project: str,
) -> dict[str, Any]:
    """Fetch lossless trace/span JSON for deterministic judging."""
    base_url = normalize_opik_url(base_url)
    if not inspect_script.exists():
        raise RuntimeError(f"inspect_trace.py not found at {inspect_script}")
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(inspect_script),
                trace_id,
                "--raw",
                "--url",
                base_url,
                "--project",
                project,
            ],
            capture_output=True,
            text=True,
            timeout=INSPECT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"inspect_trace.py --raw timed out after {INSPECT_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        safe_stderr = redact_sensitive_text(result.stderr.strip())
        raise RuntimeError(f"inspect_trace.py --raw exit={result.returncode}: {safe_stderr[:1000]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"inspect_trace.py --raw returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("spans"), list):
        raise RuntimeError("inspect_trace.py --raw returned an invalid evidence bundle")
    trace = payload.get("trace")
    if not isinstance(trace, dict) or trace.get("id") != trace_id:
        raise RuntimeError(
            "inspect_trace.py --raw returned evidence for a different or missing trace id"
        )
    for span in payload["spans"]:
        if not isinstance(span, dict):
            raise RuntimeError("inspect_trace.py --raw returned a non-object span")
        span_trace_id = span.get("trace_id")
        if span_trace_id is not None and span_trace_id != trace_id:
            raise RuntimeError(
                f"span {span.get('id')!r} belongs to trace {span_trace_id!r}, not {trace_id!r}"
            )
    return payload


def extract_async_job_ids(response_text: str) -> list[str]:
    """Return stable unique job IDs without guessing between ambiguities."""
    return list(dict.fromkeys(ASYNC_JOB_ID_RE.findall(response_text or "")))


def extract_async_job(response_text: str) -> tuple[str | None, int]:
    """Pull one unambiguous job_id and ETA from an async-stub response."""
    if not response_text:
        return None, 0
    job_ids = extract_async_job_ids(response_text)
    if len(job_ids) != 1:
        return None, 0
    eta_match = ASYNC_ETA_RE.search(response_text)
    eta_s = int(eta_match.group(1)) if eta_match else ASYNC_DEFAULT_ETA_S
    return job_ids[0], eta_s


def mark_query(query: str, marker: str) -> str:
    """Put the correlation marker in the submitted text Opik records."""
    return f"{query.rstrip()}\n\n{MARKER_TEMPLATE.format(marker=marker)}"


def extract_final_response(cli: dict[str, Any]) -> str | None:
    parsed = cli.get("stdout_json")
    if isinstance(parsed, dict):
        for key in ("response", "output", "answer"):
            value = parsed.get(key)
            if isinstance(value, str):
                return value
    return None


def is_expected_guardrail_exit(case: dict[str, Any], cli: dict[str, Any]) -> bool:
    parsed = cli.get("stdout_json")
    return bool(
        case.get("expected_outcome") == "hub_reject"
        and case.get("expect_rejection") is True
        and cli.get("exit_code") == 1
        and isinstance(parsed, dict)
        and parsed.get("guardrail_rejected") is True
    )


def extract_async_status(response_text: str) -> str | None:
    match = ASYNC_STATUS_RE.search(response_text or "")
    if not match:
        return None
    return match.group(1).lower().replace(" ", "_").replace("-", "_")


def cli_session_error(cli: dict[str, Any], expected_session_id: str) -> str | None:
    """Require the CLI's structured result to attest the requested session."""
    stdout = cli.get("stdout_json")
    if not isinstance(stdout, dict):
        return "CLI result has no structured session evidence"
    actual = stdout.get("session_id")
    if not isinstance(actual, str) or not actual:
        return "CLI result is missing session_id"
    if actual != expected_session_id:
        return f"CLI returned session_id={actual!r}, expected {expected_session_id!r}"
    return None


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _hash_tree(paths: list[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    source_suffixes = {
        ".py",
        ".pyi",
        ".md",
        ".yaml",
        ".yml",
        ".toml",
        ".lock",
        ".json",
        ".js",
        ".ts",
    }
    source_names = {"Dockerfile", "VERSION"}
    excluded_parts = {
        ".venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for directory, dirnames, filenames in os.walk(path):
                dirnames[:] = [name for name in dirnames if name not in excluded_parts]
                directory_path = Path(directory)
                for filename in filenames:
                    candidate = directory_path / filename
                    if candidate.suffix in source_suffixes or filename in source_names:
                        files.append(candidate)
    for path in sorted(set(files)):
        try:
            content = path.read_bytes()
            resolved = path.resolve()
        except OSError:
            continue
        try:
            relative: Path | str = resolved.relative_to(root.resolve())
        except ValueError:
            # Hash external runtime configuration without serializing its path
            # into a packet or manifest.
            relative = "external:" + hashlib.sha256(str(resolved).encode()).hexdigest()
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


_RUNTIME_ENV_EXACT = frozenset(
    {
        "ENABLE_GUARDRAILS",
        "ENABLE_INLINE_SCORING",
        "OBAI_REGRESSION_CONFIG_FINGERPRINT",
        "STRATEGY_MAX_TURNS",
        "CRYPTO_MAX_TURNS",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPIK_ENABLED",
        "OPIK_URL",
        "OPIK_URL_OVERRIDE",
        "OPIK_PROJECT_NAME",
        "OPIK_OBAI_PROJECT_NAME",
    }
)
_RUNTIME_ENV_PREFIXES = ("MCP_", "LANGCACHE_", "TOOL_CACHE_")
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def runtime_environment_binding() -> dict[str, dict[str, str]]:
    """Return active runtime configuration with secret values one-way bound."""
    public: dict[str, str] = {}
    secret_digests: dict[str, str] = {}
    effective_environment = effective_regression_environment()
    effective_environment["ENABLE_INLINE_SCORING"] = "false"
    for key, value in effective_environment.items():
        selected = (
            key in _RUNTIME_ENV_EXACT
            or key.startswith(_RUNTIME_ENV_PREFIXES)
            or key.endswith(("_MODEL", "_REASONING_EFFORT", "_VERBOSITY"))
            or key.endswith(("_MCP_URL", "_MCP_PORT"))
        )
        if not selected:
            continue
        if any(marker in key.upper() for marker in _SECRET_ENV_MARKERS):
            digest = hashlib.sha256()
            digest.update(b"obai-e2e-runtime-secret-v1\0")
            digest.update(key.encode("utf-8"))
            digest.update(b"\0")
            digest.update(value.encode("utf-8"))
            secret_digests[key] = digest.hexdigest()
        else:
            public[key] = value
    return {
        "public": dict(sorted(public.items())),
        "secret_digests": dict(sorted(secret_digests.items())),
    }


def runtime_source_paths(repo_root: Path) -> list[Path]:
    """Return source/config inputs shared by manifest and per-case fingerprints."""
    return [
        repo_root / "src",
        repo_root / "skills",
        repo_root / ".env",
        repo_root / "pyproject.toml",
        repo_root / "uv.lock",
        SCRIPT_DIR,
        Path.home() / ".obai" / ".env",
        Path.home() / ".obai" / "preferences.json",
    ]


def input_fingerprint(
    *,
    case: dict[str, Any],
    opik_url: str,
    opik_project: str,
    inspect_script: Path,
    dependency_digest: str | None,
    calendar_context: dict[str, Any] | None = None,
) -> str:
    """Fingerprint all inputs that can change a cached case's meaning."""
    repo_root = SKILL_DIR.parents[2]
    runtime_sources = _hash_tree(
        [*runtime_source_paths(repo_root), inspect_script],
        root=repo_root,
    )
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "case": case,
        "runner": {
            "cli_timeout_s": CLI_TIMEOUT_S,
            "async_max_wait_s": ASYNC_MAX_WAIT_S,
            "async_followup_template": ASYNC_FOLLOWUP_TEMPLATE,
            "opik_url": opik_url,
            "opik_project": opik_project,
            "inspect_script": str(inspect_script.resolve()),
            "inspect_script_sha256": _hash_file(inspect_script),
            "runtime_sources_sha256": runtime_sources,
            "openai_credential_identity": effective_openai_credential_identity(),
            "runtime_environment": runtime_environment_binding(),
            "temporal_bucket": (
                calendar_context.get("today") if isinstance(calendar_context, dict) else None
            ),
            "calendar_context": calendar_context,
        },
        "dependency_sha256": dependency_digest,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def case_contract_fingerprint(case: dict[str, Any]) -> str:
    """Hash the exact declared case contract for chain attribution."""
    canonical = json.dumps(
        case,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    """Read a regular file without following a final-component symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"{label} is missing or unreadable") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SystemExit(f"{label} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _require_contained(path: Path, root: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{label} must be inside the run directory") from exc
    return resolved


def validate_execution_binding(
    *,
    run_dir: Path,
    cases_path: Path,
    case_id: str,
    run_id: str,
    attempt_nonce: str,
    manifest_sha256: str,
    cases_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate one paid helper invocation against run_suite artifacts.

    The manifest and attempt marker use fixed paths under ``run_dir`` so a
    caller cannot redirect validation toward unrelated files.  The attempt
    nonce is high-entropy run_suite output and is bound to the manifest bytes,
    executable suite snapshot, and exact case contract.
    """
    if not CASE_ID_RE.fullmatch(case_id):
        raise SystemExit(f"Unsafe case id {case_id!r}")
    if not RUN_ID_RE.fullmatch(run_id):
        raise SystemExit("run_id has an unsafe or invalid format")
    if not ATTEMPT_NONCE_RE.fullmatch(attempt_nonce):
        raise SystemExit("attempt_nonce must contain 128-512 bits of lowercase hex entropy")
    if not SHA256_RE.fullmatch(manifest_sha256):
        raise SystemExit("manifest SHA-256 must be 64 lowercase hexadecimal characters")
    if not SHA256_RE.fullmatch(cases_sha256):
        raise SystemExit("cases SHA-256 must be 64 lowercase hexadecimal characters")

    try:
        resolved_run_dir = run_dir.resolve(strict=True)
    except OSError as exc:
        raise SystemExit("run directory does not exist") from exc
    if not resolved_run_dir.is_dir():
        raise SystemExit("run directory is not a directory")

    resolved_cases = _require_contained(
        cases_path,
        resolved_run_dir,
        label="executable cases snapshot",
    )
    cases_bytes = _read_regular_bytes(resolved_cases, label="executable cases snapshot")
    actual_cases_sha256 = hashlib.sha256(cases_bytes).hexdigest()
    if actual_cases_sha256 != cases_sha256:
        raise SystemExit("Executable cases snapshot SHA-256 mismatch")

    manifest_path = resolved_run_dir / "manifest.json"
    manifest_bytes = _read_regular_bytes(manifest_path, label="execute manifest")
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise SystemExit("execute manifest SHA-256 mismatch")
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise SystemExit("execute manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise SystemExit("execute manifest root is not an object")
    if manifest.get("schema_version") != 1 or manifest.get("mode") != "execute":
        raise SystemExit("execute manifest has an unsupported schema or mode")
    if manifest.get("run_id") != run_id:
        raise SystemExit("execute manifest run_id mismatch")
    if manifest.get("suite_fingerprint") != cases_sha256:
        raise SystemExit("execute manifest suite fingerprint mismatch")
    if manifest.get("cases_snapshot_sha256") != cases_sha256:
        raise SystemExit("execute manifest cases snapshot SHA-256 mismatch")
    recorded_snapshot = manifest.get("cases_snapshot_path")
    if not isinstance(recorded_snapshot, str):
        raise SystemExit("execute manifest has no cases snapshot path")
    recorded_snapshot_path = _require_contained(
        Path(recorded_snapshot),
        resolved_run_dir,
        label="manifest cases snapshot",
    )
    if recorded_snapshot_path != resolved_cases:
        raise SystemExit("execute manifest cases snapshot path mismatch")

    case = load_case(resolved_cases, case_id, expected_sha256=cases_sha256)
    expected_case_fingerprint = case_contract_fingerprint(case)
    records = manifest.get("cases")
    if not isinstance(records, list) or manifest.get("planned_count") != len(records):
        raise SystemExit("execute manifest has an invalid case plan")
    matching_records = [
        record for record in records if isinstance(record, dict) and record.get("id") == case_id
    ]
    if len(matching_records) != 1:
        raise SystemExit("execute manifest must contain exactly one matching case")
    record = matching_records[0]
    if record.get("fingerprint") != expected_case_fingerprint or record.get("snapshot") != case:
        raise SystemExit("execute manifest case binding mismatch")

    attempt_path = resolved_run_dir / "attempts" / f"{case_id}.json"
    attempt_bytes = _read_regular_bytes(attempt_path, label="immutable attempt marker")
    try:
        attempt = json.loads(attempt_bytes)
    except json.JSONDecodeError as exc:
        raise SystemExit("immutable attempt marker is not valid JSON") from exc
    expected_attempt = {
        "schema_version": 2,
        "run_id": run_id,
        "case_id": case_id,
        "case_fingerprint": expected_case_fingerprint,
        "attempt_nonce": attempt_nonce,
        "manifest_sha256": manifest_sha256,
        "cases_snapshot_sha256": cases_sha256,
    }
    if attempt != expected_attempt:
        raise SystemExit("immutable attempt marker binding mismatch")

    binding = {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": case_id,
        "case_fingerprint": expected_case_fingerprint,
        "attempt_nonce": attempt_nonce,
        "attempt_marker_sha256": hashlib.sha256(attempt_bytes).hexdigest(),
        "manifest_sha256": manifest_sha256,
        "cases_snapshot_sha256": cases_sha256,
    }
    return case, binding


def packet_execution_binding_failure(
    packet: dict[str, Any],
    *,
    run_dir: Path,
    cases_path: Path,
    case_id: str,
    run_id: str,
    manifest_sha256: str,
    cases_sha256: str,
) -> str | None:
    """Verify a preserved dependency packet against its own paid attempt."""
    recorded = packet.get("execution_binding")
    if not isinstance(recorded, dict):
        return "packet has no execution binding"
    attempt_nonce = recorded.get("attempt_nonce")
    if not isinstance(attempt_nonce, str):
        return "packet execution binding has no attempt nonce"
    try:
        _case, expected = validate_execution_binding(
            run_dir=run_dir,
            cases_path=cases_path,
            case_id=case_id,
            run_id=run_id,
            attempt_nonce=attempt_nonce,
            manifest_sha256=manifest_sha256,
            cases_sha256=cases_sha256,
        )
    except SystemExit as exc:
        return f"packet execution binding is invalid: {exc}"
    if recorded != expected:
        return "packet execution binding does not match its immutable attempt"
    required_top_level = {
        "run_id": expected["run_id"],
        "attempt_nonce": expected["attempt_nonce"],
        "manifest_sha256": expected["manifest_sha256"],
        "cases_snapshot_sha256": expected["cases_snapshot_sha256"],
        "attempt_marker_sha256": expected["attempt_marker_sha256"],
    }
    if any(packet.get(key) != value for key, value in required_top_level.items()):
        return "packet top-level execution binding is incomplete or inconsistent"
    return None


def cache_is_reusable(
    case: dict[str, Any],
    packet: dict[str, Any],
    fingerprint: str,
    *,
    now: datetime | None = None,
    execution_binding: dict[str, Any] | None = None,
) -> bool:
    """Accept a checkpoint only while its contract and freshness SLA hold."""
    if execution_binding is not None and packet.get("execution_binding") != execution_binding:
        return False
    if packet.get("input_fingerprint") != fingerprint:
        return False
    if packet.get("harness_status") != "completed":
        return False
    if case.get("date_policy") not in {"live", "relative"}:
        return True
    freshness = case.get("freshness") if isinstance(case.get("freshness"), dict) else {}
    max_age = case.get("max_age_seconds", freshness.get("max_age_seconds"))
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        return False
    ended_at = packet.get("ended_at")
    ended = _parse_packet_timestamp(ended_at)
    if ended is None:
        return False
    current = now or datetime.now(tz=UTC)
    age_seconds = (current.astimezone(UTC) - ended).total_seconds()
    return -60 <= age_seconds <= max_age


def _parse_packet_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _checkpoint_failure(
    packet: dict[str, Any],
    *,
    expected_case_id: str | None = None,
    expected_input_fingerprint: str | None = None,
) -> str | None:
    if expected_case_id is not None and packet.get("id") != expected_case_id:
        return f"parent id={packet.get('id')!r}, expected {expected_case_id!r}"
    if (
        expected_input_fingerprint is not None
        and packet.get("input_fingerprint") != expected_input_fingerprint
    ):
        return "parent input fingerprint is stale or does not match its current contract"
    harness_status = packet.get("harness_status")
    if harness_status not in (None, "completed"):
        return f"parent harness_status={harness_status!r}"

    for key in ("verdict", "judge_verdict", "result"):
        value = packet.get(key)
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in {
                "fail",
                "failed",
                "needs_review",
                "fail_product",
            } or normalized.startswith("inconclusive"):
                return f"parent {key}={value!r}"
    judge = packet.get("judge")
    if isinstance(judge, dict):
        value = judge.get("verdict") or judge.get("outcome")
        if isinstance(value, str) and value.lower() not in {
            "pass",
            "passed",
            "success",
            "pass_degraded",
            "needs_semantic_review",
        }:
            return f"parent judge verdict={value!r}"

    session_id = packet.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return "parent session_id is missing"
    cli = packet.get("cli")
    if not isinstance(cli, dict):
        return "parent CLI evidence is missing"
    if cli.get("timed_out"):
        return "parent CLI timed out"
    if cli.get("exit_code") != 0:
        return f"parent CLI exit_code={cli.get('exit_code')!r}"
    if not extract_final_response(cli):
        return "parent final response is missing"
    session_error = cli_session_error(cli, session_id)
    if session_error:
        return f"parent packet session validation failed: {session_error}"
    trace = packet.get("trace")
    if not isinstance(trace, dict) or not trace.get("id"):
        return "parent trace is missing"
    followup = packet.get("followup")
    if isinstance(followup, dict):
        async_status = followup.get("status")
        if async_status != "completed":
            return f"parent async job status={async_status!r}"
    return None


def _write_packet(path: Path, packet: dict[str, Any]) -> str:
    payload = json.dumps(packet, indent=2) + "\n"
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp_path.unlink(missing_ok=True)
    return payload


def _claim_paid_execution(run_dir: Path, execution_binding: dict[str, Any]) -> None:
    """Atomically consume one attempt nonce before its first model request."""
    claims_dir = run_dir / "claims"
    created_directory = False
    try:
        claims_dir.mkdir(mode=0o700)
        created_directory = True
    except FileExistsError:
        pass
    if claims_dir.is_symlink() or not claims_dir.is_dir():
        raise SystemExit("paid execution claims path is not a trusted directory")
    if claims_dir.resolve(strict=True) != run_dir.resolve(strict=True) / "claims":
        raise SystemExit("paid execution claims path escapes the run directory")

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(claims_dir, directory_flags)
    try:
        claim_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        claim_name = f"{execution_binding['case_id']}.json"
        try:
            claim_fd = os.open(claim_name, claim_flags, 0o400, dir_fd=directory_fd)
        except FileExistsError as exc:
            raise SystemExit(
                "attempt nonce was already consumed; refusing a duplicate model call"
            ) from exc
        try:
            payload = (
                json.dumps(execution_binding, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            with os.fdopen(claim_fd, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(claim_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

    if created_directory:
        run_directory_fd = os.open(run_dir, directory_flags)
        try:
            os.fsync(run_directory_fd)
        finally:
            os.close(run_directory_fd)


def run_async_followup(
    *,
    job_id: str,
    eta_s: int,
    session_id: str,
    base_url: str,
    project: str,
    inspect_script: Path,
    max_polls: int = ASYNC_MAX_POLLS,
    max_wait_s: int = ASYNC_MAX_WAIT_S,
    poll_prompt: str = ASYNC_FOLLOWUP_TEMPLATE,
    authorization_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Poll job status through bounded, correlated follow-up turns."""
    base_url = normalize_opik_url(base_url)
    started_monotonic = time.monotonic()
    max_wait_s = max(1, min(max_wait_s, ASYNC_MAX_WAIT_S))
    deadline = started_monotonic + max_wait_s
    # Waiting through the provider ETA plus a small buffer usually saves a
    # paid status turn.  A second poll, if budgeted, uses a short retry delay.
    delay_s = max(1, min(eta_s + ASYNC_BUFFER_S, max_wait_s - 1 or 1))
    total_wait_s = 0
    polls: list[dict[str, Any]] = []
    status: str | None = None

    max_polls = max(1, min(max_polls, ASYNC_MAX_POLLS))
    while time.monotonic() < deadline and len(polls) < max_polls:
        remaining_s = max(0.0, deadline - time.monotonic())
        wait_s = min(float(delay_s), remaining_s)
        if wait_s:
            time.sleep(wait_s)
            total_wait_s += int(wait_s)

        followup_query = poll_prompt.format(job_id=job_id)
        marker = f"regress:async:{job_id}:{uuid.uuid4().hex[:8]}"
        marked_query = mark_query(followup_query, marker)
        remaining_s = max(1.0, deadline - time.monotonic())
        if authorization_check is not None:
            authorization_check()
        cli = run_cli(
            marked_query,
            session_id,
            timeout_s=min(CLI_TIMEOUT_S, remaining_s),
        )

        trace_id: str | None = None
        attempts = 0
        lookup_error: str | None = None
        try:
            trace_id, attempts = find_trace_by_marker(
                marker=marker,
                t0_iso=cli["started_at"],
                base_url=base_url,
                project=project,
            )
        except TraceLookupError as exc:
            lookup_error = redact_sensitive_text(exc)

        curated: str | None = None
        raw_evidence: dict[str, Any] | None = None
        evidence_error: str | None = None
        if trace_id:
            curated = fetch_curated_trace(
                trace_id,
                inspect_script,
                base_url=base_url,
                project=project,
            )
            try:
                raw_evidence = fetch_raw_trace_evidence(
                    trace_id,
                    inspect_script,
                    base_url=base_url,
                    project=project,
                )
            except RuntimeError as exc:
                evidence_error = redact_sensitive_text(exc)

        response_text = extract_final_response(cli) or ""
        status = extract_async_status(response_text)
        response_job_ids = extract_async_job_ids(response_text)
        job_id_matches = response_job_ids == [job_id]
        session_error = cli_session_error(cli, session_id)
        stdout = cli.get("stdout_json")
        response_session_id = stdout.get("session_id") if isinstance(stdout, dict) else None
        poll = {
            "marker": marker,
            "query": followup_query,
            "marked_query": marked_query,
            "started_at": cli["started_at"],
            "ended_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "latency_ms": cli["latency_ms"],
            "status": status,
            "response_job_ids": response_job_ids,
            "job_id_matches": job_id_matches,
            "requested_session_id": session_id,
            "response_session_id": response_session_id,
            "session_id_matches": session_error is None,
            "final_response": response_text or None,
            "cli": {
                "exit_code": cli["exit_code"],
                "timed_out": cli["timed_out"],
                "stdout_json": cli["stdout_json"],
                "stdout_raw": cli["stdout_raw"],
                "stderr": cli["stderr"],
            },
            "trace": {
                "id": trace_id,
                "lookup_attempts": attempts,
                "lookup_error": lookup_error,
                "curated": curated,
                "raw": raw_evidence.get("trace") if raw_evidence else None,
                "spans": raw_evidence.get("spans") if raw_evidence else None,
                "evidence_error": evidence_error,
            },
        }
        polls.append(poll)

        if cli["timed_out"] or cli["exit_code"] != 0:
            status = "cli_failed"
            break
        if session_error:
            status = (
                "session_id_missing"
                if not isinstance(response_session_id, str) or not response_session_id
                else "session_id_mismatch"
            )
            poll["status"] = status
            break
        if not job_id_matches:
            status = "job_id_missing" if not response_job_ids else "job_id_mismatch"
            poll["status"] = status
            break
        if status in TERMINAL_JOB_STATUSES:
            break
        delay_s = min(30, max(1, int(deadline - time.monotonic()) - 1))

    poll_limit_reached = (
        len(polls) >= max_polls
        and status not in TERMINAL_JOB_STATUSES
        and status not in ASYNC_HARNESS_FAILURE_STATUSES
    )
    timed_out = status not in TERMINAL_JOB_STATUSES and status not in ASYNC_HARNESS_FAILURE_STATUSES
    result: dict[str, Any] = {
        "job_id": job_id,
        "eta_seconds": eta_s,
        "wait_seconds": total_wait_s,
        "status": status,
        "timed_out": timed_out,
        "poll_limit_reached": poll_limit_reached,
        "evidence_complete": bool(polls)
        and all(
            poll.get("trace", {}).get("id")
            and not poll.get("trace", {}).get("lookup_error")
            and poll.get("trace", {}).get("spans") is not None
            and not poll.get("trace", {}).get("evidence_error")
            and poll.get("session_id_matches") is True
            for poll in polls
        ),
        "polls": polls,
    }
    if polls:
        # Preserve the legacy top-level view as an alias to the last poll.
        last = polls[-1]
        result.update(
            {
                "query": last["query"],
                "marked_query": last["marked_query"],
                "marker": last["marker"],
                "started_at": last["started_at"],
                "ended_at": last["ended_at"],
                "latency_ms": last["latency_ms"],
                "final_response": last["final_response"],
                "cli": last["cli"],
                "trace": last["trace"],
            }
        )
    return result


def build_packet(
    case: dict[str, Any],
    cli: dict[str, Any],
    marker: str,
    marked_query: str,
    session_id: str,
    trace_id: str | None,
    lookup_attempts: int,
    curated: str | None,
    followup: dict[str, Any] | None,
    input_fingerprint: str | None = None,
    trace_lookup_error: str | None = None,
    raw_trace_evidence: dict[str, Any] | None = None,
    trace_evidence_error: str | None = None,
    submitted_query: str | None = None,
    calendar_context: dict[str, Any] | None = None,
    execution_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_guardrail_exit = is_expected_guardrail_exit(case, cli) and bool(trace_id)
    session_validation_error = cli_session_error(cli, session_id)
    if cli.get("timed_out") or (cli.get("exit_code") != 0 and not expected_guardrail_exit):
        harness_status = "cli_failed"
    elif session_validation_error:
        harness_status = "session_mismatch"
    elif trace_lookup_error or not trace_id:
        harness_status = "trace_lookup_failed"
    elif trace_evidence_error or raw_trace_evidence is None:
        harness_status = "trace_evidence_failed"
    elif case.get("expect_async_job") and not followup and not case.get("async_job_optional"):
        harness_status = "async_followup_failed"
    elif followup and (
        followup.get("timed_out")
        or followup.get("status") in ASYNC_HARNESS_FAILURE_STATUSES
        or followup.get("evidence_complete") is False
    ):
        harness_status = "async_followup_failed"
    else:
        harness_status = "completed"
    harness_exit_code = 0 if harness_status == "completed" else 2

    packet = {
        "id": case["id"],
        "case_fingerprint": case_contract_fingerprint(case),
        "input_fingerprint": input_fingerprint,
        "harness_status": harness_status,
        "harness_exit_code": harness_exit_code,
        "feature": case.get("feature", ""),
        "description": case.get("description", ""),
        "query": case["query"],
        "submitted_query": submitted_query or case["query"],
        "calendar_context": calendar_context,
        "marker": marker,
        "marked_query": marked_query,
        "session_id": session_id,
        "session_validation_error": session_validation_error,
        "expected_tools": case.get("expected_tools", []),
        "expected_sequence": case.get("expected_sequence"),
        "expected_skills": case.get("expected_skills", []),
        "expected_skills_absent": case.get("expected_skills_absent", []),
        "allowed_extras": case.get("allowed_extras", []),
        "expect_rejection": case.get("expect_rejection", False),
        "expect_options_shape": case.get("expect_options_shape"),
        "expect_async_job": case.get("expect_async_job", False),
        "async_job_status": followup.get("status") if isinstance(followup, dict) else None,
        "expected_outcome": case.get("expected_outcome", "success"),
        "acceptable_outcomes": case.get("acceptable_outcomes", []),
        "assertions": case.get("assertions", {}),
        "date_policy": case.get("date_policy"),
        "tier": case.get("tier"),
        "smoke": case.get("smoke", False),
        "chain_from": case.get("chain_from"),
        "started_at": cli["started_at"],
        "ended_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "latency_ms": cli["latency_ms"],
        "final_response": extract_final_response(cli),
        "cli": {
            "exit_code": cli["exit_code"],
            "timed_out": cli["timed_out"],
            "stdout_json": cli["stdout_json"],
            "stdout_raw": cli["stdout_raw"],
            "stderr": cli["stderr"],
        },
        "trace": {
            "id": trace_id,
            "lookup_attempts": lookup_attempts,
            "lookup_error": trace_lookup_error,
            "curated": curated,
            "raw": raw_trace_evidence.get("trace") if raw_trace_evidence else None,
            "spans": raw_trace_evidence.get("spans") if raw_trace_evidence else None,
            "evidence_error": trace_evidence_error,
        },
        "followup": followup,
    }
    if execution_binding is not None:
        packet["execution_binding"] = dict(execution_binding)
        packet["run_id"] = execution_binding["run_id"]
        packet["attempt_nonce"] = execution_binding["attempt_nonce"]
        packet["manifest_sha256"] = execution_binding["manifest_sha256"]
        packet["cases_snapshot_sha256"] = execution_binding["cases_snapshot_sha256"]
        packet["attempt_marker_sha256"] = execution_binding["attempt_marker_sha256"]
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one OBaI e2e regression case.")
    parser.add_argument("--id", required=True, help="Case ID from cases.yaml.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run output dir.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Path to cases.yaml.")
    parser.add_argument(
        "--cases-sha256",
        required=True,
        help="Expected SHA-256 of the immutable executable cases snapshot.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Execute-manifest run identifier supplied by run_suite.py.",
    )
    parser.add_argument(
        "--attempt-nonce",
        required=True,
        help="High-entropy per-case nonce from the immutable attempt marker.",
    )
    parser.add_argument(
        "--manifest-sha256",
        required=True,
        help="SHA-256 of the immutable execute manifest.",
    )
    parser.add_argument(
        "--inspect-script",
        type=Path,
        default=INSPECT_TRACE_DEFAULT,
        help="Path to opik-trace-inspect inspect_trace.py.",
    )
    parser.add_argument("--opik-url", default=configured_opik_url())
    parser.add_argument("--opik-project", default=configured_opik_project())
    parser.add_argument(
        "--calendar-anchor",
        help="Suite-wide timezone-aware ISO instant used to materialize root calendar dates.",
    )
    args = parser.parse_args()
    try:
        args.opik_url = normalize_opik_url(args.opik_url)
    except ValueError as exc:
        raise SystemExit(redact_sensitive_text(exc)) from exc
    try:
        calendar_anchor = parse_calendar_anchor(args.calendar_anchor)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    case, execution_binding = validate_execution_binding(
        run_dir=args.run_dir,
        cases_path=args.cases,
        case_id=args.id,
        run_id=args.run_id,
        attempt_nonce=args.attempt_nonce,
        manifest_sha256=args.manifest_sha256,
        cases_sha256=args.cases_sha256,
    )
    try:
        effective_openai_credential_identity()
    except CredentialConfigurationError as exc:
        raise SystemExit(redact_sensitive_text(exc)) from exc
    resolved_run_dir = args.run_dir.resolve(strict=True)
    resolved_cases = args.cases.resolve(strict=True)
    suite_timezone = load_suite_timezone(
        resolved_cases,
        expected_sha256=args.cases_sha256,
    )
    out_path = resolved_run_dir / f"{args.id}.json"

    def revalidate_paid_authorization() -> None:
        _case, current_binding = validate_execution_binding(
            run_dir=resolved_run_dir,
            cases_path=resolved_cases,
            case_id=args.id,
            run_id=args.run_id,
            attempt_nonce=args.attempt_nonce,
            manifest_sha256=args.manifest_sha256,
            cases_sha256=args.cases_sha256,
        )
        if _case != case or current_binding != execution_binding:
            raise SystemExit("paid execution binding changed after initial validation")
        current_fingerprint = input_fingerprint(
            case=case,
            opik_url=args.opik_url,
            opik_project=args.opik_project,
            inspect_script=args.inspect_script,
            dependency_digest=dependency_digest,
            calendar_context=calendar_context,
        )
        if current_fingerprint != fingerprint:
            raise SystemExit("runtime, credential, or model configuration changed before call")
        parent_id = case.get("chain_from")
        if isinstance(parent_id, str):
            try:
                current_dependency_digest = hashlib.sha256(
                    (resolved_run_dir / f"{parent_id}.json").read_bytes()
                ).hexdigest()
            except OSError as exc:
                raise SystemExit("chain dependency changed before paid call") from exc
            if current_dependency_digest != dependency_digest:
                raise SystemExit("chain dependency changed before paid call")

    dependency_packet: dict[str, Any] | None = None
    dependency_error: str | None = None
    dependency_digest: str | None = None
    if chain_from := case.get("chain_from"):
        prior_path = resolved_run_dir / f"{chain_from}.json"
        if not prior_path.exists():
            dependency_digest = hashlib.sha256(b"missing").hexdigest()
            dependency_error = f"chain_from={chain_from!r} checkpoint not found"
        else:
            try:
                prior_raw = prior_path.read_bytes()
                dependency_digest = hashlib.sha256(prior_raw).hexdigest()
                loaded_prior = json.loads(prior_raw)
                if not isinstance(loaded_prior, dict):
                    dependency_error = f"chain_from={chain_from!r} checkpoint is not an object"
                else:
                    dependency_packet = loaded_prior
                    parent_case = load_case(
                        resolved_cases,
                        chain_from,
                        expected_sha256=args.cases_sha256,
                    )
                    parent_binding_reason = packet_execution_binding_failure(
                        dependency_packet,
                        run_dir=resolved_run_dir,
                        cases_path=resolved_cases,
                        case_id=chain_from,
                        run_id=args.run_id,
                        manifest_sha256=args.manifest_sha256,
                        cases_sha256=args.cases_sha256,
                    )
                    if parent_binding_reason:
                        dependency_error = (
                            f"chain_from={chain_from!r} failed: {parent_binding_reason}"
                        )
                    parent_dependency_digest: str | None = None
                    parent_of_parent = parent_case.get("chain_from")
                    if isinstance(parent_of_parent, str):
                        grandparent_path = resolved_run_dir / f"{parent_of_parent}.json"
                        if not grandparent_path.exists():
                            dependency_error = (
                                f"chain_from={chain_from!r} cannot be verified because "
                                f"its parent {parent_of_parent!r} is missing"
                            )
                        else:
                            parent_dependency_digest = hashlib.sha256(
                                grandparent_path.read_bytes()
                            ).hexdigest()
                    expected_parent_fingerprint = input_fingerprint(
                        case=parent_case,
                        opik_url=args.opik_url,
                        opik_project=args.opik_project,
                        inspect_script=args.inspect_script,
                        dependency_digest=parent_dependency_digest,
                        calendar_context=(
                            dependency_packet.get("calendar_context")
                            if isinstance(dependency_packet, dict)
                            else None
                        ),
                    )
                    reason = _checkpoint_failure(
                        dependency_packet,
                        expected_case_id=chain_from,
                        expected_input_fingerprint=expected_parent_fingerprint,
                    )
                    if reason:
                        dependency_error = f"chain_from={chain_from!r} failed: {reason}"
            except (OSError, json.JSONDecodeError) as exc:
                dependency_error = f"chain_from={chain_from!r} checkpoint unreadable: {exc}"

    try:
        calendar_context = materialize_calendar_context(
            case,
            dependency_packet,
            now=calendar_anchor,
            default_timezone=suite_timezone,
        )
    except ValueError as exc:
        if dependency_packet is not None:
            dependency_error = f"chain_from={chain_from!r} failed: {exc}"
            calendar_context = None
        else:
            raise SystemExit(str(exc)) from exc

    fingerprint = input_fingerprint(
        case=case,
        opik_url=args.opik_url,
        opik_project=args.opik_project,
        inspect_script=args.inspect_script,
        dependency_digest=dependency_digest,
        calendar_context=calendar_context,
    )

    if out_path.exists():
        try:
            cached_raw = out_path.read_text()
            cached = json.loads(cached_raw)
        except (OSError, json.JSONDecodeError):
            cached = None
        if isinstance(cached, dict) and cache_is_reusable(
            case,
            cached,
            fingerprint,
            execution_binding=execution_binding,
        ):
            sys.stdout.write(cached_raw)
            return int(cached.get("harness_exit_code", 0))
        raise SystemExit(
            "existing packet does not match the paid execution binding; "
            "refusing a duplicate model call"
        )

    if dependency_error:
        now = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
        failure_packet = {
            "id": case["id"],
            "case_fingerprint": case_contract_fingerprint(case),
            "input_fingerprint": fingerprint,
            "harness_status": "dependency_failed",
            "harness_exit_code": 2,
            "harness_error": dependency_error,
            "feature": case.get("feature", ""),
            "description": case.get("description", ""),
            "query": case["query"],
            "submitted_query": materialize_query(case["query"], calendar_context),
            "calendar_context": calendar_context,
            "marker": None,
            "marked_query": None,
            "session_id": dependency_packet.get("session_id") if dependency_packet else None,
            "chain_from": case.get("chain_from"),
            "started_at": now,
            "ended_at": now,
            "latency_ms": 0,
            "final_response": None,
            "cli": {
                "exit_code": None,
                "timed_out": False,
                "stdout_json": None,
                "stdout_raw": "",
                "stderr": dependency_error,
            },
            "trace": {
                "id": None,
                "lookup_attempts": 0,
                "lookup_error": dependency_error,
                "curated": None,
            },
            "followup": None,
            "execution_binding": dict(execution_binding),
            "run_id": execution_binding["run_id"],
            "attempt_nonce": execution_binding["attempt_nonce"],
            "manifest_sha256": execution_binding["manifest_sha256"],
            "cases_snapshot_sha256": execution_binding["cases_snapshot_sha256"],
            "attempt_marker_sha256": execution_binding["attempt_marker_sha256"],
        }
        payload = _write_packet(out_path, failure_packet)
        sys.stdout.write(payload)
        return 2

    run_tag = f"regress:{case['id']}:{uuid.uuid4().hex[:8]}"
    query = case["query"].strip()
    submitted_query = materialize_query(query, calendar_context)
    marked_query = mark_query(submitted_query, run_tag)
    if dependency_packet is not None:
        session_id = str(dependency_packet["session_id"])
    else:
        session_id = f"regress-{uuid.uuid4().hex[:8]}"

    revalidate_paid_authorization()
    _claim_paid_execution(resolved_run_dir, execution_binding)
    cli = run_cli(marked_query, session_id)

    trace_id: str | None = None
    attempts = 0
    trace_lookup_error: str | None = None
    try:
        trace_id, attempts = find_trace_by_marker(
            marker=run_tag,
            t0_iso=cli["started_at"],
            base_url=args.opik_url,
            project=args.opik_project,
        )
    except TraceLookupError as exc:
        trace_lookup_error = redact_sensitive_text(exc)
    if trace_id is None and trace_lookup_error is None:
        trace_lookup_error = f"unique marker not found after {attempts} attempts"

    curated: str | None = None
    raw_trace_evidence: dict[str, Any] | None = None
    trace_evidence_error: str | None = None
    if trace_id:
        curated = fetch_curated_trace(
            trace_id,
            args.inspect_script,
            base_url=args.opik_url,
            project=args.opik_project,
        )
        try:
            raw_trace_evidence = fetch_raw_trace_evidence(
                trace_id,
                args.inspect_script,
                base_url=args.opik_url,
                project=args.opik_project,
            )
        except RuntimeError as exc:
            trace_evidence_error = redact_sensitive_text(exc)

    followup: dict[str, Any] | None = None
    if case.get("expect_async_job") and not cli.get("timed_out"):
        response_text = extract_final_response(cli) or ""
        job_id, eta_s = extract_async_job(response_text)
        if job_id:
            cost = case.get("cost") if isinstance(case.get("cost"), dict) else {}
            contract = (
                case.get("async_contract") if isinstance(case.get("async_contract"), dict) else {}
            )
            configured_max_polls = cost.get("max_async_polls", ASYNC_MAX_POLLS)
            if not isinstance(configured_max_polls, int):
                configured_max_polls = ASYNC_MAX_POLLS
            initial_status = extract_async_status(response_text)
            if initial_status in TERMINAL_JOB_STATUSES:
                # Do not buy a redundant status turn when the initial request
                # already contains a terminal job result.
                followup = {
                    "job_id": job_id,
                    "eta_seconds": eta_s,
                    "wait_seconds": 0,
                    "status": initial_status,
                    "timed_out": False,
                    "poll_limit_reached": False,
                    "evidence_complete": True,
                    "initial_terminal": True,
                    "final_response": response_text,
                    "polls": [],
                }
            else:
                configured_max_wait = contract.get("max_wait_seconds", ASYNC_MAX_WAIT_S)
                if not isinstance(configured_max_wait, int):
                    configured_max_wait = ASYNC_MAX_WAIT_S
                poll_prompt = contract.get("poll_prompt", ASYNC_FOLLOWUP_TEMPLATE)
                if not isinstance(poll_prompt, str) or "{job_id}" not in poll_prompt:
                    poll_prompt = ASYNC_FOLLOWUP_TEMPLATE
                followup = run_async_followup(
                    job_id=job_id,
                    eta_s=eta_s,
                    session_id=session_id,
                    base_url=args.opik_url,
                    project=args.opik_project,
                    inspect_script=args.inspect_script,
                    max_polls=configured_max_polls,
                    max_wait_s=configured_max_wait,
                    poll_prompt=poll_prompt,
                    authorization_check=revalidate_paid_authorization,
                )
        elif case.get("async_job_optional"):
            # The product completed synchronously and never dispatched a job.
            # Leave follow-up empty so the initial response is judged directly.
            followup = None
        else:
            followup = {
                "job_id": None,
                "eta_seconds": 0,
                "wait_seconds": 0,
                "status": "job_id_missing",
                "timed_out": False,
                "evidence_complete": False,
                "polls": [],
            }

    packet = build_packet(
        case=case,
        cli=cli,
        marker=run_tag,
        marked_query=marked_query,
        session_id=session_id,
        trace_id=trace_id,
        lookup_attempts=attempts,
        curated=curated,
        followup=followup,
        input_fingerprint=fingerprint,
        trace_lookup_error=trace_lookup_error,
        raw_trace_evidence=raw_trace_evidence,
        trace_evidence_error=trace_evidence_error,
        submitted_query=submitted_query,
        calendar_context=calendar_context,
        execution_binding=execution_binding,
    )

    payload = _write_packet(out_path, packet)
    sys.stdout.write(payload)
    return int(packet["harness_exit_code"])


if __name__ == "__main__":
    sys.exit(main())
