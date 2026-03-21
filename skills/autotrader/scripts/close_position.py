#!/usr/bin/env python3
"""Close an open position at market price.

Usage:
    uv run python scripts/close_position.py --symbol AAPL

Outputs JSON to stdout:
    {"order_id": "...", "status": "accepted", "side": "sell", "qty": 25, ...}
"""

import argparse
import json
import sys

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.logging_config import get_logger

_logger = get_logger("close_position")


def main() -> None:
    """Close position and print JSON result."""
    parser = argparse.ArgumentParser(description="Close a position at market price")
    parser.add_argument("--symbol", required=True, help="Ticker symbol to close")
    args = parser.parse_args()

    _logger.info("close_attempt", symbol=args.symbol)

    try:
        client = AlpacaClient()
        order = client.close_position(args.symbol)
        print(json.dumps(order.to_dict(), default=str))

    except AlpacaClientError as exc:
        _logger.exception("close_error", symbol=args.symbol, error=str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
