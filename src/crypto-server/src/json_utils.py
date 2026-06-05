"""JSON canonicalization helpers."""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

FLOAT_QUANTUM = Decimal("0.000000000001")


def canonical_json(value: dict[str, Any]) -> str:
    """Canonical JSON used for fingerprints."""
    return _canonical_json(value)


def _canonical_json(value: Any) -> str:
    if value is None:
        result = "null"
    elif isinstance(value, bool):
        result = "true" if value else "false"
    elif isinstance(value, str):
        result = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, int):
        result = str(value)
    elif isinstance(value, float):
        result = _format_float(value)
    elif isinstance(value, list | tuple):
        result = "[" + ",".join(_canonical_json(item) for item in value) + "]"
    elif isinstance(value, dict):
        result = _canonical_dict(value)
    else:
        msg = f"Unsupported type for canonical JSON: {type(value).__name__}"
        raise TypeError(msg)
    return result


def _canonical_dict(value: dict[Any, Any]) -> str:
    items: list[tuple[str, Any]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"Canonical JSON object keys must be strings, got {type(key).__name__}"
            raise TypeError(msg)
        items.append((key, item))
    parts = [
        json.dumps(key, ensure_ascii=False) + ":" + _canonical_json(item)
        for key, item in sorted(items, key=lambda pair: pair[0])
    ]
    return "{" + ",".join(parts) + "}"


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        msg = "Canonical JSON does not support NaN or infinite floats"
        raise ValueError(msg)
    decimal = Decimal(str(value)).quantize(FLOAT_QUANTUM, rounding=ROUND_HALF_EVEN)
    decimal = decimal.normalize()
    if decimal == decimal.to_integral_value():
        return str(int(decimal))
    text = format(decimal, "f")
    return text.rstrip("0").rstrip(".")
