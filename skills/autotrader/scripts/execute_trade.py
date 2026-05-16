#!/usr/bin/env python3
"""Submit a trading order with pre-trade risk validation.

Usage:
    uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10
    uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 \
        --order-type limit --limit-price 195.00

Outputs JSON to stdout:
    On success: {"order_id": "...", "status": "accepted", "symbol": "AAPL", ...}
    On risk rejection: {"error": "Risk rejected: ...", "allowed": false}
"""

import argparse
import json
import sys

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.logging_config import get_logger
from lib.risk import RiskChecker

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

    _logger.info(
        "trade_attempt",
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        order_type=args.order_type,
    )

    try:
        client = AlpacaClient()
        risk_checker = RiskChecker(client)

        if not args.allow_after_hours:
            clock = client.get_clock()
            if not clock.get("is_open"):
                print(
                    json.dumps(
                        {
                            "error": (
                                "Market is closed. Pass --allow-after-hours "
                                "to queue the order, or wait for the next "
                                "open."
                            ),
                            "next_open": str(clock.get("next_open", "")),
                            "allowed": False,
                        }
                    )
                )
                sys.exit(1)

        # Pre-trade risk check
        risk_result = risk_checker.check_order(
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            limit_price=args.limit_price,
        )

        if not risk_result.allowed:
            print(
                json.dumps(
                    {
                        "error": f"Risk rejected: {risk_result.rejection_reason}",
                        "allowed": False,
                    }
                )
            )
            sys.exit(1)

        # Submit order
        order = client.submit_order(
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            order_type=args.order_type,
            limit_price=args.limit_price,
            stop_price=args.stop_price,
            time_in_force=args.time_in_force,
        )

        print(json.dumps(order.to_dict(), default=str))

    except (AlpacaClientError, ValueError) as exc:
        _logger.exception("trade_error", symbol=args.symbol, error=str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
