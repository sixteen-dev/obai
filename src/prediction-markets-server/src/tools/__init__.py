"""Prediction market tool implementations."""

from .backtest import backtest_prediction_setup
from .discovery import get_market_details, search_prediction_markets
from .flow import get_top_holders, get_trade_flow
from .market_state import compare_prediction_markets, get_market_snapshot, get_price_history
from .wallets import get_trader_leaderboard, get_wallet_activity, get_wallet_profile

__all__ = [
    "backtest_prediction_setup",
    "compare_prediction_markets",
    "get_market_details",
    "get_market_snapshot",
    "get_price_history",
    "get_top_holders",
    "get_trade_flow",
    "get_trader_leaderboard",
    "get_wallet_activity",
    "get_wallet_profile",
    "search_prediction_markets",
]
