"""BacktestResult model - compact metrics that flow to the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestResult:
    """Compact backtest performance metrics (~50 lines JSON)."""

    # Identity
    strategy_name: str
    symbols: list[str]
    period: str

    # Performance
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float

    # Risk
    max_drawdown_pct: float
    max_drawdown_start: str
    max_drawdown_end: str
    annualized_volatility_pct: float
    var_95_pct: float
    downside_deviation_pct: float

    # Trading
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_trade_return_pct: float
    avg_holding_days: float
    max_consecutive_losses: int

    # Benchmark
    benchmark_symbol: str
    benchmark_return_pct: float
    benchmark_cagr_pct: float
    alpha_pct: float
    beta: float
    information_ratio: float

    # Breakdown
    yearly_returns: dict[str, float] = field(default_factory=dict)
    symbol_returns: dict[str, float] = field(default_factory=dict)

    # Meta
    execution_time_ms: int = 0
    data_points_processed: int = 0
    warnings: list[str] = field(default_factory=list)

    # Data quality (zero-cost observability — computed from in-memory data)
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "strategy_name": self.strategy_name,
            "symbols": self.symbols,
            "period": self.period,
            "performance": {
                "total_return_pct": self.total_return_pct,
                "cagr_pct": self.cagr_pct,
                "sharpe_ratio": self.sharpe_ratio,
                "sortino_ratio": self.sortino_ratio,
                "calmar_ratio": self.calmar_ratio,
            },
            "risk": {
                "max_drawdown_pct": self.max_drawdown_pct,
                "max_drawdown_start": self.max_drawdown_start,
                "max_drawdown_end": self.max_drawdown_end,
                "annualized_volatility_pct": self.annualized_volatility_pct,
                "var_95_pct": self.var_95_pct,
                "downside_deviation_pct": self.downside_deviation_pct,
            },
            "trading": {
                "total_trades": self.total_trades,
                "win_rate_pct": self.win_rate_pct,
                "profit_factor": self.profit_factor,
                "avg_trade_return_pct": self.avg_trade_return_pct,
                "avg_holding_days": self.avg_holding_days,
                "max_consecutive_losses": self.max_consecutive_losses,
            },
            "benchmark": {
                "benchmark_symbol": self.benchmark_symbol,
                "benchmark_return_pct": self.benchmark_return_pct,
                "benchmark_cagr_pct": self.benchmark_cagr_pct,
                "alpha_pct": self.alpha_pct,
                "beta": self.beta,
                "information_ratio": self.information_ratio,
            },
            "yearly_returns": self.yearly_returns,
            "symbol_returns": self.symbol_returns,
            "execution_time_ms": self.execution_time_ms,
            "data_points_processed": self.data_points_processed,
            "warnings": self.warnings,
            "data_quality": self.data_quality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BacktestResult:
        """Parse a dict (including nested structure) into BacktestResult."""
        perf = data.get("performance", {})
        risk = data.get("risk", {})
        trading = data.get("trading", {})
        bench = data.get("benchmark", {})

        return cls(
            strategy_name=data.get("strategy_name", ""),
            symbols=data.get("symbols", []),
            period=data.get("period", ""),
            total_return_pct=perf.get("total_return_pct", 0.0),
            cagr_pct=perf.get("cagr_pct", 0.0),
            sharpe_ratio=perf.get("sharpe_ratio", 0.0),
            sortino_ratio=perf.get("sortino_ratio", 0.0),
            calmar_ratio=perf.get("calmar_ratio", 0.0),
            max_drawdown_pct=risk.get("max_drawdown_pct", 0.0),
            max_drawdown_start=risk.get("max_drawdown_start", ""),
            max_drawdown_end=risk.get("max_drawdown_end", ""),
            annualized_volatility_pct=risk.get("annualized_volatility_pct", 0.0),
            var_95_pct=risk.get("var_95_pct", 0.0),
            downside_deviation_pct=risk.get("downside_deviation_pct", 0.0),
            total_trades=trading.get("total_trades", 0),
            win_rate_pct=trading.get("win_rate_pct", 0.0),
            profit_factor=trading.get("profit_factor", 0.0),
            avg_trade_return_pct=trading.get("avg_trade_return_pct", 0.0),
            avg_holding_days=trading.get("avg_holding_days", 0.0),
            max_consecutive_losses=trading.get("max_consecutive_losses", 0),
            benchmark_symbol=bench.get("benchmark_symbol", ""),
            benchmark_return_pct=bench.get("benchmark_return_pct", 0.0),
            benchmark_cagr_pct=bench.get("benchmark_cagr_pct", 0.0),
            alpha_pct=bench.get("alpha_pct", 0.0),
            beta=bench.get("beta", 0.0),
            information_ratio=bench.get("information_ratio", 0.0),
            yearly_returns=data.get("yearly_returns", {}),
            symbol_returns=data.get("symbol_returns", {}),
            execution_time_ms=data.get("execution_time_ms", 0),
            data_points_processed=data.get("data_points_processed", 0),
            warnings=data.get("warnings", []),
            data_quality=data.get("data_quality", {}),
        )
