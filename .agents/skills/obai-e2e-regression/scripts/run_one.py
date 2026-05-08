#!/usr/bin/env python3
"""Drive one e2e regression case end-to-end.

Steps:
  1. Load case from cases.yaml.
  2. Mint a unique marker, prepend to the query.
  3. Run `uv run obai query --json --session <id>` as a subprocess.
  4. Resolve the matching Opik trace via marker + start_time filter.
  5. Run the curated inspect_trace.py for a human-readable trace view.
  6. Write a single JSON packet to <run_dir>/<id>.json AND echo to stdout.

Idempotent: if <run_dir>/<id>.json already exists, reads it and exits 0
(used to resume an interrupted run).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CASES = SKILL_DIR / "cases" / "cases.yaml"
INSPECT_TRACE_DEFAULT = SCRIPT_DIR / "inspect_trace.py"

CLI_TIMEOUT_S = 900.0
INSPECT_TIMEOUT_S = 15.0

ASYNC_JOB_ID_RE = re.compile(r"Job\s*ID:?\s*`?([A-Za-z0-9_]+)`?")
ASYNC_ETA_RE = re.compile(r"Estimated\s*Time:?\s*~?\s*(\d+)\s*seconds?", re.IGNORECASE)
ASYNC_BUFFER_S = 30
ASYNC_MAX_WAIT_S = 600
ASYNC_DEFAULT_ETA_S = 60
ASYNC_FOLLOWUP_TEMPLATE = (
    "Check job {job_id} for the completed walk-forward results. "
    "Return the full strategy verdict, operator list, and total trades."
)

sys.path.insert(0, str(SCRIPT_DIR))
from resolve_trace import find_trace_by_marker  # noqa: E402


def load_case(cases_path: Path, case_id: str) -> dict[str, Any]:
    raw = yaml.safe_load(cases_path.read_text())
    for entry in raw.get("test_cases", []):
        if entry.get("id") == case_id:
            return entry
    msg = f"Case '{case_id}' not found in {cases_path}"
    raise SystemExit(msg)


def run_cli(query: str, session_id: str) -> dict[str, Any]:
    t0 = datetime.now(tz=UTC)
    started = time.perf_counter()
    timed_out = False
    try:
        result = subprocess.run(
            [
                "uv", "run", "obai", "query",
                query,
                "--json",
                "--session", session_id,
            ],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_S,
            check=False,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        stdout = raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, bytes) else raw_out
        prior_err = raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, bytes) else raw_err
        stderr = prior_err + f"\n[TIMEOUT after {CLI_TIMEOUT_S}s]"
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
        "stdout_raw": stdout if parsed is None else None,
        "stderr": (stderr or "")[-2000:],
    }


def fetch_curated_trace(trace_id: str, inspect_script: Path) -> str:
    if not inspect_script.exists():
        return f"[inspect_trace.py not found at {inspect_script}]"
    try:
        result = subprocess.run(
            [sys.executable, str(inspect_script), trace_id],
            capture_output=True,
            text=True,
            timeout=INSPECT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"[inspect_trace.py timed out after {INSPECT_TIMEOUT_S}s]"

    if result.returncode != 0:
        return f"[inspect_trace.py exit={result.returncode}]\n{result.stderr.strip()[:1000]}"
    return result.stdout


def extract_async_job(response_text: str) -> tuple[str | None, int]:
    """Pull job_id and ETA seconds from a strategy async-stub response."""
    if not response_text:
        return None, 0
    job_match = ASYNC_JOB_ID_RE.search(response_text)
    if not job_match:
        return None, 0
    eta_match = ASYNC_ETA_RE.search(response_text)
    eta_s = int(eta_match.group(1)) if eta_match else ASYNC_DEFAULT_ETA_S
    return job_match.group(1), eta_s


def run_async_followup(
    *,
    job_id: str,
    eta_s: int,
    session_id: str,
    base_url: str,
    project: str,
    inspect_script: Path,
) -> dict[str, Any]:
    """Sleep ETA + buffer, then send a follow-up query in the same session.

    Returns a packet-shaped dict with the second turn's CLI result and trace.
    """
    wait_s = max(ASYNC_DEFAULT_ETA_S, min(eta_s + ASYNC_BUFFER_S, ASYNC_MAX_WAIT_S))
    time.sleep(wait_s)

    followup_query = ASYNC_FOLLOWUP_TEMPLATE.format(job_id=job_id)
    cli = run_cli(followup_query, session_id)

    trace_id, attempts = find_trace_by_marker(
        marker=followup_query,
        t0_iso=cli["started_at"],
        base_url=base_url,
        project=project,
    )
    curated: str | None = None
    if trace_id:
        curated = fetch_curated_trace(trace_id, inspect_script)

    return {
        "job_id": job_id,
        "wait_seconds": wait_s,
        "eta_seconds": eta_s,
        "query": followup_query,
        "started_at": cli["started_at"],
        "ended_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "latency_ms": cli["latency_ms"],
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
            "curated": curated,
        },
    }


def build_packet(case: dict[str, Any], cli: dict[str, Any], marker: str,
                 marked_query: str, session_id: str,
                 trace_id: str | None, lookup_attempts: int,
                 curated: str | None,
                 followup: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": case["id"],
        "feature": case.get("feature", ""),
        "description": case.get("description", ""),
        "query": case["query"],
        "marker": marker,
        "marked_query": marked_query,
        "session_id": session_id,
        "expected_tools": case.get("expected_tools", []),
        "expected_sequence": case.get("expected_sequence"),
        "expected_skills": case.get("expected_skills", []),
        "expected_skills_absent": case.get("expected_skills_absent", []),
        "allowed_extras": case.get("allowed_extras", []),
        "expect_rejection": case.get("expect_rejection", False),
        "expect_options_shape": case.get("expect_options_shape"),
        "expect_async_job": case.get("expect_async_job", False),
        "smoke": case.get("smoke", False),
        "started_at": cli["started_at"],
        "ended_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "latency_ms": cli["latency_ms"],
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
            "curated": curated,
        },
        "followup": followup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one OBaI e2e regression case.")
    parser.add_argument("--id", required=True, help="Case ID from cases.yaml.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Run output dir.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES,
                        help="Path to cases.yaml.")
    parser.add_argument("--inspect-script", type=Path, default=INSPECT_TRACE_DEFAULT,
                        help="Path to opik-trace-inspect inspect_trace.py.")
    parser.add_argument("--opik-url", default=os.environ.get("OPIK_URL_OVERRIDE",
                                                              "http://localhost:5173"))
    parser.add_argument("--opik-project", default=os.environ.get("OPIK_PROJECT_NAME",
                                                                  "obai-eval"))
    args = parser.parse_args()

    args.run_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.run_dir / f"{args.id}.json"

    if out_path.exists():
        sys.stdout.write(out_path.read_text())
        return 0

    case = load_case(args.cases, args.id)
    run_tag = f"regress:{case['id']}:{uuid.uuid4().hex[:8]}"
    query = case["query"].strip()
    session_id = f"regress-{uuid.uuid4().hex[:8]}"

    cli = run_cli(query, session_id)

    trace_id, attempts = find_trace_by_marker(
        marker=query,
        t0_iso=cli["started_at"],
        base_url=args.opik_url,
        project=args.opik_project,
    )

    curated: str | None = None
    if trace_id:
        curated = fetch_curated_trace(trace_id, args.inspect_script)

    followup: dict[str, Any] | None = None
    if case.get("expect_async_job") and not cli.get("timed_out"):
        response_text = ""
        stdout_json = cli.get("stdout_json")
        if isinstance(stdout_json, dict):
            response_text = stdout_json.get("response") or ""
        job_id, eta_s = extract_async_job(response_text)
        if job_id:
            followup = run_async_followup(
                job_id=job_id,
                eta_s=eta_s,
                session_id=session_id,
                base_url=args.opik_url,
                project=args.opik_project,
                inspect_script=args.inspect_script,
            )

    packet = build_packet(
        case=case,
        cli=cli,
        marker=run_tag,
        marked_query=query,
        session_id=session_id,
        trace_id=trace_id,
        lookup_attempts=attempts,
        curated=curated,
        followup=followup,
    )

    payload = json.dumps(packet, indent=2) + "\n"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(payload)
    os.replace(tmp_path, out_path)
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
