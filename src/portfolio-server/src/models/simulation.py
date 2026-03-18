"""Monte Carlo simulation models."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .risk import Assumptions


@dataclass
class HistogramBucket:
    """A single bucket in the terminal value histogram."""

    range_start: Decimal
    range_end: Decimal
    count: int
    percentage: Decimal

    def to_dict(self) -> dict[str, float | int]:
        """Convert to dictionary for JSON serialization."""
        return {
            "range_start": float(self.range_start),
            "range_end": float(self.range_end),
            "count": self.count,
            "percentage": float(self.percentage),
        }


@dataclass
class MonteCarloResult:
    """Monte Carlo simulation output."""

    # Terminal Value Distribution
    initial_value: Decimal  # Starting portfolio value (normalized to 1.0)
    median_terminal: Decimal
    p5_terminal: Decimal  # 5th percentile (bad case)
    p25_terminal: Decimal
    p75_terminal: Decimal
    p95_terminal: Decimal  # 95th percentile (good case)

    # Probability Metrics
    probability_of_loss: Decimal  # P(terminal < initial)
    probability_gain_20pct: Decimal  # P(terminal > 1.2 * initial)

    # Return Metrics
    expected_cagr: Decimal  # Mean annualized return across sims
    median_cagr: Decimal

    # Distribution (for histogram)
    histogram_buckets: list[HistogramBucket] = field(default_factory=list)

    # Metadata
    num_simulations: int = 5000
    horizon_years: int = 10
    method: str = "parametric_covariance"  # or "historical_bootstrap"
    assumptions: Assumptions | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "initial_value": float(self.initial_value),
            "median_terminal": float(self.median_terminal),
            "p5_terminal": float(self.p5_terminal),
            "p25_terminal": float(self.p25_terminal),
            "p75_terminal": float(self.p75_terminal),
            "p95_terminal": float(self.p95_terminal),
            "probability_of_loss": float(self.probability_of_loss),
            "probability_gain_20pct": float(self.probability_gain_20pct),
            "expected_cagr": float(self.expected_cagr),
            "median_cagr": float(self.median_cagr),
            "histogram_buckets": [b.to_dict() for b in self.histogram_buckets],
            "num_simulations": self.num_simulations,
            "horizon_years": self.horizon_years,
            "method": self.method,
            "assumptions": self.assumptions.to_dict() if self.assumptions else None,
        }
