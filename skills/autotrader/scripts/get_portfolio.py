#!/usr/bin/env python3
"""Fetch account info, positions, open and recent orders, and risk status.

Outputs JSON to stdout:
    {
        "account": {...},
        "positions": [...],
        "position_count": 2,
        "open_orders": [...],
        "recent_orders": [...],
        "open_orders_may_be_truncated": false,
        "recent_orders_may_be_truncated": false,
        "risk": {...}
    }

`accepted` is not a fill: reconcile `filled_qty` and `filled_avg_price` before
changing holdings. Either truncation flag means the order list is a page, not a
complete ledger; recover older orders by client order ID.
"""

import json
import sys

from lib.alpaca_client import MAX_ORDER_PAGE, AlpacaClient, AlpacaClientError
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
        open_orders = client.get_orders("open", limit=MAX_ORDER_PAGE)
        recent_orders = client.get_orders("all", limit=MAX_ORDER_PAGE)

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
            "open_orders": [o.to_dict() for o in open_orders],
            "recent_orders": [o.to_dict() for o in recent_orders],
            "open_orders_may_be_truncated": len(open_orders) >= MAX_ORDER_PAGE,
            # Bounded history, not a complete fill ledger.
            "recent_orders_may_be_truncated": len(recent_orders) >= MAX_ORDER_PAGE,
            "risk": risk_status.to_dict(),
        }
        print(json.dumps(result, default=str))

    except (AlpacaClientError, ValueError) as exc:
        _logger.exception("portfolio_error", error=str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
