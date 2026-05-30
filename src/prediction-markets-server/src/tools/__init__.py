"""Prediction market tool implementations."""

from .backtest import backtest_prediction_setup
from .backtest_rule import backtest_prediction_rule
from .calibration import analyze_prediction_calibration
from .discovery import explore_trending_markets, get_market_details, search_prediction_markets
from .empirical_kelly import estimate_empirical_kelly
from .flow import get_top_holders, get_trade_flow
from .historical import ensure_prediction_market_history
from .longshot import analyze_longshot_bias
from .market_edge import estimate_market_edge
from .market_state import compare_prediction_markets, get_market_snapshot, get_price_history
from .monte_carlo_risk import monte_carlo_prediction_risk
from .wallets import get_trader_leaderboard, get_wallet_activity, get_wallet_profile

__all__ = [
    "analyze_longshot_bias",
    "analyze_prediction_calibration",
    "backtest_prediction_rule",
    "backtest_prediction_setup",
    "compare_prediction_markets",
    "ensure_prediction_market_history",
    "estimate_empirical_kelly",
    "estimate_market_edge",
    "explore_trending_markets",
    "get_market_details",
    "get_market_snapshot",
    "get_price_history",
    "get_top_holders",
    "get_trade_flow",
    "get_trader_leaderboard",
    "get_wallet_activity",
    "get_wallet_profile",
    "monte_carlo_prediction_risk",
    "search_prediction_markets",
]
