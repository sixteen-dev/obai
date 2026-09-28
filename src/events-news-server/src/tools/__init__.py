"""MCP tools for events and news."""

from .congress import get_congress_trades
from .dividends import get_dividends
from .earnings import get_earnings, get_earnings_calendar
from .news import search_market_news

__all__ = [
    "search_market_news",
    "get_earnings",
    "get_earnings_calendar",
    "get_dividends",
    "get_congress_trades",
]
