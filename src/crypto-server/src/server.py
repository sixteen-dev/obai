"""Crypto MCP server entry point."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients import CoinbaseClient
from .config import Settings, load_settings
from .engine import export_artifact, run_bar_backtest, validate_artifact
from .logging_config import configure_logging, get_logger, log_error
from .models import Candle, SourceQuality
from .quality import (
    build_candle_source_quality,
    compute_coverage,
    freshness_seconds,
    normalize_granularity,
    parse_time,
    snap_start_to_available,
)
from .response_utils import format_api_error
from .storage import CryptoStore, canonical_json

logger = get_logger(__name__)

mcp = FastMCP("crypto-server", version=__version__)
_server_start_time = time.time()


@dataclass
class _ServerState:
    settings: Settings | None = None
    coinbase: CoinbaseClient | None = None
    store: CryptoStore | None = None

    def require(self, name: str) -> Any:
        value = getattr(self, name)
        if value is None:
            msg = f"{name} not initialized - call bootstrap() first"
            raise RuntimeError(msg)
        return value


_state = _ServerState()


@mcp.tool(
    annotations={
        "title": "Resolve Coinbase Crypto Product",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def crypto_resolve_symbol(query: str, venue: str = "coinbase") -> dict[str, Any]:
    """Resolve a user query to Coinbase spot product IDs."""
    if venue.lower() != "coinbase":
        return {"isError": True, "error": "v1 supports venue='coinbase' only"}
    try:
        products = await _state.require("coinbase").list_products()
        normalized = query.upper().replace("/", "-").strip()
        exact_matches = [
            product
            for product in products
            if normalized in {product.product_id, product.base_currency_id.upper()}
        ]
        contains_matches = [
            product
            for product in products
            if normalized in product.product_id and product not in exact_matches
        ]
        matches = [*exact_matches, *contains_matches]
        return {
            "query": query,
            "venue": "coinbase",
            "matches": [product.to_dict() for product in matches[:20]],
            "count": len(matches),
            "source_quality": SourceQuality(
                product_id=matches[0].product_id if matches else None,
                latest_observation_at=None,
                limitations=["Coinbase-tradable spot products only"],
                execution_grade=bool(matches),
            ).to_dict(),
        }
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_resolve_symbol", "query": query})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Get Coinbase OHLCV",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def crypto_get_ohlcv(
    product_id: str,
    timeframe: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Get Coinbase OHLCV candles with source-quality metadata."""
    try:
        granularity = normalize_granularity(timeframe)
        start_dt = parse_time(start)
        end_dt = parse_time(end)
        candles, quality = await _load_candles(
            product_id=product_id,
            timeframe=timeframe,
            granularity=granularity,
            start=start_dt,
            end=end_dt,
            execution_grade_required=False,
        )
        return {
            "product_id": product_id.upper(),
            "timeframe": timeframe,
            "granularity": granularity,
            "candles": [candle.to_dict() for candle in candles],
            "source_quality": quality.to_dict(),
        }
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_get_ohlcv", "product_id": product_id})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Get Coinbase Order Book",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def crypto_get_orderbook(product_id: str, depth: int = 50) -> dict[str, Any]:
    """Get Coinbase public order book for a product."""
    try:
        payload = await _state.require("coinbase").get_product_book(product_id, depth=depth)
        pricebook = payload.get("pricebook") or {}
        observed = pricebook.get("time")
        quality = SourceQuality(
            product_id=product_id.upper(),
            latest_observation_at=observed,
            freshness_seconds=(
                freshness_seconds(parse_time(observed)) if isinstance(observed, str) else None
            ),
            limitations=["Snapshot only; historical queue position is not reconstructed"],
            execution_grade=True,
        )
        return {
            "product_id": product_id.upper(),
            "orderbook": payload,
            "source_quality": quality.to_dict(),
        }
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_get_orderbook", "product_id": product_id})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Get Coinbase Latest Trade",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def crypto_get_latest_trade(product_id: str) -> dict[str, Any]:
    """Get latest Coinbase public trade payload."""
    try:
        ticker = await _state.require("coinbase").get_ticker(product_id)
        trades = ticker.get("trades") or []
        observed = trades[0].get("time") if trades and isinstance(trades[0], dict) else None
        return {
            "product_id": product_id.upper(),
            "latest_trade": trades[0] if trades else None,
            "source_quality": _live_quality(product_id, observed).to_dict(),
        }
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_get_latest_trade", "product_id": product_id})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Get Coinbase Latest Quote",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def crypto_get_latest_quote(product_id: str) -> dict[str, Any]:
    """Get Coinbase best bid/ask from the public ticker endpoint."""
    try:
        ticker = await _state.require("coinbase").get_ticker(product_id)
        trades = ticker.get("trades") or []
        observed = trades[0].get("time") if trades and isinstance(trades[0], dict) else None
        return {
            "product_id": product_id.upper(),
            "best_bid": ticker.get("best_bid"),
            "best_ask": ticker.get("best_ask"),
            "source_endpoint": "/products/{product_id}/ticker",
            "source_quality": _live_quality(product_id, observed).to_dict(),
        }
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_get_latest_quote", "product_id": product_id})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Run Coinbase Spot Crypto Backtest",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def crypto_backtest_run_strategy(
    strategy_spec: str,
    data_config: str,
    execution_config: str,
) -> dict[str, Any]:
    """Run a Coinbase spot strategy backtest.

    Args:
        strategy_spec: JSON-encoded strategy specification dict.
        data_config: JSON-encoded data configuration dict.
        execution_config: JSON-encoded execution configuration dict.

    """
    try:
        _strategy_spec: dict[str, Any] = json.loads(strategy_spec)
        _data_config: dict[str, Any] = json.loads(data_config)
        _execution_config: dict[str, Any] = json.loads(execution_config)
        product_id = str(_data_config["product_id"]).upper()
        timeframe = str(_data_config["timeframe"])
        granularity = normalize_granularity(timeframe)
        start = parse_time(_data_config["start"])
        end = parse_time(_data_config["end"])
        candles, quality = await _load_candles(
            product_id=product_id,
            timeframe=timeframe,
            granularity=granularity,
            start=start,
            end=end,
            execution_grade_required=True,
        )
        if quality.blocking_quality_warning:
            return {
                "isError": True,
                "error": "Execution-grade Coinbase data is incomplete",
                "source_quality": quality.to_dict(),
            }
        backtest = run_bar_backtest(
            candles,
            strategy_spec=_strategy_spec,
            execution_config=_execution_config,
        )
        source_quality = quality.to_dict()
        source_quality_fingerprint = _fingerprint(source_quality)
        effective_start = quality.coverage.start if quality.coverage else start.isoformat()
        effective_end = quality.coverage.end if quality.coverage else end.isoformat()
        result = {
            **backtest.result,
            "data_config": {
                "product_id": product_id,
                "venue": "coinbase",
                "timeframe": timeframe,
                "granularity": granularity,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "start": effective_start,
                "end": effective_end,
                "range_adjusted": effective_start != start.isoformat()
                or effective_end != end.isoformat(),
                "data_source_policy": _data_config.get(
                    "data_source_policy", "execution_venue_required"
                ),
            },
            "quality_status": "execution_grade",
            "source_quality": source_quality,
            "source_quality_fingerprint": source_quality_fingerprint,
        }
        job_id = _job_id(_strategy_spec, result["data_config"], _execution_config, source_quality)
        await _state.require("store").store_job(job_id, status="completed", result=result)
        await _state.require("store").store_trade_log(job_id, backtest.trades)
        return {"job_id": job_id, "status": "completed", "result": result}
    except Exception as exc:
        log_error(logger, exc, {"tool": "crypto_backtest_run_strategy"})
        return format_api_error(exc)


