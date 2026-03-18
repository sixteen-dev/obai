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
# Stock Screening Filters
# =============================================================================


def filter_screen_results(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter stock screening results to essential fields.

    Args:
        data: Raw FMP screening results

    Returns:
        Filtered list of stock results
    """
    return _apply_list_filter(data, "screen_stocks")


def filter_search_results(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter company search results to essential fields.

    Args:
        data: Raw FMP search results

    Returns:
        Filtered list of company results
    """
    return _apply_list_filter(data, "search_company")
