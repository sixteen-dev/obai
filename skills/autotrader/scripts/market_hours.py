#!/usr/bin/env python3
"""Check if the stock market is currently open.

Outputs JSON to stdout:
    {"is_open": true, "timestamp": "...", "next_open": "...", "next_close": "..."}
"""

import json
import sys

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.logging_config import get_logger

_logger = get_logger("market_hours")


def main() -> None:
    """Check market hours and print JSON result."""
    try:
        client = AlpacaClient()
        clock = client.get_clock()
        _logger.info("market_check", is_open=clock["is_open"])
        print(json.dumps(clock, default=str))
    except AlpacaClientError as exc:
        _logger.exception("market_hours_error", error=str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
