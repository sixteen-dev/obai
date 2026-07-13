"""Options tools for real-time options data via Massive API.

MVP Tools (Critical):
- get_option_chain_snapshot: Full option chain with Greeks, IV, OI
- get_option_contract_snapshot: Single contract details
- get_latest_option_trade: Most recent trade
- get_latest_option_quote: Most recent NBBO quote

Optional Tools (Nice-to-Have):
- list_option_contracts: Reference data for contracts
- get_option_trades_history: Historical tick-level trades
- get_option_quotes_history: Historical NBBO quotes
- get_option_aggregates: OHLCV bars
"""

from typing import Any

from ..clients.massive_client import MassiveClient
from ..config import get_settings
from ..logging_config import get_logger, log_error
from ..response_filters import (
    filter_option_chain_snapshot,
    filter_option_contract_snapshot,
    filter_option_contracts_list,
    filter_option_quote,
    filter_option_trade,
)

logger = get_logger(__name__)


# =============================================================================
# MVP Tools - Critical Real-Time Options Endpoints
# =============================================================================


async def get_option_chain_snapshot(
    underlying_asset: str,
    expiration_date: str | None = None,
    strike_price: float | None = None,
    contract_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get real-time snapshot of option chain for an underlying asset.

    Retrieves pricing, Greeks, implied volatility, open interest, and
    underlying price context for all matching option contracts.

    Args:
        underlying_asset: Stock ticker symbol (e.g., 'AAPL')
        expiration_date: Filter by expiration date (YYYY-MM-DD)
        strike_price: Filter by exact strike price
        contract_type: Filter by 'call' or 'put'
        limit: Maximum results (max 250)

    Returns:
        Option chain snapshot with contracts and metadata

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_option_chain_snapshot(
                underlying_asset=underlying_asset,
                expiration_date=expiration_date,
                strike_price=strike_price,
                contract_type=contract_type,
                limit=limit,
            )

            results = data.get("results", [])
            filtered_results = filter_option_chain_snapshot(results)

            response: dict[str, Any] = {
                "underlying_asset": underlying_asset.upper(),
                "filters": {
                    "expiration_date": expiration_date,
                    "strike_price": strike_price,
                    "contract_type": contract_type,
                },
                "count": len(filtered_results),
                "contracts": filtered_results,
                "pages_fetched": data["pages_fetched"],
                "truncated": data["truncated"],
            }
            if data["truncated"]:
                response["warning"] = (
                    "Option chain was truncated at the page cap, so chain-wide "
                    "stats (put/call ratio, max OI, IV skew) cover only the "
                    "fetched contracts. Narrow the filter (e.g. by "
                    "expiration_date or contract_type) to see the rest; "
                    "``next_cursor`` can be used to resume."
                )
                response["next_cursor"] = data["next_cursor"]
            return response
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_option_chain_snapshot",
                "underlying_asset": underlying_asset,
                "expiration_date": expiration_date,
            },
        )
        raise


async def get_option_contract_snapshot(
    underlying_asset: str,
    option_contract: str,
) -> dict[str, Any]:
    """Get real-time snapshot for a single option contract.

    Provides complete details including last trade, last quote, Greeks,
    implied volatility, and open interest.

    Args:
        underlying_asset: Stock ticker symbol (e.g., 'AAPL')
        option_contract: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Complete contract snapshot with pricing and Greeks

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_option_contract_snapshot(
                underlying_asset=underlying_asset,
                option_contract=option_contract,
            )

            results = data.get("results", {})
            filtered_results = filter_option_contract_snapshot(results)

            return {
                "underlying_asset": underlying_asset.upper(),
                "option_contract": option_contract,
                "snapshot": filtered_results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_option_contract_snapshot",
                "underlying_asset": underlying_asset,
                "option_contract": option_contract,
            },
        )
        raise


async def get_latest_option_trade(options_ticker: str) -> dict[str, Any]:
    """Get the most recent trade for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Latest trade with price, size, exchange, and timestamp

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_latest_option_trade(options_ticker)

            results = data.get("results", {})
            filtered_results = filter_option_trade(results)

            return {
                "options_ticker": options_ticker,
                "trade": filtered_results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_latest_option_trade",
                "options_ticker": options_ticker,
            },
        )
        raise


