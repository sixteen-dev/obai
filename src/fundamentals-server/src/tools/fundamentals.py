"""Fundamental analysis tools with AI-enhanced context."""

from typing import Any, Literal

from ..clients import FMPClient, QdrantVectorClient
from ..config import get_settings
from ..logging_config import get_logger, log_api_call, log_error, log_tool_invocation
from ..response_filters import (
    filter_analyst_estimates,
    filter_company_profile,
    filter_company_rating,
    filter_financial_ratios,
    filter_financial_statement,
    filter_insider_trades,
    filter_insider_trading_statistics,
    filter_key_metrics,
    filter_price_target,
    filter_revenue_segments,
    filter_sec_filings,
)
from ..response_utils import format_api_error

logger = get_logger(__name__)


def _summarize_source_failures(
    sources: dict[str, list[dict[str, Any]] | BaseException],
) -> tuple[list[str], BaseException | None]:
    """Identify which combined-tool sub-fetches failed.

    Args:
        sources: Mapping of source name to its gathered result — the fetched
            data list on success, or the raised exception on failure.

    Returns:
        A tuple of the failed source names (in ``sources`` order) and the first
        captured failure exception, or ``None`` if every source succeeded.
    """
    failed_names = [name for name, r in sources.items() if isinstance(r, BaseException)]
    first_failure = next((r for r in sources.values() if isinstance(r, BaseException)), None)
    return failed_names, first_failure


def _all_sources_failed_error(failure: BaseException | None, **context: str) -> dict[str, Any]:
    """Map an all-sources-failed condition to a typed provider-error payload.

    Args:
        failure: The first captured sub-fetch exception (must not be ``None``).
        **context: Identifying fields to attach (e.g. ``symbol``, ``period``).

    Returns:
        An ``isError`` payload matching this server's error convention.

    Raises:
        RuntimeError: If called without a captured failure.
        BaseException: Re-raises non-``Exception`` failures (e.g. cancellation)
            instead of masking them as a friendly error dict.
    """
    if failure is None:
        raise RuntimeError("all sources reported failed but no exception was captured")
    if not isinstance(failure, Exception):
        raise failure
    payload = format_api_error(failure, "FMP")
    payload.update(context)
    return payload


def _fiscal_basis_label(record: dict[str, Any]) -> str:
    """Build a fiscal-period basis label (e.g. 'FY2023', 'Q1 2024') for a record.

    Args:
        record: A key-metrics or ratios record carrying ``date``/``period`` fields.

    Returns:
        A human-readable fiscal-period label, or ``"fiscal-period"`` when the
        record lacks period identifiers.
    """
    period = str(record.get("period") or "").strip()
    year = str(record.get("date") or "")[:4]
    if period == "FY":
        return f"FY{year}" if year else "fiscal-period"
    if period and year:
        return f"{period} {year}"
    return period or year or "fiscal-period"


def _stamp_valuation_basis(
    records: list[dict[str, Any]], period: str | None
) -> list[dict[str, Any]]:
    """Label each valuation record with its basis: TTM or its fiscal period.

    Args:
        records: Filtered key-metrics or ratios records to annotate in place.
        period: ``None`` for the trailing-twelve-month path (basis ``"ttm"``),
            otherwise the requested fiscal period (basis derived per record).

    Returns:
        The same records, each with a ``basis`` field added.
    """
    for record in records:
        record["basis"] = "ttm" if period is None else _fiscal_basis_label(record)
    return records


