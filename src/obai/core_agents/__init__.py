"""OBaI - Multi-agent financial research assistant.

This package provides client-agnostic agents that can be used by Discord bots,
web APIs, mobile apps, or any other client interface.

Architecture:
    - Central Hub Agent: Routes queries to specialist agents
    - Fundamentals Agent: Company financials and ratios
    - Market Data Agent: Prices, quotes, and technical indicators
    - Events/News Agent: News, earnings, and dividend calendars
    - Options Agent: Options chains and Greeks analysis (Massive)
    - Screener Agent: Stock screening and ticker discovery
    - Research Agent: Deep company research via Exa semantic search

All agent classes and factories are importable from this package but loaded
lazily to avoid pulling in the entire agent system when only config or a
single submodule is needed (e.g. ``from core_agents.config import get_config``).
"""

from __future__ import annotations

import importlib
from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("obai")

__all__ = [
    # Config
    "AgentConfig",
    "get_config",
    "reset_config",
    # Base class
    "BaseAgent",
    # Specialist Agents
    "FundamentalsAgent",
    "MarketDataAgent",
    "EventsNewsAgent",
    "OptionsAgent",
    "ResearchAgent",
    "ScreenerAgent",
    "PortfolioAgent",
    "StrategyAgent",
    # Central Hub
    "CentralHubAgent",
    # Convenience Functions
    "create_fundamentals_agent",
    "create_market_data_agent",
    "create_events_news_agent",
    "create_options_agent",
    "create_research_agent",
    "create_screener_agent",
    "create_central_hub",
]

_LAZY_IMPORTS: dict[str, str] = {
    "AgentConfig": ".config",
    "get_config": ".config",
    "reset_config": ".config",
    "BaseAgent": ".base_agent",
    "CentralHubAgent": ".central_hub_agent",
    "create_central_hub": ".central_hub_agent",
    "EventsNewsAgent": ".events_news_agent",
    "create_events_news_agent": ".events_news_agent",
    "FundamentalsAgent": ".fundamentals_agent",
    "create_fundamentals_agent": ".fundamentals_agent",
    "MarketDataAgent": ".market_data_agent",
    "create_market_data_agent": ".market_data_agent",
    "OptionsAgent": ".options_agent",
    "create_options_agent": ".options_agent",
    "ResearchAgent": ".research_agent",
    "create_research_agent": ".research_agent",
    "ScreenerAgent": ".screener_agent",
    "create_screener_agent": ".screener_agent",
    "PortfolioAgent": ".portfolio_agent",
    "StrategyAgent": ".strategy_agent",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module = importlib.import_module(_LAZY_IMPORTS[name], __name__)
        value = getattr(module, name)
        globals()[name] = value  # cache for subsequent access
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