@mcp.tool(
    annotations={
        "title": "Get Crypto Backtest Job Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def crypto_backtest_get_job_status(job_id: str) -> dict[str, Any]:
    """Get a stored crypto backtest job."""
    store: CryptoStore = _state.require("store")
    job = await store.get_job(job_id)
    if job is None:
        return {"isError": True, "error": f"Job not found: {job_id}"}
    return job


@mcp.tool(
    annotations={
        "title": "Get Crypto Backtest Trade Log",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def crypto_backtest_get_trade_log(
    job_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get a stored crypto trade log."""
    store: CryptoStore = _state.require("store")
    return await store.get_trade_log(job_id, limit=limit, offset=offset)


@mcp.tool(
    annotations={
        "title": "Validate Crypto Strategy Artifact",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def crypto_strategy_validate_artifact(artifact: str) -> dict[str, Any]:
    """Validate a Coinbase v1 crypto strategy artifact.

    Args:
        artifact: JSON-encoded artifact dict.

    """
    return validate_artifact(json.loads(artifact))


@mcp.tool(
    annotations={
        "title": "Export Crypto Strategy Artifact",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def crypto_strategy_export_artifact(
    job_id: str,
    strategy_spec: str,
    risk_config: str,
    execution_profile: str,
) -> dict[str, Any]:
    """Export a validated Coinbase paper-ledger artifact from a backtest job.

    Args:
        job_id: Stored backtest job identifier.
        strategy_spec: JSON-encoded strategy specification dict.
        risk_config: JSON-encoded risk configuration dict.
        execution_profile: JSON-encoded execution profile dict.

    """
    job = await _state.require("store").get_job(job_id)
    if job is None or job.get("result") is None:
        return {"isError": True, "error": f"Completed job not found: {job_id}"}
    result = job["result"]
    source_quality = result.get("source_quality") or {}
    if result.get("quality_status") != "execution_grade" or source_quality.get(
        "blocking_quality_warning"
    ):
        return {"isError": True, "error": "Backtest is not eligible for artifact export"}
    artifact = export_artifact(
        job_id=job_id,
        strategy_spec=json.loads(strategy_spec),
        risk_config=json.loads(risk_config),
        execution_profile=json.loads(execution_profile),
        backtest_result=result,
    )
    validation = validate_artifact(artifact)
    if not validation["valid"]:
        return {"isError": True, "error": "Artifact validation failed", "validation": validation}
    await _state.require("store").store_artifact(artifact["fingerprint"], artifact)
    return {"artifact": artifact, "validation": validation}


async def _load_candles(
    *,
    product_id: str,
    timeframe: str,
    granularity: str,
    start: Any,
    end: Any,
    execution_grade_required: bool,
) -> tuple[list[Candle], SourceQuality]:
    product = product_id.upper()
    store: CryptoStore = _state.require("store")
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    cached = await store.get_candles(product, granularity, start_ts, end_ts)
    coverage = compute_coverage(
        cached,
        requested_start=start,
        requested_end=end,
        granularity=granularity,
    )
    settings: Settings = _state.require("settings")
    fetch_failed = False
    if coverage.missing_intervals > 0:
        try:
            fetched = await _state.require("coinbase").get_historical_candles(
                product,
                start=start,
                end=end,
                granularity=granularity,
            )
            await store.upsert_candles(fetched, granularity)
            cached = await store.get_candles(product, granularity, start_ts, end_ts)
            coverage = compute_coverage(
                cached,
                requested_start=start,
                requested_end=end,
                granularity=granularity,
            )
        except (httpx.HTTPError, ValueError):
            fetch_failed = True
            logger.exception(
                "coinbase_partial_candle_data",
                product_id=product,
                timeframe=timeframe,
            )

    if execution_grade_required:
        start, coverage = snap_start_to_available(
            cached,
            coverage,
            requested_start=start,
            requested_end=end,
            granularity=granularity,
        )
        cached = [c for c in cached if int(start.timestamp()) <= c.start_ts < int(end.timestamp())]

    quality = build_candle_source_quality(
        product_id=product,
        candles=cached,
        coverage=coverage,
        execution_grade_required=execution_grade_required,
        max_missing_pct_execution=settings.max_missing_pct_execution,
        fetch_failed=fetch_failed,
    )
    return cached, quality


def _live_quality(product_id: str, observed: str | None) -> SourceQuality:
    freshness = freshness_seconds(parse_time(observed)) if observed else None
    return SourceQuality(
        product_id=product_id.upper(),
        latest_observation_at=observed,
        freshness_seconds=freshness,
        limitations=["Public ticker endpoint; latest_trade and quote share one cached response"],
        execution_grade=True,
    )


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _job_id(
    strategy_spec: dict[str, Any],
    data_config: dict[str, Any],
    execution_config: dict[str, Any],
    source_quality: dict[str, Any],
) -> str:
    payload = {
        "strategy_spec": strategy_spec,
        "data_config": data_config,
        "execution_config": execution_config,
        "source_quality_fingerprint": _fingerprint(source_quality),
    }
    return "crypto_bt_" + _fingerprint(payload)[:16]


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    """Liveness check."""
    return JSONResponse({"status": "healthy", "uptime_seconds": time.time() - _server_start_time})


@mcp.custom_route("/health/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """Readiness check."""
    ready_state = (
        _state.settings is not None and _state.coinbase is not None and _state.store is not None
    )
    return JSONResponse(
        {"status": "ready" if ready_state else "not_ready"},
        status_code=200 if ready_state else 503,
    )


def bootstrap() -> Settings:
    """Initialize process-wide dependencies."""
    settings = load_settings()
    configure_logging(settings.log_level)
    _state.settings = settings
    _state.coinbase = CoinbaseClient(
        base_url=settings.coinbase_market_base_url,
        timeout=settings.request_timeout,
        local_safety_limit=settings.coinbase_local_safety_limit,
        rate_window_seconds=settings.coinbase_rate_window_seconds,
        max_concurrent_requests=settings.coinbase_max_concurrent_requests,
        max_retries=settings.coinbase_max_retries,
        backoff_base_seconds=settings.coinbase_backoff_base_seconds,
        backoff_max_seconds=settings.coinbase_backoff_max_seconds,
    )
    _state.store = CryptoStore(
        settings.crypto_duckdb_path,
        memory_limit=settings.crypto_duckdb_memory_limit,
    )
    logger.info("crypto_server_bootstrapped", version=__version__)
    return settings


async def _close_state() -> None:
    """Close process-wide resources."""
    if _state.coinbase is not None:
        await _state.coinbase.close()
        _state.coinbase = None
    if _state.store is not None:
        _state.store.close()
        _state.store = None


async def main() -> None:
    """Run FastMCP server."""
    settings = bootstrap()
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
    try:
        await mcp.run_async(
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
            path="/mcp",
            middleware=middleware,
        )
    finally:
        await _close_state()


if __name__ == "__main__":
    asyncio.run(main())