async def get_fundamentals(
    symbol: str,
    statement_type: Literal["income", "balance", "cashflow"],
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 4,
    include_context: bool = True,
) -> dict[str, Any]:
    """Get financial statements with optional educational context.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        statement_type: Type of financial statement
        period: Reporting period (annual or quarter)
        limit: Number of periods to return
        include_context: Whether to include educational PDF context

    Returns:
        Financial statement data with optional educational context

    Raises:
        ValueError: If symbol or statement_type is invalid
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger,
        "get_fundamentals",
        {
            "symbol": symbol,
            "statement_type": statement_type,
            "period": period,
            "limit": limit,
            "include_context": include_context,
        },
    )

    fmp = FMPClient()

    try:
        # Fetch financial statement from FMP
        endpoint = f"{statement_type}-statement"
        log_api_call(logger, "fmp", endpoint, {"symbol": symbol, "period": period, "limit": limit})

        if statement_type == "income":
            data = await fmp.get_income_statement(symbol, period, limit)
        elif statement_type == "balance":
            data = await fmp.get_balance_sheet(symbol, period, limit)
        else:  # cashflow
            data = await fmp.get_cash_flow(symbol, period, limit)
        logger.info(
            "fmp_data_fetched",
            symbol=symbol,
            statement_type=statement_type,
            records_count=len(data),
        )

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_financial_statement(data)

        result: dict[str, Any] = {
            "symbol": symbol,
            "statement_type": statement_type,
            "period": period,
            "data": filtered_data,
        }

        # Add educational context from vector search (only when Qdrant is enabled)
        if include_context and get_settings().qdrant_enabled:
            try:
                async with QdrantVectorClient() as vectors:
                    query = f"How to analyze {statement_type} statement financial metrics"
                    log_api_call(logger, "qdrant", "query_points", {"query": query})
                    context = await vectors.search(query, top_k=3)

                    logger.info(
                        "vector_context_fetched",
                        symbol=symbol,
                        query=query,
                        results_count=len(context),
                    )

                    result["educational_context"] = [
                        {
                            "source": c["metadata"].get("source", "Unknown"),
                            "text": c["metadata"].get("text", ""),
                            "relevance": 1 - c.get("distance", 1),
                        }
                        for c in context
                    ]
            except Exception as e:
                log_error(
                    logger,
                    e,
                    context={
                        "tool": "get_fundamentals",
                        "symbol": symbol,
                        "event": "vector_search_failed",
                    },
                )
                # Continue without context if vector search fails
                result["educational_context"] = []

        logger.info("tool_execution_complete", tool="get_fundamentals", symbol=symbol)
        return result

    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_fundamentals",
                "symbol": symbol,
                "statement_type": statement_type,
            },
        )
        raise

    finally:
        await fmp.close()


async def get_company_profile(symbol: str, include_context: bool = False) -> dict[str, Any]:
    """Get company profile with optional industry context.

    Args:
        symbol: Stock ticker symbol
        include_context: Whether to include industry educational context

    Returns:
        Company profile data with optional context

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger, "get_company_profile", {"symbol": symbol, "include_context": include_context}
    )

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "profile", {"symbol": symbol})
        data = await fmp.get_company_profile(symbol)

        if not data:
            error = ValueError(f"No profile data found for {symbol}")
            log_error(logger, error, context={"tool": "get_company_profile", "symbol": symbol})
            raise error

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_company_profile(data)
        profile = filtered_data[0]  # FMP returns list with single item
        logger.info("company_profile_fetched", symbol=symbol, sector=profile.get("sector"))

        result: dict[str, Any] = {
            "symbol": symbol,
            "profile": profile,
        }

        # Add sector/industry educational context (only when Qdrant is enabled)
        if include_context and profile.get("sector") and get_settings().qdrant_enabled:
            try:
                async with QdrantVectorClient() as vectors:
                    query = f"Investing in {profile['sector']} sector companies"
                    log_api_call(logger, "qdrant", "query_points", {"query": query})
                    context = await vectors.search(query, top_k=2)

                    logger.info(
                        "sector_context_fetched",
                        symbol=symbol,
                        sector=profile["sector"],
                        results_count=len(context),
                    )

                    result["educational_context"] = [
                        {
                            "source": c["metadata"].get("source", "Unknown"),
                            "text": c["metadata"].get("text", ""),
                            "relevance": 1 - c.get("distance", 1),
                        }
                        for c in context
                    ]
            except Exception as e:
                log_error(
                    logger,
                    e,
                    context={
                        "tool": "get_company_profile",
                        "symbol": symbol,
                        "event": "vector_search_failed",
                    },
                )
                result["educational_context"] = []

        logger.info("tool_execution_complete", tool="get_company_profile", symbol=symbol)
        return result

    except Exception as e:
        log_error(logger, e, context={"tool": "get_company_profile", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_key_metrics(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 5,
) -> dict[str, Any]:
    """Get key financial metrics (P/E, ROE, ROIC, etc.).

    Args:
        symbol: Stock ticker symbol
        period: Reporting period
        limit: Number of periods to return

    Returns:
        Key metrics data

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger, "get_key_metrics", {"symbol": symbol, "period": period, "limit": limit}
    )

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "key-metrics", {"symbol": symbol, "period": period})
        data = await fmp.get_key_metrics(symbol, period, limit)

        logger.info("key_metrics_fetched", symbol=symbol, period=period, records_count=len(data))
        logger.info("tool_execution_complete", tool="get_key_metrics", symbol=symbol)

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_key_metrics(data)

        return {
            "symbol": symbol,
            "period": period,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_key_metrics", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_financial_ratios(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 5,
) -> dict[str, Any]:
    """Get financial ratios (liquidity, profitability, etc.).

    Args:
        symbol: Stock ticker symbol
        period: Reporting period
        limit: Number of periods to return

    Returns:
        Financial ratios data

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger, "get_financial_ratios", {"symbol": symbol, "period": period, "limit": limit}
    )

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "ratios", {"symbol": symbol, "period": period})
        data = await fmp.get_financial_ratios(symbol, period, limit)

        logger.info(
            "financial_ratios_fetched", symbol=symbol, period=period, records_count=len(data)
        )
        logger.info("tool_execution_complete", tool="get_financial_ratios", symbol=symbol)

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_financial_ratios(data)

        return {
            "symbol": symbol,
            "period": period,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_financial_ratios", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_analyst_estimates(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 5,
) -> dict[str, Any]:
    """Get analyst estimates for EPS and revenue forecasts.

    Args:
        symbol: Stock ticker symbol
        period: Reporting period
        limit: Number of periods to return

    Returns:
        Analyst estimates data

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger, "get_analyst_estimates", {"symbol": symbol, "period": period, "limit": limit}
    )

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "analyst-estimates", {"symbol": symbol, "period": period})
        data = await fmp.get_analyst_estimates(symbol, period, limit)

        logger.info(
            "analyst_estimates_fetched", symbol=symbol, period=period, records_count=len(data)
        )
        logger.info("tool_execution_complete", tool="get_analyst_estimates", symbol=symbol)

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_analyst_estimates(data)

        return {
            "symbol": symbol,
            "period": period,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_analyst_estimates", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_price_target_summary(symbol: str) -> dict[str, Any]:
    """Get price target summary from analysts.

    Args:
        symbol: Stock ticker symbol

    Returns:
        Price target summary data

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(logger, "get_price_target_summary", {"symbol": symbol})

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "price-target-summary", {"symbol": symbol})
        data = await fmp.get_price_target_summary(symbol)

        logger.info("price_target_summary_fetched", symbol=symbol, records_count=len(data))
        logger.info("tool_execution_complete", tool="get_price_target_summary", symbol=symbol)

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_price_target(data)

        return {
            "symbol": symbol,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_price_target_summary", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_company_rating(symbol: str) -> dict[str, Any]:
    """Get company rating and recommendation.

    Args:
        symbol: Stock ticker symbol

    Returns:
        Company rating data

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(logger, "get_company_rating", {"symbol": symbol})

    fmp = FMPClient()

    try:
        log_api_call(logger, "fmp", "rating", {"symbol": symbol})
        data = await fmp.get_company_rating(symbol)

        logger.info("company_rating_fetched", symbol=symbol, records_count=len(data))
        logger.info("tool_execution_complete", tool="get_company_rating", symbol=symbol)

        # Filter out unnecessary fields to reduce token usage
        filtered_data = filter_company_rating(data)

        return {
            "symbol": symbol,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_company_rating", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_sec_filings(
    symbol: str,
    limit: int = 5,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Get SEC filings for a company.

    Retrieves official SEC filings including 10-K (annual reports), 10-Q (quarterly),
    8-K (material events), and other regulatory filings.

    Args:
        symbol: Stock ticker symbol
        limit: Number of filings to return
        from_date: Start date filter (YYYY-MM-DD), defaults to 3 months ago
        to_date: End date filter (YYYY-MM-DD), defaults to today

    Returns:
        SEC filings data with links to official documents

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger,
        "get_sec_filings",
        {"symbol": symbol, "limit": limit, "from_date": from_date, "to_date": to_date},
    )

    fmp = FMPClient()

    try:
        log_api_call(
            logger,
            "fmp",
            "sec-filings-search/symbol",
            {"symbol": symbol, "limit": limit, "from": from_date, "to": to_date},
        )
        data = await fmp.get_sec_filings(symbol, limit, from_date, to_date)

        logger.info("sec_filings_fetched", symbol=symbol, records_count=len(data))
        logger.info("tool_execution_complete", tool="get_sec_filings", symbol=symbol)

        # Filter to essential fields
        filtered_data = filter_sec_filings(data)

        return {
            "symbol": symbol,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_sec_filings", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_insider_trades(
    symbol: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Get insider trading activity for a company.

    Retrieves transactions by corporate insiders including executives, directors,
    and 10%+ shareholders. Useful for sentiment analysis and tracking insider
    confidence in the company.

    Args:
        symbol: Stock ticker symbol
        limit: Number of trades to return

    Returns:
        Insider trade data including transaction type, shares, and price

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger,
        "get_insider_trades",
        {"symbol": symbol, "limit": limit},
    )

    fmp = FMPClient()

    try:
        log_api_call(
            logger,
            "fmp",
            "insider-trading/search",
            {"symbol": symbol, "limit": limit},
        )
        data = await fmp.get_insider_trades(symbol, limit)

        logger.info("insider_trades_fetched", symbol=symbol, records_count=len(data))
        logger.info("tool_execution_complete", tool="get_insider_trades", symbol=symbol)

        # Filter to relevant transaction details
        filtered_data = filter_insider_trades(data)

        return {
            "symbol": symbol,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_insider_trades", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_insider_trading_statistics(
    symbol: str,
    limit: int = 4,
) -> dict[str, Any]:
    """Get aggregated insider trading statistics by quarter.

    Returns quarterly summaries of insider buying/selling activity including
    transaction counts, volumes, and the acquired-to-disposed ratio. More
    token-efficient than individual trades for gauging insider sentiment.

    Args:
        symbol: Stock ticker symbol
        limit: Number of most recent quarters to return (default: 4)

    Returns:
        Quarterly insider trading statistics with buy/sell ratios

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger,
        "get_insider_trading_statistics",
        {"symbol": symbol, "limit": limit},
    )

    fmp = FMPClient()

    try:
        log_api_call(
            logger,
            "fmp",
            "insider-trading/statistics",
            {"symbol": symbol, "limit": limit},
        )
        data = await fmp.get_insider_trading_statistics(symbol, limit)

        logger.info(
            "insider_trading_statistics_fetched",
            symbol=symbol,
            records_count=len(data),
        )
        logger.info(
            "tool_execution_complete",
            tool="get_insider_trading_statistics",
            symbol=symbol,
        )

        filtered_data = filter_insider_trading_statistics(data)

        return {
            "symbol": symbol,
            "quarters": len(filtered_data),
            "data": filtered_data,
        }

    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "get_insider_trading_statistics", "symbol": symbol},
        )
        raise

    finally:
        await fmp.close()


