"""MCP tools for fundamentals analysis."""

from .fundamentals import (
    get_analyst_estimates,
    get_analyst_outlook,
    get_company_profile,
    get_company_rating,
    get_financial_ratios,
    get_fundamentals,
    get_insider_trades,
    get_insider_trading_statistics,
    get_key_metrics,
    get_price_target_summary,
    get_revenue_segments,
    get_sec_filings,
    get_valuation_metrics,
)
from .vector_search import search_fundamentals

__all__ = [
    "get_fundamentals",
    "get_company_profile",
    "get_key_metrics",
    "get_financial_ratios",
    "get_analyst_estimates",
    "get_price_target_summary",
    "get_company_rating",
    "get_sec_filings",
    "get_insider_trades",
    "get_insider_trading_statistics",
    "get_revenue_segments",
    "search_fundamentals",
    # Consolidated tools
    "get_valuation_metrics",
    "get_analyst_outlook",
]
