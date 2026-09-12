#!/usr/bin/env python3
"""Close an open position at market price.

Usage:
    uv run python scripts/close_position.py --symbol AAPL

Outputs JSON to stdout:
    On success: {"order_id": "...", "status": "accepted", "side": "sell", "qty": 25, ...}
    On failure: {"error": "...", "client_order_id": "...", "submission_state": "..."}
"""

import argparse
import json
import sys
from uuid import uuid4

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.execution import execute_order, submission_failure
from lib.logging_config import get_logger

_logger = get_logger("close_position")


def main() -> None:
    """Close position and print JSON result."""
    parser = argparse.ArgumentParser(description="Close a position at market price")
    parser.add_argument("--symbol", required=True, help="Ticker symbol to close")
    parser.add_argument(
        "--client-order-id", help="Stable intent ID; required for scheduled retries"
    )
    args = parser.parse_args()
    client_order_id = args.client_order_id or f"obai-{uuid4().hex}"

    _logger.info("close_attempt", symbol=args.symbol)

    try:
        client = AlpacaClient()
        order = execute_order(
            client,
            {"symbol": args.symbol, "close_position": True},
            client_order_id,
            reduce_only=True,
        )
        print(json.dumps({**order.to_dict(), "client_order_id": client_order_id}, default=str))

    except (AlpacaClientError, ValueError, OSError) as exc:
        _logger.exception("close_error", symbol=args.symbol, error=str(exc))
        print(json.dumps(submission_failure(exc, client_order_id)))
        sys.exit(1)


if __name__ == "__main__":
    main()
