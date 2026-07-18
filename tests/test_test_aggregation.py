"""Checks that the monorepo aggregate command covers standalone test suites."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGGREGATOR = REPO_ROOT / "scripts" / "run-all-tests.sh"
LEGACY_RUN_ONE = REPO_ROOT / ".agents" / "skills" / "obai-e2e-regression" / "scripts" / "run_one.py"


def test_aggregate_runner_includes_regression_harness_and_evaluation_tests() -> None:
    """The aggregate command explicitly invokes both standalone suites."""
    script = AGGREGATOR.read_text(encoding="utf-8")

    assert 'uv run pytest -q ".claude/skills/obai-e2e-regression/tests" "$@"' in script
    assert 'cd "src/obai" && uv run pytest -q "evaluation/tests" "$@"' in script


def test_legacy_direct_paid_runner_is_disabled() -> None:
    """Old agent bookmarks cannot bypass the canonical cost-controlled runner."""
    completed = subprocess.run(
        [sys.executable, str(LEGACY_RUN_ONE)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "legacy direct case runner is disabled" in completed.stderr
    assert completed.stdout == ""
