#!/usr/bin/env python3
"""Evaluate rule predicates from a validated completed-bar snapshot, without orders.

Outputs JSON to stdout:
    On success: {"symbol": "AAPL", "signal_bar_date": "...", "entry_signal": true, ...}
    On failure: {"error": "...", "eligible": false} on stderr, exit 1
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lib.logging_config import get_logger
from lib.signals import evaluate_signals

_logger = get_logger("evaluate_signals")


def _load_document(path: Path, label: str) -> dict[str, Any]:
    """Read a JSON object from disk, rejecting any other top-level shape."""
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document


def main() -> None:
    """Read the strategy and snapshot, evaluate predicates, print JSON result."""
    parser = argparse.ArgumentParser(description="Evaluate completed-bar rule predicates")
    parser.add_argument("--strategy", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--expected-bar-date", required=True)
    parser.add_argument("--expected-price-basis", required=True)
    args = parser.parse_args()
    try:
        result = evaluate_signals(
            _load_document(args.strategy, "strategy"),
            _load_document(args.snapshot, "snapshot"),
            expected_bar_date=args.expected_bar_date,
            expected_price_basis=args.expected_price_basis,
        )
        print(json.dumps(result, allow_nan=False))

    except (ValueError, KeyError, TypeError, OSError) as exc:
        _logger.exception("signal_error", snapshot=str(args.snapshot), error=str(exc))
        print(json.dumps({"error": str(exc), "eligible": False}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
