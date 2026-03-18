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


def _apply_filter(data: list[dict[str, Any]], filter_name: str) -> list[dict[str, Any]]:
    """Apply filter based on config.

    Args:
        data: List of dictionaries to filter
        filter_name: Name of filter in config

    Returns:
        Filtered data
    """
    filter_config = _FILTERS.get(filter_name, {})

    # If keep_all is True, return data unchanged
    if filter_config.get("keep_all", False):
        return data

    # If keep_fields is specified, keep only those fields (already a set)
    if "keep_fields" in filter_config:
        keep_fields: set[str] = filter_config["keep_fields"]
        return [{k: v for k, v in item.items() if k in keep_fields} for item in data]

    # If remove_fields is specified, remove those fields (already a set)
    if "remove_fields" in filter_config:
        remove_fields: set[str] = filter_config["remove_fields"]
        return [{k: v for k, v in item.items() if k not in remove_fields} for item in data]

    # No filter specified, return unchanged
    return data


def filter_company_profile(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter company profile to essential fields only."""
    return _apply_filter(data, "company_profile")


def filter_financial_statement(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter financial statements to remove redundant metadata."""
    return _apply_filter(data, "financial_statement")


def filter_key_metrics(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter key metrics to remove redundant fields."""
    return _apply_filter(data, "key_metrics")


def filter_financial_ratios(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter financial ratios to remove redundant fields."""
    return _apply_filter(data, "financial_ratios")


def filter_analyst_estimates(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter analyst estimates - keep all estimate data as it's all relevant."""
    return _apply_filter(data, "analyst_estimates")


def filter_price_target(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter price target summary - keep all as it's concise and all relevant."""
    return _apply_filter(data, "price_target")


def filter_company_rating(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter company rating - keep all as ratings are concise and all relevant."""
    return _apply_filter(data, "company_rating")


def filter_sec_filings(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter SEC filings to essential filing information."""
    return _apply_filter(data, "sec_filings")


def filter_insider_trades(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter insider trades to transaction details relevant for analysis."""
    return _apply_filter(data, "insider_trades")


def filter_revenue_segments(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter revenue segments - keep all as product-level data is all relevant."""
    return _apply_filter(data, "revenue_segments")
