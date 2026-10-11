#!/usr/bin/env python3
"""Submit a trading order with pre-trade risk validation.

Usage:
    uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10
    uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 \
        --order-type limit --limit-price 195.00

Outputs JSON to stdout:
    On success: {"order_id": "...", "status": "accepted", "symbol": "AAPL", ...}
    On risk rejection: {"error": "Risk rejected: ...", "submission_state": "not_submitted"}
"""

import argparse
import json
import sys
from uuid import uuid4

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.execution import execute_order, submission_failure
from lib.logging_config import get_logger

_logger = get_logger("execute_trade")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Execute a paper trade via Alpaca")
    parser.add_argument("--symbol", required=True, help="Ticker symbol (e.g., AAPL)")
    parser.add_argument("--side", required=True, choices=["buy", "sell"], help="Order side")
    parser.add_argument("--qty", required=True, type=float, help="Number of shares")
    parser.add_argument(
        "--order-type", default="market", choices=["market", "limit", "stop", "stop_limit"]
    )
    parser.add_argument("--limit-price", type=float, default=None, help="Limit price")
    parser.add_argument("--stop-price", type=float, default=None, help="Stop price")
    parser.add_argument(
        "--client-order-id", help="Stable intent ID; required for scheduled retries"
    )
    parser.add_argument("--reduce-only", action="store_true", help="Reject any position increase")
    parser.add_argument(
        "--time-in-force", default="day", choices=["day", "gtc", "opg", "cls", "ioc", "fok"]
    )
    parser.add_argument(
        "--allow-after-hours",
        action="store_true",
        help=(
            "Submit even if the market is closed. Default is to reject — "
            "agent reasoning steps can miss this otherwise, and queued "
            "market orders execute at the next open at unpredictable prices."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Execute trade with risk check and print JSON result."""
    args = parse_args()
    client_order_id = args.client_order_id or f"obai-{uuid4().hex}"

    _logger.info(
        "trade_attempt",
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        order_type=args.order_type,
    )

    try:
        client = AlpacaClient()
        order = execute_order(
            client,
            {
                "symbol": args.symbol,
                "side": args.side,
                "qty": args.qty,
                "order_type": args.order_type,
                "limit_price": args.limit_price,
                "stop_price": args.stop_price,
                "time_in_force": args.time_in_force,
            },
            client_order_id,
            allow_after_hours=args.allow_after_hours,
            reduce_only=args.reduce_only,
        )
        print(json.dumps({**order.to_dict(), "client_order_id": client_order_id}, default=str))

    except (AlpacaClientError, ValueError, OSError) as exc:
        _logger.exception("trade_error", symbol=args.symbol, error=str(exc))
        print(json.dumps(submission_failure(exc, client_order_id)))
        sys.exit(1)


if __name__ == "__main__":
    main()