async def get_revenue_segments(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
) -> dict[str, Any]:
    """Get revenue breakdown by product segment.

    Shows how a company's revenue is distributed across different product lines
    or business segments. Useful for understanding business diversification and
    which products drive earnings.

    Args:
        symbol: Stock ticker symbol
        period: Reporting period (annual or quarter)

    Returns:
        Revenue segmentation data by product/business line

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    log_tool_invocation(
        logger,
        "get_revenue_segments",
        {"symbol": symbol, "period": period},
    )

    fmp = FMPClient()

    try:
        log_api_call(
            logger,
            "fmp",
            "revenue-product-segmentation",
            {"symbol": symbol, "period": period},
        )
        data = await fmp.get_revenue_product_segmentation(symbol, period)

        logger.info(
            "revenue_segments_fetched", symbol=symbol, period=period, records_count=len(data)
        )
        logger.info("tool_execution_complete", tool="get_revenue_segments", symbol=symbol)

        # Keep all segment data
        filtered_data = filter_revenue_segments(data)

        return {
            "symbol": symbol,
            "period": period,
            "data": filtered_data,
        }

    except Exception as e:
        log_error(logger, e, context={"tool": "get_revenue_segments", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_valuation_metrics(
    symbol: str,
    period: Literal["annual", "quarter"] | None = None,
    limit: int = 1,
) -> dict[str, Any]:
    """Get comprehensive valuation metrics combining key metrics and ratios.

    This is the go-to tool for valuation questions like P/E ratio, P/B ratio,
    EV/EBITDA, ROE, margins, and debt ratios.

    Defaults to trailing-twelve-month (TTM) figures against the latest price so
    ratios reflect current conditions. Pass an explicit ``period`` to retrieve a
    fiscal-period historical series instead. Every record carries a ``basis``
    field (``"ttm"`` or a fiscal-period label such as ``"FY2023"``).

    Includes:
    - Valuation: P/E, P/B, P/S, EV/EBITDA, EV/Sales
    - Profitability: ROE, ROA, ROIC, gross/operating/net margins
    - Efficiency: asset turnover, inventory turnover
    - Leverage: debt/equity, debt/assets, interest coverage
    - Per-share: revenue, earnings, book value, free cash flow

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        period: Reporting period. ``None`` (default) returns TTM valuation;
            ``'annual'`` or ``'quarter'`` returns a fiscal-period series.
        limit: Number of periods to return in history mode (default: 1 for latest)

    Returns:
        Combined valuation and financial ratio data, each record basis-labelled

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    import asyncio

    log_tool_invocation(
        logger,
        "get_valuation_metrics",
        {"symbol": symbol, "period": period, "limit": limit},
    )

    fmp = FMPClient()

    basis_label = period or "ttm"

    try:
        endpoints = "key-metrics-ttm+ratios-ttm" if period is None else "key-metrics+ratios"
        log_api_call(logger, "fmp", endpoints, {"symbol": symbol, "period": period})

        if period is None:
            key_metrics_task = fmp.get_key_metrics_ttm(symbol)
            ratios_task = fmp.get_financial_ratios_ttm(symbol)
        else:
            key_metrics_task = fmp.get_key_metrics(symbol, period, limit)
            ratios_task = fmp.get_financial_ratios(symbol, period, limit)

        results = await asyncio.gather(key_metrics_task, ratios_task, return_exceptions=True)
        key_metrics_data: list[dict[str, Any]] | BaseException = results[0]
        ratios_data: list[dict[str, Any]] | BaseException = results[1]

        key_metrics_result: list[dict[str, Any]] = []
        ratios_result: list[dict[str, Any]] = []

        if isinstance(key_metrics_data, BaseException):
            logger.warning("key_metrics_fetch_failed", error=str(key_metrics_data))
        else:
            key_metrics_result = _stamp_valuation_basis(
                filter_key_metrics(key_metrics_data), period
            )

        if isinstance(ratios_data, BaseException):
            logger.warning("ratios_fetch_failed", error=str(ratios_data))
        else:
            ratios_result = _stamp_valuation_basis(filter_financial_ratios(ratios_data), period)

        sources: dict[str, list[dict[str, Any]] | BaseException] = {
            "key_metrics": key_metrics_data,
            "financial_ratios": ratios_data,
        }
        failed_sources, first_failure = _summarize_source_failures(sources)
        if len(failed_sources) == len(sources):
            return _all_sources_failed_error(first_failure, symbol=symbol, period=basis_label)

        logger.info(
            "valuation_metrics_fetched",
            symbol=symbol,
            basis=basis_label,
            key_metrics_count=len(key_metrics_result),
            ratios_count=len(ratios_result),
        )
        logger.info("tool_execution_complete", tool="get_valuation_metrics", symbol=symbol)

        result: dict[str, Any] = {
            "symbol": symbol,
            "period": basis_label,
            "key_metrics": key_metrics_result,
            "financial_ratios": ratios_result,
        }
        if failed_sources:
            result["degraded_sources"] = failed_sources
        return result

    except Exception as e:
        log_error(logger, e, context={"tool": "get_valuation_metrics", "symbol": symbol})
        raise

    finally:
        await fmp.close()


