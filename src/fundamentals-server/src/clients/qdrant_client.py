"""Qdrant vector client for educational context search."""

from typing import Any

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from ..config import get_settings
from ..utils import retry_async


class QdrantVectorClient:
    """Client for Qdrant semantic search."""

    def __init__(self) -> None:
        """Initialize Qdrant vector client."""
        settings = get_settings()
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.qdrant: AsyncQdrantClient | None = None
        self.settings = settings

    async def __aenter__(self) -> "QdrantVectorClient":
        """Async context manager entry."""
        self.qdrant = AsyncQdrantClient(url=self.settings.qdrant_url)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self.qdrant:
            await self.qdrant.close()

    @retry_async(max_attempts=3, initial_delay=0.5, backoff=2.0, jitter=0.25)
    async def create_embedding(self, text: str) -> list[float]:
        """Create embedding for text using OpenAI.

        Args:
            text: Text to embed

        Returns:
            Embedding vector

        Raises:
            Exception: If embedding creation fails
        """
        response = await self.openai_client.embeddings.create(
            model=self.settings.embedding_model,
            input=text,
            dimensions=self.settings.embedding_dimension,
        )
        embedding: list[float] = response.data[0].embedding
        return embedding

    @retry_async(max_attempts=3, initial_delay=0.5, backoff=2.0, jitter=0.25)
    async def search(
        self, query: str, top_k: int = 3, filter_metadata: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Search for similar vectors using semantic query.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            filter_metadata: Optional metadata filter

        Returns:
            List of matching results with metadata and distance.
            Distance is cosine distance (lower = more similar).

        Raises:
            RuntimeError: If client not initialized via context manager
        """
        if not self.qdrant:
            raise RuntimeError("QdrantVectorClient not initialized - use async context manager")

        query_embedding = await self.create_embedding(query)

        query_filter = None
        if filter_metadata:
            query_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filter_metadata.items()
                ]
            )

        points = await self.qdrant.query_points(
            collection_name=self.settings.qdrant_collection,
            query=query_embedding,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )

        # Convert Qdrant response to match existing format.
        # Qdrant cosine score is similarity (0-1, higher = more similar).
        # Consumers expect distance (lower = more similar), so: distance = 1 - score.
        results: list[dict[str, Any]] = []
        for point in points.points:
            results.append(
                {
                    "key": str(point.id),
                    "distance": 1 - point.score,
                    "metadata": point.payload or {},
                }
            )

        return results

    async def ensure_collection(self) -> None:
        """Create collection if it doesn't exist.

        Used by the seed script, not during normal operation.

        Raises:
            RuntimeError: If client not initialized via context manager
        """
        if not self.qdrant:
            raise RuntimeError("QdrantVectorClient not initialized - use async context manager")

        exists = await self.qdrant.collection_exists(self.settings.qdrant_collection)
        if not exists:
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=self.settings.embedding_dimension,
                    distance=Distance.COSINE,
                ),
            )

    async def upsert_points(self, points: list[PointStruct]) -> None:
        """Upsert points into the collection.

        Used by the seed script for batch loading.

        Args:
            points: List of PointStruct to upsert

        Raises:
            RuntimeError: If client not initialized via context manager
        """
        if not self.qdrant:
            raise RuntimeError("QdrantVectorClient not initialized - use async context manager")

        await self.qdrant.upsert(
            collection_name=self.settings.qdrant_collection,
            points=points,
        )

    async def count(self) -> int:
        """Get the number of points in the collection.

        Returns:
            Number of vectors in the collection

        Raises:
            RuntimeError: If client not initialized via context manager
        """
        if not self.qdrant:
            raise RuntimeError("QdrantVectorClient not initialized - use async context manager")

        result = await self.qdrant.count(collection_name=self.settings.qdrant_collection)
        return result.count
