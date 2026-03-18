"""Semantic caching for agent responses using Redis LangCache.

Provides session-scoped caching to avoid redundant tool calls
for follow-up questions within a conversation.
"""

from core_agents.cache.client import QueryCache
from core_agents.config import CacheConfig, get_cache_config, reset_cache_config

__all__ = ["QueryCache", "CacheConfig", "get_cache_config", "reset_cache_config"]
