"""FastMCP server for deep company research — 5 tools via Exa.

Design doc: docs/plans/RESEARCH_AGENT_EXA.md
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import Settings, get_settings, load_settings
from .logging_config import configure_logging, get_logger, log_error
from .response_utils import format_api_error, truncate_response
from .tools import (
    research_company_profile,
    research_competitive_landscape,
    research_general,
    research_leadership,
    research_product_sentiment,
)

logger = get_logger(__name__)

mcp = FastMCP("research-server", version=__version__)

_server_start_time = time.time()

# Status codes < 500 are taken as "Exa is reachable"; 5xx means upstream is
# degraded and we should not declare readiness.
HTTP_5XX_BOUNDARY = 500


# -- Tool 1: Company Profile ---------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Research Company Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_company_profile_tool(
    symbol: str,
    company_name: str,
    days_back: int = 180,
) -> dict[str, Any]:
    """Research a company's business model, strategy, products, and market position.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        days_back: How many days of history to search. Default 180.

    Returns:
        Web research results with highlighted excerpts from multiple sources.

    """
    try:
        result = await research_company_profile(symbol, company_name, days_back)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "research_company_profile", "symbol": symbol})
        return format_api_error(exc, "Exa")


# -- Tool 2: Leadership --------------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Research Leadership",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_leadership_tool(
    symbol: str,
    company_name: str,
    person_name: str | None = None,
    days_back: int = 365,
) -> dict[str, Any]:
    """Research CEO/exec track record, leadership changes, and management quality.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        person_name: Specific exec name. Defaults to CEO if omitted.
        days_back: How many days of history to search. Default 365.

    Returns:
        Web research results focused on leadership and management decisions.

    """
    try:
        result = await research_leadership(symbol, company_name, person_name, days_back)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "research_leadership", "symbol": symbol})
        return format_api_error(exc, "Exa")


# -- Tool 3: Product Sentiment -------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Research Product Sentiment",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_product_sentiment_tool(
    symbol: str,
    company_name: str,
    product: str | None = None,
    days_back: int = 90,
) -> dict[str, Any]:
    """Research product/service reception from user reviews, Reddit, and forums.

    Searches user-generated content. Excludes major news domains.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        product: Specific product/service name. Omit for all products.
        days_back: How many days of history to search. Default 90.

    Returns:
        Web research results from user-generated content and review sites.

    """
    try:
        result = await research_product_sentiment(symbol, company_name, product, days_back)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "research_product_sentiment", "symbol": symbol})
        return format_api_error(exc, "Exa")


# -- Tool 4: Competitive Landscape ---------------------------------------------


@mcp.tool(
    annotations={
        "title": "Research Competitive Landscape",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_competitive_landscape_tool(
    symbol: str,
    company_name: str,
    days_back: int = 180,
) -> dict[str, Any]:
    """Research competitors, market share, and competitive positioning.

    Args:
        symbol: Stock ticker (e.g., "AAPL").
        company_name: Full company name (e.g., "Apple Inc").
        days_back: How many days of history to search. Default 180.

    Returns:
        Competitor companies and comparison analysis from web sources.

    """
    try:
        result = await research_competitive_landscape(symbol, company_name, days_back)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "research_competitive_landscape", "symbol": symbol})
        return format_api_error(exc, "Exa")


# -- Tool 5: General Research --------------------------------------------------


@mcp.tool(
    annotations={
        "title": "General Research",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def research_general_tool(
    query: str,
    symbol: str | None = None,
    days_back: int = 180,
) -> dict[str, Any]:
    """Thematic or open-ended qualitative research across web sources.

    Use for structural, thematic, or cross-cutting research questions that
    do not fit a narrower company-profile, leadership, product-sentiment,
    or competitive-landscape tool. Not for breaking news, earnings data,
    filings, insider activity, or simple price and valuation lookups.

    Args:
        query: Free-form research query.
        symbol: Optional stock ticker for context.
        days_back: How many days of history to search. Default 180.

    Returns:
        Research results for the query.

    """
    try:
        result = await research_general(query, symbol, days_back)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "research_general", "query": query})
        return format_api_error(exc, "Exa")


# -- Health Endpoints ----------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Liveness probe — is the server running."""
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
                "service": "research-server",
                "version": __version__,
            }
        )


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_check_ready(_request: Request) -> JSONResponse:
    """Readiness probe — Exa API key set AND upstream reachable.

    Sends a 3-second HTTP probe to ``api.exa.ai`` so we detect (a) DNS
    failures and (b) widespread Exa outages before traffic is routed
    here. We only check TCP/HTTP reachability — not a real search —
    to avoid burning Exa credits on every probe.
    """
    try:
        s = get_settings()
    except RuntimeError:
        return JSONResponse(
            {"status": "not_ready", "reason": "Settings not loaded"},
            status_code=503,
        )

    if not s.exa_api_key:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": "EXA_API_KEY not configured",
                "service": s.server_name,
            },
            status_code=503,
        )

    try:
        async with httpx.AsyncClient(timeout=3.0) as probe:
            resp = await probe.get("https://api.exa.ai/")
        # Any HTTP response (even 404) means we reached Exa. A network
        # error throws, which the except below catches.
        upstream_ok = resp.status_code < HTTP_5XX_BOUNDARY
    except httpx.HTTPError as exc:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": f"Exa upstream unreachable: {exc!s}",
                "service": s.server_name,
            },
            status_code=503,
        )

    if not upstream_ok:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": f"Exa upstream returned {resp.status_code}",
                "service": s.server_name,
            },
            status_code=503,
        )

    return JSONResponse({"status": "ready", "service": s.server_name})


# -- Bootstrap & Main ----------------------------------------------------------


def bootstrap() -> Settings:
    """Bootstrap server by loading settings."""
    logger.info("bootstrap_started", server="research-server")
    try:
        settings = load_settings()
        logger.info(
            "bootstrap_complete",
            port=settings.port,
            exa_configured=bool(settings.exa_api_key),
        )
        return settings
    except Exception as exc:
        log_error(logger, exc, context={"event": "bootstrap_failed"})
        raise


async def main() -> None:
    """Start the MCP server."""
    settings = bootstrap()
    configure_logging(settings.log_level)

    cors_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    await mcp.run_async(
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
        path="/mcp",
        stateless_http=True,
        middleware=cors_middleware,
    )


if __name__ == "__main__":
    asyncio.run(main())
