"""Shared utility functions for the backtest engine."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def date_to_str(val: Any) -> str:
    """Convert a date or datetime value to ISO string.

    Args:
        val: Date, datetime, or other value.

    Returns:
        ISO format string representation.

    """
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val)
