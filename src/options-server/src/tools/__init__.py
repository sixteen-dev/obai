"""Options tools for Polygon.io API."""

from .options import (
    get_latest_option_quote,
    get_latest_option_trade,
    get_option_aggregates,
    get_option_chain_snapshot,
    get_option_contract_snapshot,
    get_option_quotes_history,
    get_option_trades_history,
    list_option_contracts,
)

__all__ = [
    # MVP Tools (Critical)
    "get_option_chain_snapshot",
    "get_option_contract_snapshot",
    "get_latest_option_trade",
    "get_latest_option_quote",
    # Optional Tools (Nice-to-Have)
    "list_option_contracts",
    "get_option_trades_history",
    "get_option_quotes_history",
    "get_option_aggregates",
]
