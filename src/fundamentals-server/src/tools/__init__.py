"""MCP tools for fundamentals analysis."""

from .vector_search import search_fundamentals
from .fundamentals import (
    get_analyst_estimates,
    get_analyst_outlook,
    get_company_profile,
    get_company_rating,
    get_financial_ratios,
    get_fundamentals,
    get_insider_trades,
    get_key_metrics,
    get_price_target_summary,
    get_revenue_segments,
    get_sec_filings,
    get_valuation_metrics,
)

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
    "get_revenue_segments",
    # Consolidated tools
    "get_valuation_metrics",
    "get_analyst_outlook",
    "search_fundamentals",
]
