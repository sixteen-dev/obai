#!/usr/bin/env python3
"""Pre-flight readiness check for the OBaI e2e regression suite.

Verifies:
1. OPENAI_API_KEY is set.
2. Opik is reachable at http://localhost:5173.
3. `obai status` reports all 9 MCP servers healthy.

Exits 0 if ready, non-zero with a clear reason otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OPIK_URL = os.environ.get("OPIK_URL_OVERRIDE", "http://localhost:5173")
OPIK_PROJECT = os.environ.get("OPIK_PROJECT_NAME", "obai-eval")
TIMEOUT_S = 5.0
OBAI_STATUS_TIMEOUT_S = 30.0


def _fail(msg: str) -> int:
    sys.stderr.write(f"PREFLIGHT FAIL: {msg}\n")
    return 1


def check_openai_key() -> str | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set in the environment."
    return None


def check_opik() -> str | None:
    url = f"{OPIK_URL}/api/v1/private/traces?project_name={OPIK_PROJECT}&size=1"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            if resp.status != 200:
                return f"Opik returned HTTP {resp.status} for {url}"
    except urllib.error.URLError as e:
        return f"Opik not reachable at {OPIK_URL}: {e}"
    except OSError as e:
        return f"Opik connection error: {e}"
    return None


def check_obai_status() -> str | None:
    try:
        result = subprocess.run(
            ["uv", "run", "obai", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=OBAI_STATUS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "obai status timed out — MCP servers may be hung."
    except FileNotFoundError:
        return "`uv` not on PATH — install uv or activate the venv."

    if not result.stdout.strip():
        return f"obai status returned no output (exit={result.returncode}): {result.stderr.strip()[:300]}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return f"obai status returned non-JSON: {e} — stdout head: {result.stdout[:200]}"

    if not payload.get("all_healthy"):
        down = [s["name"] for s in payload.get("servers", []) if s.get("status") != "ok"]
        return f"MCP servers down: {', '.join(down) or 'unknown'}"
    return None


def main() -> int:
    checks = [
        ("OPENAI_API_KEY", check_openai_key),
        ("Opik reachable", check_opik),
        ("obai status (9 MCP servers)", check_obai_status),
    ]
    failed = False
    for label, fn in checks:
        problem = fn()
        if problem:
            sys.stderr.write(f"  [FAIL] {label}: {problem}\n")
            failed = True
        else:
            sys.stdout.write(f"  [OK]   {label}\n")
    if failed:
        return _fail("one or more checks failed; fix and re-run preflight.")
    sys.stdout.write("\nReady.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
