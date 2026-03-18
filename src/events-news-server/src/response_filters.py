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


def filter_news(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter news articles to essential fields."""
    return _apply_filter(data, "news")


def filter_earnings(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter earnings data to essential fields."""
    return _apply_filter(data, "earnings")


def filter_dividends(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter dividends data to essential fields."""
    return _apply_filter(data, "dividends")
