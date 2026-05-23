"""Root-level smoke test.

This single test exists so `uv run pytest` at the monorepo root exits 0
instead of pytest's exit code 5 (no tests collected). The real per-service
suites live under each service directory and are aggregated by
scripts/run-all-tests.sh.

Asserts the aggregator script is present and executable so a regression
removing it would surface here instead of going unnoticed.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGGREGATOR = REPO_ROOT / "scripts" / "run-all-tests.sh"


def test_aggregator_script_present_and_executable() -> None:
    assert AGGREGATOR.is_file(), f"Missing aggregator script at {AGGREGATOR}"
    assert os.access(AGGREGATOR, os.X_OK), (
        f"Aggregator script is not executable: {AGGREGATOR}. "
        "Run `chmod +x scripts/run-all-tests.sh` to fix."
    )