async def get_latest_option_quote(options_ticker: str) -> dict[str, Any]:
    """Get the most recent NBBO quote for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Latest NBBO with bid/ask prices and sizes

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_latest_option_quote(options_ticker)

            results = data.get("results", {})
            filtered_results = filter_option_quote(results)

            return {
                "options_ticker": options_ticker,
                "quote": filtered_results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_latest_option_quote",
                "options_ticker": options_ticker,
            },
        )
        raise


# =============================================================================
# Optional Tools - Reference and Historical Data
# =============================================================================


async def list_option_contracts(
    underlying_ticker: str | None = None,
    expiration_date: str | None = None,
    contract_type: str | None = None,
    expired: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List all option contracts with optional filters.

    Useful for discovering available contracts before querying snapshots.

    Args:
        underlying_ticker: Filter by underlying stock
        expiration_date: Filter by expiration date (YYYY-MM-DD)
        contract_type: Filter by 'call' or 'put'
        expired: Include expired contracts
        limit: Maximum results per page

    Returns:
        List of option contract references

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            page = await client.list_option_contracts(
                underlying_ticker=underlying_ticker,
                expiration_date=expiration_date,
                contract_type=contract_type,
                expired=expired,
                limit=limit,
            )

            filtered_results = filter_option_contracts_list(page["results"])

            response: dict[str, Any] = {
                "filters": {
                    "underlying_ticker": underlying_ticker,
                    "expiration_date": expiration_date,
                    "contract_type": contract_type,
                    "expired": expired,
                },
                "count": len(filtered_results),
                "contracts": filtered_results,
                "pages_fetched": page["pages_fetched"],
                "truncated": page["truncated"],
            }
            if page["truncated"]:
                response["warning"] = (
                    "Result set was truncated at the page cap. Narrow the "
                    "filter (e.g. by expiration_date) to see remaining "
                    "contracts; ``next_cursor`` can be used to resume."
                )
                response["next_cursor"] = page["next_cursor"]
            return response
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "list_option_contracts",
                "underlying_ticker": underlying_ticker,
            },
        )
        raise


async def get_option_trades_history(
    options_ticker: str,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get historical tick-level trades for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        timestamp_from: Start timestamp (nanoseconds or RFC3339)
        timestamp_to: End timestamp (nanoseconds or RFC3339)
        limit: Maximum results

    Returns:
        Historical trades data

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_option_trades_history(
                options_ticker=options_ticker,
                timestamp_from=timestamp_from,
                timestamp_to=timestamp_to,
                limit=limit,
            )

            results = data.get("results", [])

            return {
                "options_ticker": options_ticker,
                "time_range": {
                    "from": timestamp_from,
                    "to": timestamp_to,
                },
                "count": len(results),
                "trades": results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_option_trades_history",
                "options_ticker": options_ticker,
            },
        )
        raise


async def get_option_quotes_history(
    options_ticker: str,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get historical NBBO quotes for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        timestamp_from: Start timestamp (nanoseconds or RFC3339)
        timestamp_to: End timestamp (nanoseconds or RFC3339)
        limit: Maximum results

    Returns:
        Historical quotes data

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_option_quotes_history(
                options_ticker=options_ticker,
                timestamp_from=timestamp_from,
                timestamp_to=timestamp_to,
                limit=limit,
            )

            results = data.get("results", [])

            return {
                "options_ticker": options_ticker,
                "time_range": {
                    "from": timestamp_from,
                    "to": timestamp_to,
                },
                "count": len(results),
                "quotes": results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_option_quotes_history",
                "options_ticker": options_ticker,
            },
        )
        raise


async def get_option_aggregates(
    options_ticker: str,
    multiplier: int,
    timespan: str,
    from_date: str,
    to_date: str,
    adjusted: bool = True,
) -> dict[str, Any]:
    """Get OHLCV aggregate bars for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        multiplier: Size of timespan multiplier (e.g., 1, 5, 15)
        timespan: Timespan unit (minute, hour, day, week, month)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        adjusted: Adjust for splits

    Returns:
        OHLCV bars data

    Raises:
        MassiveAPIError: If API request fails
    """
    try:
        settings = get_settings()
        async with MassiveClient(settings) as client:
            data = await client.get_option_aggregates(
                options_ticker=options_ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_date=from_date,
                to_date=to_date,
                adjusted=adjusted,
            )

            results = data.get("results", [])

            return {
                "options_ticker": options_ticker,
                "aggregation": {
                    "multiplier": multiplier,
                    "timespan": timespan,
                    "from": from_date,
                    "to": to_date,
                    "adjusted": adjusted,
                },
                "count": len(results),
                "bars": results,
            }
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "get_option_aggregates",
                "options_ticker": options_ticker,
            },
        )
        raise
