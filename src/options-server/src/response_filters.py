"""Response filtering to reduce token usage by removing unnecessary fields.

Filter rules are configured in filter_config.yaml at the project root.
Edit that file to customize which fields are kept or removed.
"""

from pathlib import Path
from typing import Any, cast

import yaml


def _load_filter_config() -> dict[str, Any]:
    """Load filter configuration from YAML file and pre-compute sets.

    Returns:
        Filter configuration dictionary with field lists converted to sets

    Raises:
        FileNotFoundError: If filter_config.yaml doesn't exist
        yaml.YAMLError: If config file is invalid
    """
    config_path = Path(__file__).parent.parent / "filter_config.yaml"
    with open(config_path) as f:
        config = cast(dict[str, Any], yaml.safe_load(f))

    filters = cast(dict[str, Any], config["filters"])

    # Pre-convert field lists to sets for O(1) lookups instead of O(n)
    for _filter_name, filter_config in filters.items():
        if "keep_fields" in filter_config:
            filter_config["keep_fields"] = set(filter_config["keep_fields"])
        if "remove_fields" in filter_config:
            filter_config["remove_fields"] = set(filter_config["remove_fields"])

    return filters


# Load config once at module import with pre-computed sets
_FILTERS = _load_filter_config()


def _apply_dict_filter(data: dict[str, Any], filter_name: str) -> dict[str, Any]:
    """Apply filter to a single dictionary.

    Args:
        data: Dictionary to filter
        filter_name: Name of filter in config

    Returns:
        Filtered dictionary
    """
    filter_config = _FILTERS.get(filter_name, {})

    # Handle use_filter reference
    if "use_filter" in filter_config:
        filter_name = filter_config["use_filter"]
        filter_config = _FILTERS.get(filter_name, {})

    # If keep_all is True, return data unchanged
    if filter_config.get("keep_all", False):
        return data

    # If keep_fields is specified, keep only those fields (already a set)
    if "keep_fields" in filter_config:
        keep_fields: set[str] = filter_config["keep_fields"]
        return {k: v for k, v in data.items() if k in keep_fields}

    # If remove_fields is specified, remove those fields (already a set)
    if "remove_fields" in filter_config:
        remove_fields: set[str] = filter_config["remove_fields"]
        return {k: v for k, v in data.items() if k not in remove_fields}

    # No filter specified, return unchanged
    return data


def _apply_list_filter(data: list[dict[str, Any]], filter_name: str) -> list[dict[str, Any]]:
    """Apply filter to a list of dictionaries.

    Args:
        data: List of dictionaries to filter
        filter_name: Name of filter in config

    Returns:
        Filtered list
    """
    return [_apply_dict_filter(item, filter_name) for item in data]


# =============================================================================
# Massive Options Snapshot Filters
# =============================================================================


def filter_option_chain_snapshot(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter option chain snapshot data to essential fields.

    Massive returns nested structures for each contract. We flatten and filter
    to keep only the most relevant trading data.

    Args:
        data: Raw Massive option chain snapshot results

    Returns:
        Filtered list of contract snapshots
    """
    filtered_results: list[dict[str, Any]] = []

    for contract in data:
        filtered_contract: dict[str, Any] = {}

        # Extract details (contract metadata)
        details = contract.get("details", {})
        if details:
            filtered_contract["ticker"] = details.get("ticker")
            filtered_contract["strike_price"] = details.get("strike_price")
            filtered_contract["expiration_date"] = details.get("expiration_date")
            filtered_contract["contract_type"] = details.get("contract_type")

        # Extract last trade
        last_trade = contract.get("last_trade", {})
        if last_trade:
            filtered_contract["last_trade"] = {
                "price": last_trade.get("price"),
                "size": last_trade.get("size"),
                "sip_timestamp": last_trade.get("sip_timestamp"),
            }

        # Extract last quote
        last_quote = contract.get("last_quote", {})
        if last_quote:
            filtered_contract["last_quote"] = {
                "bid": last_quote.get("bid"),
                "ask": last_quote.get("ask"),
                "bid_size": last_quote.get("bid_size"),
                "ask_size": last_quote.get("ask_size"),
            }

        # Extract Greeks
        greeks = contract.get("greeks", {})
        if greeks:
            filtered_contract["greeks"] = {
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
            }

        # Extract key metrics
        filtered_contract["implied_volatility"] = contract.get("implied_volatility")
        filtered_contract["open_interest"] = contract.get("open_interest")
        filtered_contract["break_even_price"] = contract.get("break_even_price")

        # Extract underlying asset price context
        underlying = contract.get("underlying_asset", {})
        if underlying:
            filtered_contract["underlying_price"] = underlying.get("price")

        filtered_results.append(filtered_contract)

    return filtered_results


def filter_option_contract_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Filter single option contract snapshot.

    Args:
        data: Raw Massive contract snapshot

    Returns:
        Filtered contract snapshot
    """
    if not data:
        return {}

    # Reuse the chain filter logic for a single contract
    filtered_list = filter_option_chain_snapshot([data])
    return filtered_list[0] if filtered_list else {}


# =============================================================================
# Massive Trade/Quote Filters
# =============================================================================


def filter_option_trade(data: dict[str, Any]) -> dict[str, Any]:
    """Filter option trade data to essential fields.

    Args:
        data: Raw Massive trade result

    Returns:
        Filtered trade data
    """
    if not data:
        return {}

    return {
        "price": data.get("price"),
        "size": data.get("size"),
        "exchange": data.get("exchange"),
        "sip_timestamp": data.get("sip_timestamp"),
        "conditions": data.get("conditions"),
    }


def filter_option_quote(data: dict[str, Any]) -> dict[str, Any]:
    """Filter option quote (NBBO) data to essential fields.

    Args:
        data: Raw Massive quote result

    Returns:
        Filtered quote data
    """
    if not data:
        return {}

    return {
        "bid_price": data.get("bid_price") or data.get("bid"),
        "bid_size": data.get("bid_size"),
        "ask_price": data.get("ask_price") or data.get("ask"),
        "ask_size": data.get("ask_size"),
        "bid_exchange": data.get("bid_exchange"),
        "ask_exchange": data.get("ask_exchange"),
        "sip_timestamp": data.get("sip_timestamp"),
    }


# =============================================================================
# Massive Reference Data Filters
# =============================================================================


def filter_option_contracts_list(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter option contracts reference list.

    Args:
        data: Raw Massive contracts list

    Returns:
        Filtered list of contract references
    """
    return _apply_list_filter(data, "option_contracts_list")
