#!/usr/bin/env python3
"""Disabled legacy OBaI regression case runner.

Paid regression execution is intentionally available only through the
cost-controlled canonical suite runner.  Keeping this tracked compatibility
stub prevents old bookmarks or agent instructions from bypassing the run
manifest, preflight, attempt ledger, and between-case request limit.
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "ERROR: the legacy direct case runner is disabled. Use "
        ".claude/skills/obai-e2e-regression/scripts/run_suite.py; paid cases "
        "must be authorized and bound to its manifest and attempt ledger.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
