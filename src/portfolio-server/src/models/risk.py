"""Risk metrics models."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Assumptions:
    """Explicit assumptions for any calculation."""

    data_source: str  # "FMP", "market-data-server", etc.
    lookback_window: str  # "3y", "5y"
    return_frequency: str  # "daily", "monthly"
    risk_free_rate: Decimal
    benchmark: str  # "SPY"
    as_of_date: str
    etf_holdings_date: str | None = None  # When ETF data was last updated

    def to_dict(self) -> dict[str, str | float | None]:
        """Convert to dictionary for JSON serialization."""
        return {
            "data_source": self.data_source,
            "lookback_window": self.lookback_window,
            "return_frequency": self.return_frequency,
            "risk_free_rate": float(self.risk_free_rate),
            "benchmark": self.benchmark,
            "as_of_date": self.as_of_date,
            "etf_holdings_date": self.etf_holdings_date,
        }


@dataclass
class RiskMetrics:
    """Portfolio risk analysis result."""

    # Volatility
    annualized_volatility: Decimal  # Standard deviation of returns * sqrt(252)
    daily_volatility: Decimal

    # Beta & Market Risk
    beta: Decimal  # Regression beta to benchmark
    r_squared: Decimal  # How much variance explained by market

    # Drawdown
    max_drawdown: Decimal  # Worst peak-to-trough (negative number)
    max_drawdown_start: str  # Date
    max_drawdown_end: str
    current_drawdown: Decimal  # Current distance from peak

    # Returns (for context)
    total_return: Decimal  # Over lookback period
    annualized_return: Decimal
    sharpe_ratio: Decimal  # (return - rf) / volatility

    # Metadata
    lookback_days: int
    data_start: str
    data_end: str

    # Optional extended metrics
    sortino_ratio: Decimal | None = None
    var_95: Decimal | None = None  # 5th percentile daily return (Value at Risk)
    calmar_ratio: Decimal | None = None  # annualized_return / abs(max_drawdown)

    # Coverage: True when unpriceable holdings were dropped and the remaining
    # priced weights renormalized — metrics reflect the priced sub-portfolio only.
    coverage_incomplete: bool = False

    # Warnings
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, float | str | int | list[str] | None]:
        """Convert to dictionary for JSON serialization."""
        return {
            "annualized_volatility": float(self.annualized_volatility),
            "daily_volatility": float(self.daily_volatility),
            "beta": float(self.beta),
            "r_squared": float(self.r_squared),
            "max_drawdown": float(self.max_drawdown),
            "max_drawdown_start": self.max_drawdown_start,
            "max_drawdown_end": self.max_drawdown_end,
            "current_drawdown": float(self.current_drawdown),
            "total_return": float(self.total_return),
            "annualized_return": float(self.annualized_return),
            "sharpe_ratio": float(self.sharpe_ratio),
            "sortino_ratio": float(self.sortino_ratio) if self.sortino_ratio is not None else None,
            "var_95": float(self.var_95) if self.var_95 is not None else None,
            "calmar_ratio": float(self.calmar_ratio) if self.calmar_ratio is not None else None,
            "coverage_incomplete": self.coverage_incomplete,
            "lookback_days": self.lookback_days,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "warnings": self.warnings,
        }