async def get_analyst_outlook(
    symbol: str,
    estimates_limit: int = 2,
) -> dict[str, Any]:
    """Get comprehensive analyst outlook combining estimates, targets, and ratings.

    This is the go-to tool for forward-looking analyst data. Combines:
    - Analyst estimates (EPS and revenue forecasts)
    - Price target summary (consensus high/low/average)
    - Company rating (buy/hold/sell recommendation)

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        estimates_limit: Number of estimate periods to return (default: 2)

    Returns:
        Combined analyst data with estimates, targets, and rating

    Raises:
        httpx.HTTPError: If FMP API request fails
    """
    import asyncio

    log_tool_invocation(
        logger,
        "get_analyst_outlook",
        {"symbol": symbol, "estimates_limit": estimates_limit},
    )

    fmp = FMPClient()

    try:
        log_api_call(
            logger,
            "fmp",
            "analyst-estimates+price-target+rating",
            {"symbol": symbol},
        )

        estimates_task = fmp.get_analyst_estimates(symbol, "annual", estimates_limit)
        price_target_task = fmp.get_price_target_summary(symbol)
        rating_task = fmp.get_company_rating(symbol)

        results = await asyncio.gather(
            estimates_task, price_target_task, rating_task, return_exceptions=True
        )
        estimates_data: list[dict[str, Any]] | BaseException = results[0]
        price_target_data: list[dict[str, Any]] | BaseException = results[1]
        rating_data: list[dict[str, Any]] | BaseException = results[2]

        estimates_result: list[dict[str, Any]] = []
        price_target_result: list[dict[str, Any]] = []
        rating_result: list[dict[str, Any]] = []

        if isinstance(estimates_data, BaseException):
            logger.warning("estimates_fetch_failed", error=str(estimates_data))
        else:
            estimates_result = filter_analyst_estimates(estimates_data)

        if isinstance(price_target_data, BaseException):
            logger.warning("price_target_fetch_failed", error=str(price_target_data))
        else:
            price_target_result = filter_price_target(price_target_data)

        if isinstance(rating_data, BaseException):
            logger.warning("rating_fetch_failed", error=str(rating_data))
        else:
            rating_result = filter_company_rating(rating_data)

        sources: dict[str, list[dict[str, Any]] | BaseException] = {
            "analyst_estimates": estimates_data,
            "price_target": price_target_data,
            "rating": rating_data,
        }
        failed_sources, first_failure = _summarize_source_failures(sources)
        if len(failed_sources) == len(sources):
            return _all_sources_failed_error(first_failure, symbol=symbol)

        logger.info(
            "analyst_outlook_fetched",
            symbol=symbol,
            estimates_count=len(estimates_result),
            has_price_target=len(price_target_result) > 0,
            has_rating=len(rating_result) > 0,
        )
        logger.info("tool_execution_complete", tool="get_analyst_outlook", symbol=symbol)

        result: dict[str, Any] = {
            "symbol": symbol,
            "analyst_estimates": estimates_result,
            "price_target": price_target_result[0] if price_target_result else None,
            "rating": rating_result[0] if rating_result else None,
        }
        if failed_sources:
            result["degraded_sources"] = failed_sources
        return result

    except Exception as e:
        log_error(logger, e, context={"tool": "get_analyst_outlook", "symbol": symbol})
        raise

    finally:
        await fmp.close()
