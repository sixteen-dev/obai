"""Storage layer for prediction-markets historical analytics.

Public surface:
    PredictionDuckDBManager — DuckDB connection + schema init.
    PredictionStore         — typed upsert/read helpers.
    MarketRow, TokenRow,
    PriceRow, TradeRow,
    MetaRow                  — row dataclasses.
    build_trade_key          — deterministic trade key composer (§8.4).
    infer_resolution,
    ResolutionResult         — 6-rule resolution waterfall (§8.1).
    fingerprint_universe,
    fingerprint_resolution,
    fingerprint_analysis     — deterministic SHA-256 fingerprints.
"""

from __future__ import annotations

from .db import PredictionDuckDBManager
from .fingerprint import (
    fingerprint_analysis,
    fingerprint_resolution,
    fingerprint_universe,
)
from .resolution import ResolutionResult, infer_resolution
from .store import (
    MarketRow,
    MetaRow,
    PredictionStore,
    PriceRow,
    TokenRow,
    TradeRow,
    build_trade_key,
)

__all__ = [
    "MarketRow",
    "MetaRow",
    "PredictionDuckDBManager",
    "PredictionStore",
    "PriceRow",
    "ResolutionResult",
    "TokenRow",
    "TradeRow",
    "build_trade_key",
    "fingerprint_analysis",
    "fingerprint_resolution",
    "fingerprint_universe",
    "infer_resolution",
]
