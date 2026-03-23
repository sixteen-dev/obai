"""Allocation analysis models."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class ConcentrationMetrics:
    """Portfolio concentration measures."""

    top_5_weight: Decimal  # Sum of top 5 holdings
    top_10_weight: Decimal  # Sum of top 10 holdings
    herfindahl_index: Decimal  # HHI = sum(w_i^2)
    effective_positions: int  # 1 / HHI (diversification measure)

    def to_dict(self) -> dict[str, float | int]:
        """Convert to dictionary for JSON serialization."""
        return {
            "top_5_weight": float(self.top_5_weight),
            "top_10_weight": float(self.top_10_weight),
            "herfindahl_index": float(self.herfindahl_index),
            "effective_positions": self.effective_positions,
        }


@dataclass
class AllocationBreakdown:
    """Allocation analysis result."""

    by_ticker: dict[str, Decimal]  # After ETF expansion (look-through)
    by_sector: dict[str, Decimal]
    by_asset_class: dict[str, Decimal]  # Equity, Fixed Income, Cash
    top_holdings: list[tuple[str, Decimal]]  # Top 10 by weight
    concentration: ConcentrationMetrics
    by_ticker_held: dict[str, Decimal] = field(
        default_factory=dict
    )  # Pre-expansion weights (held instruments view)
    etf_attribution: dict[str, list[str]] = field(
        default_factory=dict
    )  # stock -> [contributing ETFs]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "by_ticker": {k: float(v) for k, v in self.by_ticker.items()},
            "by_ticker_held": {k: float(v) for k, v in self.by_ticker_held.items()},
            "by_sector": {k: float(v) for k, v in self.by_sector.items()},
            "by_asset_class": {k: float(v) for k, v in self.by_asset_class.items()},
            "top_holdings": [[symbol, float(weight)] for symbol, weight in self.top_holdings],
            "concentration": self.concentration.to_dict(),
            "etf_attribution": self.etf_attribution,
        }
