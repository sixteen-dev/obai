#!/usr/bin/env python3
"""Fetch account info, all positions, and risk status.

Outputs JSON to stdout:
    {
        "account": {...},
        "positions": [...],
        "risk": {...}
    }
"""

import json
import sys

from lib.alpaca_client import AlpacaClient, AlpacaClientError
from lib.logging_config import get_logger
from lib.risk import RiskChecker

_logger = get_logger("get_portfolio")


def main() -> None:
    """Fetch portfolio state and print JSON result."""
    try:
        client = AlpacaClient()
        risk_checker = RiskChecker(client)

        account = client.get_account()
        positions = client.get_positions()
        risk_status = risk_checker.get_risk_status()

        _logger.info(
            "portfolio_fetched",
            equity=account.equity,
            position_count=len(positions),
            exposure_pct=risk_status.current_exposure_pct,
        )

        result = {
            "account": account.to_dict(),
            "positions": [p.to_dict() for p in positions],
            "position_count": len(positions),
            "risk": risk_status.to_dict(),
        }
        print(json.dumps(result, default=str))

    except AlpacaClientError as exc:
        _logger.exception("portfolio_error", error=str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
