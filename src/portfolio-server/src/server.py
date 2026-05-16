"""Portfolio MCP Server - Main entry point."""

import asyncio
from decimal import Decimal
from typing import Any

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients import FMPClient
from .config import Settings, load_settings
from .engine import compute_allocation_breakdown, compute_portfolio_risk
from .logging_config import configure_logging, get_logger, log_error
from .models import AssetType, Position, WeightType
from .response_utils import format_api_error, truncate_response
from .tools import (
    calculate_effective_exposure,
    generate_concentration_flags,
    parse_positions,
)

# Configure logging early
configure_logging()
logger = get_logger(__name__)

# Initialize FastMCP server
mcp = FastMCP("portfolio-server", version=__version__)
logger.info("mcp_server_initialized", name="portfolio-server", version=__version__)

# Global FMP client (initialized in bootstrap)
_fmp_client: FMPClient | None = None


def get_fmp_client() -> FMPClient:
    """Get the initialized FMP client.

    Returns:
        FMP client instance.

    Raises:
        RuntimeError: If client not initialized.

    """
    if _fmp_client is None:
        msg = "FMP client not initialized - call bootstrap() first"
        raise RuntimeError(msg)
    return _fmp_client


# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "title": "Parse Portfolio Positions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def portfolio_parse_positions_tool(
    text: str,
    normalize: bool = True,
) -> dict[str, Any]:
    """Parse free-form text into structured portfolio positions.

    Supports multiple input formats:
    - Percentages: "AAPL 40%, QQQ 35%, BND 25%"
    - Decimals: "AAPL 0.40, QQQ 0.35, BND 0.25"
    - Shares: "100 shares AAPL, 50 shares MSFT" (note: needs prices for weighting)
    - Dollars: "$50,000 AAPL, $30,000 QQQ"
    - Mixed: "AAPL 40%, BND 30%, CASH 30%"

    Automatically detects asset types (stock, ETF, bond ETF, cash).

    Args:
        text: Portfolio description in any supported format.
        normalize: Whether to normalize weights to sum to 100%. Default True.

    Returns:
        Parsed portfolio with positions, weights, asset types, and any warnings.

    """
    try:
        result = parse_positions(text=text, normalize=normalize)
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_parse_positions_tool"})
        return format_api_error(e)


