"""LangCache client for semantic query caching.

Provides RAG-style context injection for follow-up questions.
"""

from __future__ import annotations

import logging
from typing import Any

from core_agents.config import CacheConfig

logger = logging.getLogger(__name__)


class QueryCache:
    """Semantic cache client using Redis LangCache.

    Wraps the langcache SDK with semantic caching and
    RAG-style context building for the central hub.

    Example:
        ```python
        from core_agents.cache import QueryCache, get_cache_config

        config = get_cache_config()
        cache = QueryCache(config)

        # Search for similar cached response
        cached = await cache.search("What's NVDA's P/E?")

        if cached:
            # Build RAG context for hub
            augmented = cache.build_rag_context(
                query="What's NVDA's P/E?",
                cached_response=cached,
            )
            # Pass augmented query to hub

        # After agent response, store it
        await cache.store(
            query="Analyze NVDA",
            response="NVDA is trading at...",
        )
        ```
    """

    def __init__(self, config: CacheConfig) -> None:
        """Initialize cache client.

        Args:
            config: Cache configuration from get_cache_config().
        """
        self.config = config
        self._client: Any = None
        self._initialized = False

    async def _ensure_client(self) -> Any:
        """Lazily initialize LangCache client.

        Returns:
            LangCache client instance.

        Raises:
            ImportError: If langcache package not installed.
            RuntimeError: If cache not properly configured.
        """
        if self._client is not None:
            return self._client

        if not self.config.is_configured():
            msg = "LangCache not configured. Set LANGCACHE_ENABLED, SERVER_URL, and API_KEY."
            raise RuntimeError(msg)

        # Lazy-import langcache — it's an optional dep ([project.optional-dependencies] caching).
        from langcache import LangCache

        self._client = LangCache(
            server_url=self.config.server_url,
            cache_id=self.config.cache_id,
            api_key=self.config.api_key,
        )
        self._initialized = True
        logger.info(f"LangCache client initialized (cache_id: {self.config.cache_id})")
        return self._client

    async def search(
        self,
        query: str,
    ) -> str | None:
        """Search for semantically similar cached response.

        Args:
            query: User query to search for.

        Returns:
            Cached response if found with sufficient similarity, else None.
        """
        if not self.config.is_configured():
            return None

        try:
            client = await self._ensure_client()

            result = client.search(
                prompt=query,
                similarity_threshold=self.config.similarity_threshold,
            )

            # Check for hits - SearchResponse is an object, not a dict
            hits = getattr(result, "hits", []) or []
            if hits:
                best_hit = hits[0]
                response: str | None = getattr(best_hit, "response", None)
                similarity = getattr(best_hit, "similarity", 0.0)
                logger.info(f"Cache HIT (similarity: {similarity:.2f})")
                return response

            logger.debug("Cache MISS")
            return None

        except Exception as e:
            logger.warning(f"Cache search failed: {e}")
            return None

    async def store(
        self,
        query: str,
        response: str,
    ) -> bool:
        """Store query-response pair in cache.

        Args:
            query: User query.
            response: Agent response to cache.

        Returns:
            True if stored successfully, False otherwise.
        """
        if not self.config.is_configured():
            return False

        try:
            client = await self._ensure_client()

            client.set(
                prompt=query,
                response=response,
            )
            logger.debug("Cached response")
            return True

        except Exception as e:
            logger.warning(f"Cache store failed: {e}")
            return False

    def build_rag_context(
        self,
        query: str,
        cached_response: str,
    ) -> str:
        """Build RAG-style augmented query with cached context.

        Injects cached data into the query so the hub can decide
        whether to use it or fetch fresh data.

        Args:
            query: Original user query.
            cached_response: Previously cached response.

        Returns:
            Augmented query with cached context.
        """
        return f"""## Session Cache

{cached_response}

## User Query

{query}"""

    async def clear(self) -> bool:
        """Clear all cached entries.

        Returns:
            True if cleared successfully, False otherwise.
        """
        if not self.config.is_configured():
            return False

        try:
            client = await self._ensure_client()
            client.flush()
            logger.info("Cache cleared")
            return True
        except Exception as e:
            logger.warning(f"Cache clear failed: {e}")
            return False
