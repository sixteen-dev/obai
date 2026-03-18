"""OBaI - Multi-agent financial research assistant.

This package provides client-agnostic agents that can be used by Discord bots,
web APIs, mobile apps, or any other client interface.

Architecture:
    - Central Hub Agent: Routes queries to specialist agents
    - Fundamentals Agent: Company financials and ratios
    - Market Data Agent: Prices, quotes, and technical indicators
    - Events/News Agent: News, earnings, and dividend calendars
    - Options Agent: Options chains and Greeks analysis (Polygon.io)
    - Screener Agent: Stock screening and ticker discovery
"""

from .base_agent import BaseAgent
from .central_hub_agent import CentralHubAgent, create_central_hub
from .config import AgentConfig, get_config, reset_config
from .events_news_agent import EventsNewsAgent, create_events_news_agent
from .fundamentals_agent import FundamentalsAgent, create_fundamentals_agent
from .market_data_agent import MarketDataAgent, create_market_data_agent
from .options_agent import OptionsAgent, create_options_agent
from .screener_agent import ScreenerAgent, create_screener_agent

__version__ = "0.1.0"

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
    "ScreenerAgent",
    # Central Hub
    "CentralHubAgent",
    # Convenience Functions
    "create_fundamentals_agent",
    "create_market_data_agent",
    "create_events_news_agent",
    "create_options_agent",
    "create_screener_agent",
    "create_central_hub",
]