@mcp.tool(
    annotations={
        "title": "Expand ETF Holdings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def portfolio_expand_etf_holdings_tool(
    etf_symbol: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Get the underlying holdings of an ETF.

    Fetches the constituent holdings of an ETF from Financial Modeling Prep API.
    Useful for look-through analysis to see actual stock exposures.

    Args:
        etf_symbol: ETF ticker symbol (e.g., "SPY", "QQQ", "VTI").
        limit: Maximum number of holdings to return (default 50, max 500).

    Returns:
        ETF holdings with symbols, names, weights, and metadata.
        Also includes ETF info (expense ratio, AUM, sector weights).

    """
    try:
        client = get_fmp_client()

        # Fetch holdings and info in parallel
        holdings, info = await asyncio.gather(
            client.get_etf_holdings(etf_symbol.upper()),
            client.get_etf_info(etf_symbol.upper()),
        )

        if holdings is None:
            return {
                "isError": True,
                "error": f"ETF holdings unavailable for {etf_symbol}",
                "error_type": "data_unavailable",
                "suggestion": (
                    "This ETF may not be supported or data may be temporarily unavailable"
                ),
            }

        # Clamp display limit so negative slices don't return surprising
        # holdings and very large requests don't blow up response size.
        effective_limit = max(1, min(int(limit), 500))
        limited_holdings = holdings[:effective_limit]

        # Format response
        result = {
            "etf_symbol": etf_symbol.upper(),
            "holdings_count": len(holdings),
            "returned_count": len(limited_holdings),
            "holdings": [
                {
                    "symbol": h.asset_symbol,
                    "name": h.name,
                    "weight_percent": float(h.weight_percentage),
                    "market_value": float(h.market_value) if h.market_value else None,
                    "shares": h.shares,
                }
                for h in limited_holdings
            ],
            "data_freshness": holdings[0].updated_at if holdings else None,
        }

        if info:
            result["etf_info"] = {
                "name": info.name,
                "expense_ratio": float(info.expense_ratio),
                "aum": float(info.aum),
                "holdings_count": info.holdings_count,
                "inception_date": info.inception_date,
                "sector_weights": {k: float(v) for k, v in info.sector_weights.items()},
            }

        return truncate_response(result)

    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_expand_etf_holdings_tool"})
        return format_api_error(e)


async def _fetch_etf_holdings(
    etf_positions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Fetch ETF holdings for all ETF positions in parallel.

    Args:
        etf_positions: List of ETF positions from parsed portfolio.

    Returns:
        Map of ETF symbol to list of holdings.

    """
    if not etf_positions:
        return {}

    client = get_fmp_client()
    holdings_tasks = [client.get_etf_holdings(p["symbol"]) for p in etf_positions]
    holdings_results = await asyncio.gather(*holdings_tasks, return_exceptions=True)

    etf_holdings_map: dict[str, list[dict[str, Any]]] = {}
    for pos, result in zip(etf_positions, holdings_results, strict=True):
        if isinstance(result, Exception):
            logger.warning("etf_expansion_failed", symbol=pos["symbol"], error=str(result))
        elif isinstance(result, list) and result:
            # Capture every holding for math. Broad-market ETFs like VTI/VT
            # have thousands of constituents and truncating to 100 would
            # treat them as partial portfolios when computing concentration
            # and sector exposure. Response-side truncation lives in the
            # tool that renders ETF holdings to the user.
            etf_holdings_map[pos["symbol"]] = [
                {
                    "symbol": h.asset_symbol,
                    "name": h.name,
                    "weight_percent": float(h.weight_percentage),
                }
                for h in result
            ]

    return etf_holdings_map


@mcp.tool(
    annotations={
        "title": "Calculate Effective Exposure",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def portfolio_effective_exposure_tool(
    text: str,
    concentration_threshold: float = 25.0,
    top_n_threshold: float = 60.0,
) -> dict[str, Any]:
    """Calculate effective exposure including look-through for ETFs.

    Parses portfolio positions, expands ETF holdings, and calculates the true
    exposure to each underlying stock. Flags concentration risks.

    This is an all-in-one tool that combines parsing + ETF expansion + analysis.
    Use this for portfolio visualization and risk analysis.

    Args:
        text: Portfolio description (e.g., "AAPL 30%, QQQ 40%, MSFT 20%, cash 10%").
        concentration_threshold: Flag stocks exceeding this % (default 25%).
        top_n_threshold: Flag if top 3 holdings exceed this % (default 60%).

    Returns:
        Effective exposure per stock, concentration flags, and portfolio summary.

    """
    try:
        # Step 1: Parse positions
        parse_result = parse_positions(text=text, normalize=True)
        if parse_result.get("isError"):
            return parse_result

        portfolio_data = parse_result.get("portfolio", {})
        positions = portfolio_data.get("positions", [])
        if not positions:
            return {"isError": True, "error": "No positions found", "error_type": "parse_error"}

        # Step 2: Fetch ETF holdings in parallel
        etf_positions = [p for p in positions if p.get("asset_type") == "etf"]
        etf_holdings_map = await _fetch_etf_holdings(etf_positions)

        # Step 3: Calculate effective exposure using helper
        effective_exposure = calculate_effective_exposure(positions, etf_holdings_map)

        # Step 4: Generate concentration flags using helper
        concentration_flags = generate_concentration_flags(
            effective_exposure, concentration_threshold, top_n_threshold
        )

        # Step 5: Build response
        stock_count = len([p for p in positions if p.get("asset_type") in ("stock", "unknown")])
        result = {
            "effective_exposure": effective_exposure[:20],
            "total_stocks_exposed": len(effective_exposure),
            "concentration_flags": concentration_flags,
            "concentration_risk": len(concentration_flags) > 0,
            "portfolio_summary": {
                "direct_stocks": stock_count,
                "etfs": len(etf_positions),
                "etfs_expanded": len(etf_holdings_map),
                "positions_parsed": len(positions),
            },
            "etf_expansion": {
                etf: {"holdings_count": len(holdings), "top_5": holdings[:5]}
                for etf, holdings in etf_holdings_map.items()
            },
            "parsing_warnings": parse_result.get("warnings", []),
        }

        return truncate_response(result)

    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_effective_exposure_tool"})
        return format_api_error(e)


@mcp.tool(
    annotations={
        "title": "Get Treasury Rates",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def portfolio_get_treasury_rates_tool() -> dict[str, Any]:
    """Get current US Treasury rates for all maturities.

    Fetches the latest Treasury yields from 1-month to 30-year maturities.
    The 3-month rate is commonly used as the risk-free rate for Sharpe ratio calculations.

    Returns:
        Treasury rates for each maturity (month1, month3, year1, year2, year5, year10, etc.)

    """
    try:
        client = get_fmp_client()
        rates = await client.get_treasury_rates()

        if rates is None:
            return {
                "isError": True,
                "error": "Treasury rates unavailable",
                "error_type": "data_unavailable",
            }

        # Format for readability
        result = {
            "rates": {
                "1_month": rates.get("month1"),
                "2_month": rates.get("month2"),
                "3_month": rates.get("month3"),
                "6_month": rates.get("month6"),
                "1_year": rates.get("year1"),
                "2_year": rates.get("year2"),
                "3_year": rates.get("year3"),
                "5_year": rates.get("year5"),
                "7_year": rates.get("year7"),
                "10_year": rates.get("year10"),
                "20_year": rates.get("year20"),
                "30_year": rates.get("year30"),
            },
            "date": rates.get("date"),
            "note": (
                "Rates are in percent (e.g., 5.45 = 5.45%). "
                "3-month rate commonly used as risk-free rate."
            ),
        }

        return truncate_response(result)

    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_get_treasury_rates_tool"})
        return format_api_error(e)


@mcp.tool(
    annotations={
        "title": "Portfolio Risk Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def portfolio_risk_analysis_tool(
    text: str,
    benchmark: str = "SPY",
    lookback_days: int = 252,
) -> dict[str, Any]:
    """Compute portfolio risk metrics from held instrument price history.

    Calculates volatility, Sharpe/Sortino ratios, beta, max drawdown, VaR,
    and other risk statistics for a portfolio described in free-form text.

    Risk metrics use HELD instruments (the tickers you actually own), not
    look-through exposure. This gives risk based on actual tradeable positions.

    Args:
        text: Portfolio description (e.g., "AAPL 40%, QQQ 35%, BND 25%").
        benchmark: Benchmark symbol for beta/correlation (default "SPY").
        lookback_days: Trading days to look back (default 252 = ~1 year).

    Returns:
        Risk metrics including volatility, Sharpe, beta, drawdown, and more.

    """
    try:
        # Step 1: Parse positions
        parse_result = parse_positions(text=text, normalize=True)
        if parse_result.get("isError"):
            return parse_result

        portfolio_data = parse_result.get("portfolio", {})
        raw_positions = portfolio_data.get("positions", [])
        if not raw_positions:
            return {"isError": True, "error": "No positions found", "error_type": "parse_error"}

        # Convert raw position dicts back to Position objects
        positions = _dicts_to_positions(raw_positions)

        # Step 2: Get risk-free rate
        client = get_fmp_client()
        rfr = await client.get_risk_free_rate()

        # Step 3: Compute risk metrics
        risk_metrics = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark=benchmark,
            lookback_days=lookback_days,
            risk_free_rate=float(rfr),
        )

        # Step 4: Build response
        result: dict[str, Any] = {
            "risk_metrics": risk_metrics.to_dict(),
            "benchmark": benchmark,
            "risk_free_rate": float(rfr),
            "positions_analyzed": len(positions),
            "parsing_warnings": parse_result.get("warnings", []),
        }

        return truncate_response(result)

    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_risk_analysis_tool"})
        return format_api_error(e)


@mcp.tool(
    annotations={
        "title": "Portfolio Allocation Breakdown",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def portfolio_allocation_breakdown_tool(
    text: str,
) -> dict[str, Any]:
    """Compute portfolio allocation breakdown with look-through analysis.

    Expands ETFs to underlying holdings and computes sector exposure,
    asset class distribution, concentration metrics, and ETF attribution.

    Allocation uses LOOK-THROUGH exposure (expanding ETFs to underlying stocks).
    This shows where your money actually ends up.

    Args:
        text: Portfolio description (e.g., "AAPL 40%, QQQ 35%, BND 25%").

    Returns:
        Allocation breakdown by ticker, sector, asset class, with concentration metrics.

    """
    try:
        # Step 1: Parse positions
        parse_result = parse_positions(text=text, normalize=True)
        if parse_result.get("isError"):
            return parse_result

        portfolio_data = parse_result.get("portfolio", {})
        raw_positions = portfolio_data.get("positions", [])
        if not raw_positions:
            return {"isError": True, "error": "No positions found", "error_type": "parse_error"}

        # Convert raw position dicts back to Position objects
        positions = _dicts_to_positions(raw_positions)

        # Step 2: Fetch ETF holdings
        etf_positions = [p for p in raw_positions if p.get("asset_type") == "etf"]
        etf_holdings_map = await _fetch_etf_holdings(etf_positions)

        # Step 3: Compute allocation breakdown
        client = get_fmp_client()
        breakdown = await compute_allocation_breakdown(
            positions=positions,
            etf_holdings_map=etf_holdings_map,
            fmp_client=client,
        )

        # Step 4: Build response
        result: dict[str, Any] = {
            "allocation": breakdown.to_dict(),
            "positions_analyzed": len(positions),
            "etfs_expanded": len(etf_holdings_map),
            "parsing_warnings": parse_result.get("warnings", []),
        }

        return truncate_response(result)

    except Exception as e:
        log_error(logger, e, context={"tool": "portfolio_allocation_breakdown_tool"})
        return format_api_error(e)


def _dicts_to_positions(raw_positions: list[dict[str, Any]]) -> list[Position]:
    """Convert position dicts from parse_positions back to Position objects.

    Args:
        raw_positions: List of position dicts from parsed portfolio.

    Returns:
        List of Position model objects.

    """
    positions: list[Position] = []
    for raw in raw_positions:
        positions.append(
            Position(
                symbol=raw["symbol"],
                weight=Decimal(str(raw.get("weight", 0))),
                asset_type=AssetType(raw.get("asset_type", "stock")),
                original_input=raw.get("original_input", ""),
                weight_type=WeightType(raw.get("weight_type", "percentage")),
                shares=(Decimal(str(raw["shares"])) if raw.get("shares") is not None else None),
                dollar_value=(
                    Decimal(str(raw["dollar_value"]))
                    if raw.get("dollar_value") is not None
                    else None
                ),
                price_used=(
                    Decimal(str(raw["price_used"])) if raw.get("price_used") is not None else None
                ),
            )
        )
    return positions


# ─────────────────────────────────────────────────────────────────────────────
# Health Checks
# ─────────────────────────────────────────────────────────────────────────────


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Check if the process is running (liveness probe)."""
    return JSONResponse(
        {
            "status": "healthy",
            "service": "portfolio-server",
            "version": __version__,
        }
    )


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_check_ready(_request: Request) -> JSONResponse:
    """Check if service is ready to handle requests (readiness probe)."""
    try:
        client = get_fmp_client()
        fmp_healthy = await client.health_check(timeout=5.0)

        if fmp_healthy:
            return JSONResponse(
                {
                    "status": "ready",
                    "service": "portfolio-server",
                    "version": __version__,
                    "dependencies": {"fmp_api": "healthy"},
                }
            )
        else:
            return JSONResponse(
                {
                    "status": "degraded",
                    "service": "portfolio-server",
                    "version": __version__,
                    "dependencies": {"fmp_api": "unhealthy"},
                },
                status_code=503,
            )
    except Exception as e:
        return JSONResponse(
            {
                "status": "unhealthy",
                "service": "portfolio-server",
                "error": str(e),
            },
            status_code=503,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap & Main
# ─────────────────────────────────────────────────────────────────────────────


def bootstrap() -> Settings:
    """Bootstrap server by loading settings and initializing clients.

    Returns:
        Loaded settings instance.

    """
    global _fmp_client  # noqa: PLW0603

    logger.info("bootstrap_started", server="portfolio-server")

    try:
        logger.info("loading_settings", source="env")
        settings = load_settings()
        logger.info("settings_loaded")

        # Initialize FMP client
        logger.info("initializing_fmp_client")
        _fmp_client = FMPClient(settings)

        logger.info("bootstrap_complete")
        return settings

    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


async def main() -> None:
    """Start the portfolio server."""
    settings = bootstrap()

    cors_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    logger.info(
        "server_starting",
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
    )

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
