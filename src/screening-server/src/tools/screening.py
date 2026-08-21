"""Screening tools for stock discovery and ticker resolution via FMP API.

Tools:
- screen_stocks: Market-wide stock screener with multiple filters
- search_company_by_name: Resolve company names to ticker symbols
- search_company_by_symbol: Resolve partial/typo symbols to valid tickers
"""

from datetime import datetime, timezone
from typing import Any

from ..clients.fmp_client import FMPClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import filter_screen_results, filter_search_results

logger = get_logger(__name__)

# US national securities exchanges as FMP reports them in `exchangeShortName`.
# OTC/PNK venues are deliberately absent: they are US-traded but not
# exchange-listed, and "US-listed" screens should not silently include them.
_US_LISTING_VENUES = frozenset({"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "BATS", "CBOE"})


def _split_by_listing_venue(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Partition screener rows into US-listed rows and the venues dropped.

    Args:
        rows: Screener rows carrying an ``exchangeShortName`` field.

    Returns:
        Tuple of the US-listed rows and the sorted distinct venue labels that
        were excluded. A row with no recognisable venue is excluded and
        reported as ``unknown`` rather than assumed to be US-listed.

    """
    kept: list[dict[str, Any]] = []
    excluded: set[str] = set()
    for row in rows:
        venue = str(row.get("exchangeShortName") or "").strip().upper()
        if venue in _US_LISTING_VENUES:
            kept.append(row)
        else:
            excluded.add(venue or "unknown")
    return kept, sorted(excluded)


# =============================================================================
# Stock Screening Tool
# =============================================================================


async def screen_stocks(
    market_cap_more_than: int | None = None,
    market_cap_lower_than: int | None = None,
    price_more_than: float | None = None,
    price_lower_than: float | None = None,
    volume_more_than: int | None = None,
    volume_lower_than: int | None = None,
    beta_more_than: float | None = None,
    beta_lower_than: float | None = None,
    dividend_more_than: float | None = None,
    dividend_lower_than: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    exchange: str | None = None,
    is_etf: bool | None = None,
    is_fund: bool | None = None,
    is_actively_trading: bool = True,
    include_all_share_classes: bool = False,
    us_listed_only: bool = False,
    limit: int = 25,
) -> dict[str, Any]:
    """Screen stocks with various filters for idea generation.

    Market-wide stock screening to find candidates matching specific criteria.
    Returns matching stocks in the provider's default order (no local ranking
    is applied) that can be passed to other agents for deeper analysis.

    Args:
        market_cap_more_than: Minimum market cap
        market_cap_lower_than: Maximum market cap
        price_more_than: Minimum price
        price_lower_than: Maximum price
        volume_more_than: Minimum volume
        volume_lower_than: Maximum volume
        beta_more_than: Minimum beta
        beta_lower_than: Maximum beta
        dividend_more_than: Minimum annual dividend in dollars per share
            (the lastAnnualDividend amount, not a yield percent)
        dividend_lower_than: Maximum annual dividend in dollars per share
            (the lastAnnualDividend amount, not a yield percent)
        sector: Sector filter (e.g., "Technology", "Healthcare", "Financial Services")
        industry: Industry filter
        country: Country code (e.g., "US", "CN", "GB")
        exchange: Exchange (e.g., "NASDAQ", "NYSE", "AMEX")
        is_etf: Filter for ETFs only
        is_fund: Filter for mutual funds only
        is_actively_trading: Only actively traded stocks (default: True)
        include_all_share_classes: Include all share classes (default: False)
        us_listed_only: Keep only rows listed on a US exchange. The provider's
            `country` filter is company domicile, not listing venue, so a
            US-domiciled issuer's foreign cross-listings survive it. Excluded
            rows and their venues are always reported in the response metadata.
        limit: Maximum results (default: 25, max: 100)

    Returns:
        Screening results with metadata and filtered stock list

    Raises:
        FMPAPIError: If API request fails
    """
    try:
        settings = get_settings()
        # Over-fetch one row past the cap so truncation is detectable without a
        # second request. The provider applies no documented ordering, so rows
        # are returned in provider-default order (not a ranking).
        capped_limit = min(limit, 100)
        fetch_limit = min(capped_limit + 1, 100)
        async with FMPClient(settings) as client:
            data = await client.screen_stocks(
                market_cap_more_than=market_cap_more_than,
                market_cap_lower_than=market_cap_lower_than,
                price_more_than=price_more_than,
                price_lower_than=price_lower_than,
                volume_more_than=volume_more_than,
                volume_lower_than=volume_lower_than,
                beta_more_than=beta_more_than,
                beta_lower_than=beta_lower_than,
                dividend_more_than=dividend_more_than,
                dividend_lower_than=dividend_lower_than,
                sector=sector,
                industry=industry,
                country=country,
                exchange=exchange,
                is_etf=is_etf,
                is_fund=is_fund,
                is_actively_trading=is_actively_trading,
                include_all_share_classes=include_all_share_classes,
                limit=fetch_limit,
            )

            page = filter_screen_results(data)
            # The over-fetched probe row exists only to answer "are there more?".
            # It is not part of the result set, so it must not reach any count.
            has_more = len(page) > capped_limit
            considered = page[:capped_limit]
            eligible, excluded_venues = (
                _split_by_listing_venue(considered) if us_listed_only else (considered, [])
            )
            provider_rows_considered = len(considered)
            excluded_count = provider_rows_considered - len(eligible)
            results = eligible

            # Build filters applied for response metadata
            filters_applied: dict[str, Any] = {}
            if market_cap_more_than is not None:
                filters_applied["market_cap_more_than"] = market_cap_more_than
            if market_cap_lower_than is not None:
                filters_applied["market_cap_lower_than"] = market_cap_lower_than
            if price_more_than is not None:
                filters_applied["price_more_than"] = price_more_than
            if price_lower_than is not None:
                filters_applied["price_lower_than"] = price_lower_than
            if volume_more_than is not None:
                filters_applied["volume_more_than"] = volume_more_than
            if volume_lower_than is not None:
                filters_applied["volume_lower_than"] = volume_lower_than
            if beta_more_than is not None:
                filters_applied["beta_more_than"] = beta_more_than
            if beta_lower_than is not None:
                filters_applied["beta_lower_than"] = beta_lower_than
            if dividend_more_than is not None:
                filters_applied["dividend_more_than"] = dividend_more_than
            if dividend_lower_than is not None:
                filters_applied["dividend_lower_than"] = dividend_lower_than
            if sector is not None:
                filters_applied["sector"] = sector
            if industry is not None:
                filters_applied["industry"] = industry
            if country is not None:
                filters_applied["country"] = country
            if exchange is not None:
                filters_applied["exchange"] = exchange
            if is_etf is not None:
                filters_applied["is_etf"] = is_etf
            if is_fund is not None:
                filters_applied["is_fund"] = is_fund
            if not is_actively_trading:
                filters_applied["is_actively_trading"] = False
            if include_all_share_classes:
                filters_applied["include_all_share_classes"] = True
            if us_listed_only:
                filters_applied["us_listed_only"] = True

            # Warn if no filters applied (could return too many results)
            warning = None
            if not filters_applied:
                warning = "No filters applied. Consider adding filters to narrow results."

            return {
                "meta": {
                    "vendor": "FMP",
                    "endpoint": "/stable/company-screener",
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "row_count": len(results),
                    "returned": len(results),
                    "provider_rows_considered": provider_rows_considered,
                    "us_listed_only": us_listed_only,
                    "excluded_by_venue": excluded_count,
                    "excluded_venues": excluded_venues,
                    "limit": capped_limit,
                    "has_more": has_more,
                    "order": "provider_default",
                    "filters_applied": filters_applied,
                    "warning": warning,
                },
                "results": results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "screen_stocks",
                "sector": sector,
                "country": country,
            },
        )
        raise


# =============================================================================
# Company Search Tools
# =============================================================================


async def search_company_by_name(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search companies by name to resolve ticker symbols.

    Useful when users mention company names instead of tickers.
    Returns matching companies with their symbols and exchange info.

    Args:
        query: Company name or partial name (e.g., "Palantir", "Apple")
        limit: Maximum results (default: 10, max: 20)

    Returns:
        Search results with matching companies

    Raises:
        FMPAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.search_by_name(
                query=query,
                limit=limit,
            )

            filtered_results = filter_search_results(data)

            return {
                "meta": {
                    "vendor": "FMP",
                    "endpoint": "/stable/search-name",
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "query": query,
                    "row_count": len(filtered_results),
                },
                "results": filtered_results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "search_company_by_name",
                "query": query,
            },
        )
        raise


async def list_available_sectors() -> dict[str, Any]:
    """List all valid sector values accepted by the stock screener.

    Call this before screening with a sector filter to discover
    the exact sector strings the API accepts.

    Returns:
        List of valid sector values with metadata.
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_available_sectors()

            sectors = sorted(entry["sector"] for entry in data if "sector" in entry)

            return {
                "meta": {
                    "vendor": "FMP",
                    "endpoint": "/stable/available-sectors",
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "count": len(sectors),
                },
                "sectors": sectors,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "list_available_sectors"},
        )
        raise


async def list_available_industries() -> dict[str, Any]:
    """List all valid industry values accepted by the stock screener.

    Call this before screening with an industry filter to discover
    the exact industry strings the API accepts.

    Returns:
        List of valid industry values with metadata.
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.get_available_industries()

            industries = sorted(entry["industry"] for entry in data if "industry" in entry)

            return {
                "meta": {
                    "vendor": "FMP",
                    "endpoint": "/stable/available-industries",
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "count": len(industries),
                },
                "industries": industries,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "list_available_industries"},
        )
        raise


async def search_company_by_symbol(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search companies by symbol to resolve partial or typo tickers.

    Useful when users enter:
    - Typos (e.g., "AAPLL" instead of "AAPL")
    - Partial symbols (e.g., "AAP")
    - International variants

    Args:
        query: Ticker or partial ticker (e.g., "AAPL", "AAP", "TSLA")
        limit: Maximum results (default: 10, max: 20)

    Returns:
        Search results with matching companies

    Raises:
        FMPAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with FMPClient(settings) as client:
            data = await client.search_by_symbol(
                query=query,
                limit=limit,
            )

            filtered_results = filter_search_results(data)

            return {
                "meta": {
                    "vendor": "FMP",
                    "endpoint": "/stable/search-symbol",
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "query": query,
                    "row_count": len(filtered_results),
                },
                "results": filtered_results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "search_company_by_symbol",
                "query": query,
            },
        )
        raise
