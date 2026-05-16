"""MCP server for the OBaI knowledge-base corpus.

Phase 1: bootstrap + liveness/readiness probes only. Tools land in Phase 3.
"""

import asyncio
import sqlite3
import time

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import Settings, get_settings, load_settings
from .logging_config import configure_logging, get_logger, log_error
from .tools import get_corpus_entry, list_categories, search_corpus

_server_start_time: float = time.time()

configure_logging()
logger = get_logger(__name__)


def bootstrap() -> Settings:
    """Load settings from environment variables.

    Returns:
        Application settings.

    Raises:
        Exception: If bootstrap fails (logged before re-raise).
    """
    logger.info("bootstrap_started", server="knowledge-base-server")
    try:
        settings = load_settings()
        logger.info(
            "settings_loaded",
            server=settings.server_name,
            version=settings.server_version,
            corpus_db_path=str(settings.corpus_db_path),
            transport=settings.transport,
            port=settings.port,
        )
        return settings
    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


mcp = FastMCP("knowledge-base-server", version=__version__)
logger.info("mcp_server_initialized", name="knowledge-base-server", version=__version__)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Liveness probe: is the server process running?"""
    try:
        s = get_settings()
        uptime = time.time() - _server_start_time
        return JSONResponse(
            {
                "status": "alive",
                "service": s.server_name,
                "version": s.server_version,
                "uptime_seconds": round(uptime, 2),
            }
        )
    except RuntimeError:
        return JSONResponse(
            {
                "status": "starting",
                "service": "knowledge-base-server",
                "version": __version__,
            }
        )


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_check_ready(_request: Request) -> JSONResponse:
    """Readiness probe: is the corpus DB queryable?

    Returns 200 when `corpus.db` exists and a trivial SELECT against
    `corpus_entries` succeeds; 503 otherwise.
    """
    try:
        s = get_settings()
    except RuntimeError:
        return JSONResponse(
            {"status": "not_ready", "reason": "Settings not loaded"},
            status_code=503,
        )

    if not s.corpus_db_path.is_file():
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": f"corpus.db not found at {s.corpus_db_path}",
                "service": s.server_name,
            },
            status_code=503,
        )

    try:
        conn = sqlite3.connect(
            f"file:{s.corpus_db_path}?mode=ro",
            uri=True,
            timeout=2.0,
        )
        try:
            count = conn.execute("SELECT COUNT(*) FROM corpus_entries").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("readiness_db_probe_failed", error=str(exc))
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": f"corpus.db unreadable: {exc}",
                "service": s.server_name,
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "status": "ready",
            "service": s.server_name,
            "version": s.server_version,
            "corpus_entries": count,
        }
    )


# =============================================================================
# MCP Tools
# =============================================================================


@mcp.tool(
    annotations={
        "title": "Search Corpus",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def kb_search_corpus_tool(
    query: str | None = None,
    entry_type: str | None = None,
    category: str | None = None,
    asset_class: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    """Search the knowledge-base corpus for strategies and concepts.

    Use when the hub needs to ground a named strategy (e.g. "wheel", "VRP harvest"),
    resolve market-vocabulary terms (e.g. "contango", "low dispersion"), or surface
    candidate strategies for a vague universe-flavored request. Results are summary
    records; call `kb_get_corpus_entry_tool` for full content.

    Args:
        query: Free-text search across canonical name, aliases, body, and definition.
        entry_type: "strategy" or "concept" to restrict; omit for both.
        category: Restrict to a single category (e.g. "momentum", "vol", "regimes").
        asset_class: Restrict to entries tagged with this asset class.
        limit: Max results, 1-100. Default 10.
    """
    et: object = entry_type
    if entry_type not in (None, "strategy", "concept"):
        return {"error": f"entry_type must be 'strategy' or 'concept', got {entry_type!r}"}
    return search_corpus(
        query=query,
        entry_type=et,  # type: ignore[arg-type]
        category=category,
        asset_class=asset_class,
        limit=limit,
    )


@mcp.tool(
    annotations={
        "title": "Get Corpus Entry",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def kb_get_corpus_entry_tool(entry_id: str) -> dict[str, object]:
    """Fetch a single corpus entry's full record including the markdown body.

    Use after `kb_search_corpus_tool` when the hub needs the complete strategy or
    concept content (thesis, signal intuition, construction sketch, failure modes,
    references).

    Args:
        entry_id: snake_case id from a prior search result.
    """
    return get_corpus_entry(entry_id=entry_id)


@mcp.tool(
    annotations={
        "title": "List Categories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def kb_list_categories_tool() -> dict[str, object]:
    """List corpus categories grouped by entry_type with entry counts.

    Use to browse what's available before issuing a search.
    """
    return list_categories()


async def main() -> None:
    """Bootstrap settings and run the MCP server."""
    try:
        settings = bootstrap()

        cors_middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
        ]

        await mcp.run_async(
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
            path="/mcp",
            stateless_http=True,
            middleware=cors_middleware,
        )
    except Exception as e:
        log_error(logger, e, context={"event": "server_failed"})
        raise


if __name__ == "__main__":
    asyncio.run(main())
