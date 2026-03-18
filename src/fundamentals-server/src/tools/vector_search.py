"""Vector search tools for educational content."""

from typing import Any

from ..clients import QdrantVectorClient
from ..logging_config import get_logger, log_api_call, log_error, log_tool_invocation

logger = get_logger(__name__)


async def search_fundamentals(
    query: str,
    top_k: int = 5,
    source_filter: str | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Search educational content about financial fundamentals.

    Args:
        query: Natural language query (e.g., "What are option strategies?")
        top_k: Number of results to return per page
        source_filter: Optional filter by PDF source name
        offset: Number of results to skip (for pagination)

    Returns:
        Search results with relevant educational content and pagination metadata

    Raises:
        Exception: If vector search fails
    """
    log_tool_invocation(
        logger,
        "search_fundamentals",
        {"query": query, "top_k": top_k, "source_filter": source_filter},
    )

    try:
        async with QdrantVectorClient() as vectors:
            # Build metadata filter if source specified
            filter_metadata = None
            if source_filter:
                filter_metadata = {"source": source_filter}
                logger.info("applying_source_filter", source_filter=source_filter)

            # Fetch extra results to support pagination
            fetch_count = offset + top_k + 1  # +1 to check if more results exist
            log_api_call(
                logger,
                "qdrant",
                "query_points",
                {"query": query, "top_k": fetch_count, "filter": filter_metadata},
            )
            all_results = await vectors.search(query, fetch_count, filter_metadata)

            # Apply offset and limit
            paginated_results = all_results[offset : offset + top_k]
            has_more = len(all_results) > offset + top_k

            logger.info(
                "vector_search_complete",
                query=query,
                total_fetched=len(all_results),
                returned_count=len(paginated_results),
                source_filter=source_filter,
                offset=offset,
            )

            formatted_results = {
                "query": query,
                "results": [
                    {
                        "source": r["metadata"].get("source", "Unknown"),
                        "text": r["metadata"].get("text", ""),
                        "element_type": r["metadata"].get("element_type", ""),
                        "page_number": r["metadata"].get("page_number", ""),
                        "relevance_score": 1 - r.get("distance", 1),
                    }
                    for r in paginated_results
                ],
                "pagination": {
                    "limit": top_k,
                    "offset": offset,
                    "returned": len(paginated_results),
                    "has_more": has_more,
                    "next_offset": offset + top_k if has_more else None,
                },
            }

            logger.info("tool_execution_complete", tool="search_fundamentals")
            return formatted_results

    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "search_fundamentals", "query": query, "top_k": top_k},
        )
        raise
