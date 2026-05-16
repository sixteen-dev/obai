"""Knowledge-base corpus tools: search, fetch one, list categories.

These are thin wrappers around `corpus_db`; serialization to plain dicts (the
MCP wire format) happens here so the FastMCP `@mcp.tool` registrations stay
side-effect-free.
"""

from typing import Any, Literal

from .. import corpus_db
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_utils import domain_error, truncate_response

logger = get_logger(__name__)


def search_corpus(
    query: str | None = None,
    entry_type: Literal["strategy", "concept"] | None = None,
    category: str | None = None,
    asset_class: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search the corpus for strategies and concepts.

    Used by the Hub's `knowledge_base_lookup` specialist to resolve trading
    vocabulary or surface candidate strategies. When `query` is provided, FTS5
    bm25 ranking is applied; otherwise results are sorted by canonical name.

    Args:
        query: Free-text search across canonical name, aliases, body, and
            definition. Phrase-matched (no DSL).
        entry_type: Restrict to "strategy" or "concept". Omit to include both.
        category: Restrict to a single category (e.g., "momentum", "regimes").
        asset_class: Restrict to entries that tag this asset class.
        limit: Maximum results (1-100; default 10).

    Returns:
        Dict with a `results` list of compact entry summaries.
    """
    s = get_settings()
    try:
        summaries = corpus_db.search(
            db_path=s.corpus_db_path,
            query=query,
            entry_type=entry_type,
            category=category,
            asset_class=asset_class,
            limit=limit,
        )
    except corpus_db.CorpusDBError as exc:
        log_error(logger, exc, context={"event": "search_corpus_db_error"})
        return domain_error(str(exc))

    payload: dict[str, Any] = {
        "results": [item.model_dump() for item in summaries],
        "count": len(summaries),
    }
    result = truncate_response(payload)
    assert isinstance(result, dict)
    return result


def get_corpus_entry(entry_id: str) -> dict[str, Any]:
    """Fetch the full corpus entry for a given id.

    Args:
        entry_id: Entry id (snake_case, e.g., "openap_mom12m" or "contango").

    Returns:
        Full entry record including frontmatter fields and the markdown body,
        or an error dict if the id is unknown.
    """
    s = get_settings()
    try:
        entry = corpus_db.get_entry(s.corpus_db_path, entry_id)
    except corpus_db.CorpusDBError as exc:
        log_error(logger, exc, context={"event": "get_corpus_entry_db_error"})
        return domain_error(str(exc))

    if entry is None:
        return domain_error(f"no corpus entry with id {entry_id!r}", entry_id=entry_id)
    result = truncate_response(entry.model_dump())
    assert isinstance(result, dict)
    return result


def list_categories() -> dict[str, Any]:
    """List available categories, grouped by entry_type, with counts.

    Returns:
        Dict with `strategies` and `concepts` arrays of {category, count}.
    """
    s = get_settings()
    try:
        index = corpus_db.list_categories(s.corpus_db_path)
    except corpus_db.CorpusDBError as exc:
        log_error(logger, exc, context={"event": "list_categories_db_error"})
        return domain_error(str(exc))
    return index.model_dump()
