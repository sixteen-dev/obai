"""Portfolio risk computation engine."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..clients.fmp_client import FMPClient
from ..logging_config import get_logger
from ..models.position import CASH_SYMBOLS, AssetType, Position, WeightType
from ..models.risk import RiskMetrics

logger = get_logger(__name__)

# Minimum trading days required for meaningful risk metrics
MIN_DATA_POINTS = 30
MIN_SERIES_LENGTH = 2  # Minimum data points to compute returns
TRADING_DAYS_PER_YEAR = 252


async def _resolve_weights(
    positions: list[Position],
    fmp_client: FMPClient,
) -> list[tuple[str, float]]:
    """Resolve position weights to percentages regardless of weight type.

    For SHARES or DOLLARS positions without percentage weights, fetches
    current quotes to derive market values and percentage weights.

    Args:
        positions: List of portfolio positions.
        fmp_client: FMP client for quote lookups.

    Returns:
        List of (symbol, weight) tuples where weight is a float 0-1.

    """
    weight_types = {p.weight_type for p in positions}
    has_pct = WeightType.PERCENTAGE in weight_types
    has_absolute = weight_types & {WeightType.SHARES, WeightType.DOLLARS}

    # Reject mixed formats — ambiguous without total portfolio value
    if has_pct and has_absolute:
        msg = (
            "Mixed position formats (percentages + shares/dollars) are ambiguous "
            "for risk analysis. Use one format: all percentages, all share counts, "
            "or all dollar values."
        )
        raise ValueError(msg)

    # All percentage weights — use directly
    if has_pct:
        return [(p.symbol, float(p.weight)) for p in positions if p.weight > 0]

    # All absolute (shares or dollars) — fetch quotes and compute weights
    symbols_needing_quotes = [p.symbol for p in positions]
    quotes = await fmp_client.get_quotes_batch(symbols_needing_quotes)

    market_values: dict[str, float] = {}
    for pos in positions:
        quote = quotes.get(pos.symbol)
        price = float(quote.get("price", 0)) if quote else 0.0

        if pos.weight_type == WeightType.SHARES and pos.shares:
            market_values[pos.symbol] = float(pos.shares) * price
        elif pos.weight_type == WeightType.DOLLARS and pos.dollar_value:
            market_values[pos.symbol] = float(pos.dollar_value)
        else:
            market_values[pos.symbol] = price  # single share fallback

    total_value = sum(market_values.values())
    if total_value == 0:
        # Falling back to equal weights silently produces a *different*
        # portfolio than the user supplied. Surface this loudly so the caller
        # can surface "we couldn't price these holdings" instead of analyzing
        # a fabricated portfolio.
        missing = [pos.symbol for pos in positions if market_values.get(pos.symbol, 0.0) <= 0]
        msg = (
            "Portfolio could not be analyzed: no quotes were available for "
            f"{', '.join(missing) or 'any supplied symbol'}. Re-check the "
            "symbols or supply percentage weights directly."
        )
        raise ValueError(msg)

    return [(sym, val / total_value) for sym, val in market_values.items()]


def _align_price_series(
    price_data: dict[str, list[dict[str, Any]]],
    benchmark_data: list[dict[str, Any]],
) -> tuple[list[str], dict[str, NDArray[np.float64]], NDArray[np.float64]]:
    """Align price series across all symbols to common dates.

    Args:
        price_data: Map of symbol to list of price dicts.
        benchmark_data: Benchmark price data list.

    Returns:
        Tuple of (aligned_dates, {symbol: close_prices_array}, benchmark_closes).

    """
    # Build date->close maps
    bench_map = {d["date"]: float(d["close"]) for d in benchmark_data}

    symbol_maps: dict[str, dict[str, float]] = {}
    for symbol, data in price_data.items():
        symbol_maps[symbol] = {d["date"]: float(d["close"]) for d in data}

    # Find common dates (inner join)
    common_dates = set(bench_map.keys())
    for smap in symbol_maps.values():
        common_dates &= set(smap.keys())

    sorted_dates = sorted(common_dates)

    symbol_arrays: dict[str, NDArray[np.float64]] = {}
    for symbol, smap in symbol_maps.items():
        symbol_arrays[symbol] = np.array([smap[d] for d in sorted_dates], dtype=np.float64)

    bench_array = np.array([bench_map[d] for d in sorted_dates], dtype=np.float64)

    return sorted_dates, symbol_arrays, bench_array


def _compute_returns(prices: NDArray[np.float64]) -> NDArray[np.float64]:
    """Compute daily returns from price series.

    Args:
        prices: Array of daily closing prices.

    Returns:
        Array of daily returns (length = len(prices) - 1).

    """
    return np.diff(prices) / prices[:-1]


def _compute_max_drawdown(
    cumulative: NDArray[np.floating[Any]],
    dates: list[str],
) -> tuple[float, float, str, str]:
    """Compute max drawdown and current drawdown from cumulative returns.

    Args:
        cumulative: Cumulative return series (1 + r_1)(1 + r_2)...
        dates: Corresponding date strings (one longer than returns).

    Returns:
        Tuple of (max_drawdown, current_drawdown, dd_start_date, dd_end_date).

    """
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - running_max) / running_max

    max_dd_idx = int(np.argmin(drawdowns))
    max_dd = float(drawdowns[max_dd_idx])

    # Find the peak before this trough
    peak_idx = int(np.argmax(cumulative[: max_dd_idx + 1]))

    # +1 because dates includes day 0 but returns start at day 1
    dd_start = dates[peak_idx] if peak_idx < len(dates) else dates[0]
    dd_end = dates[max_dd_idx] if max_dd_idx < len(dates) else dates[-1]

    current_dd = float(drawdowns[-1])

    return max_dd, current_dd, dd_start, dd_end


async def compute_portfolio_risk(
    positions: list[Position],
    fmp_client: FMPClient,
    benchmark: str = "SPY",
    lookback_days: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float | None = None,
) -> RiskMetrics:
    """Compute portfolio risk metrics from held instrument price history.

    Args:
        positions: List of portfolio positions.
        fmp_client: FMP client for data fetching.
        benchmark: Benchmark symbol for beta/correlation.
        lookback_days: Number of trading days to look back.
        risk_free_rate: Annual risk-free rate (None = fetch from treasury).

    Returns:
        Populated RiskMetrics dataclass.

    """
    warnings: list[str] = []

    # Step 0: Resolve weights
    weighted_positions = await _resolve_weights(positions, fmp_client)
    symbols = [sym for sym, _ in weighted_positions]

    # Step 1: Date range
    end_date = date.today()
    # Use ~1.5x calendar days to account for weekends/holidays
    start_date = end_date - timedelta(days=int(lookback_days * 1.5))
    from_str = start_date.isoformat()
    to_str = end_date.isoformat()

    # Step 2: Fetch prices
    all_symbols = [*symbols, benchmark]
    price_data = await fmp_client.get_historical_prices_multi(all_symbols, from_str, to_str)

    # Identify cash positions (zero return, dampens portfolio risk)
    cash_syms = {
        sym
        for sym, _ in weighted_positions
        if sym.upper() in CASH_SYMBOLS
        or any(p.asset_type == AssetType.CASH for p in positions if p.symbol == sym)
    }
    cash_weight = sum(w for sym, w in weighted_positions if sym in cash_syms)
    if cash_weight > 0:
        logger.info("Cash weight %.2f%% dampens portfolio risk", cash_weight * 100)

    # Check for missing equity (non-cash) symbols
    equity_positions = [(sym, w) for sym, w in weighted_positions if sym not in cash_syms]
    missing = [s for s, _ in equity_positions if s not in price_data]
    if missing:
        warnings.append(f"No price data for: {', '.join(missing)}")

    if benchmark not in price_data:
        warnings.append(f"No benchmark data for {benchmark}")

    # Filter to equity symbols with price data (skip cash — it has zero return)
    available = [(sym, w) for sym, w in equity_positions if sym in price_data]
    if not available:
        return _empty_risk_metrics(lookback_days, from_str, to_str, warnings)

    # Step 3: Risk-free rate
    if risk_free_rate is None:
        rfr_decimal = await fmp_client.get_risk_free_rate()
        risk_free_rate = float(rfr_decimal)

    # Do NOT renormalize — cash weight dampens returns/risk naturally
    avail_symbols = [s for s, _ in available]
    avail_weights = np.array([w for _, w in available], dtype=np.float64)

    # Build price data dict (excluding benchmark)
    sym_price_data = {s: price_data[s] for s in avail_symbols}

    bench_data = price_data.get(benchmark, [])
    if not bench_data:
        # Use first symbol as pseudo-benchmark (beta=1)
        bench_data = price_data[avail_symbols[0]]
        warnings.append(f"Using {avail_symbols[0]} as pseudo-benchmark (no {benchmark} data)")

    # Step 4: Align dates
    aligned_dates, symbol_arrays, bench_closes = _align_price_series(sym_price_data, bench_data)

    if len(aligned_dates) < MIN_DATA_POINTS:
        warnings.append(
            f"Only {len(aligned_dates)} overlapping data points "
            f"(minimum recommended: {MIN_DATA_POINTS})"
        )

    if len(aligned_dates) < MIN_SERIES_LENGTH:
        return _empty_risk_metrics(lookback_days, from_str, to_str, warnings)

    # Step 5-6: Compute returns and portfolio return series
    symbol_returns: dict[str, NDArray[np.float64]] = {}
    for sym, prices in symbol_arrays.items():
        symbol_returns[sym] = _compute_returns(prices)

    bench_returns = _compute_returns(bench_closes)

    # Weighted portfolio returns
    port_returns = np.zeros(len(bench_returns), dtype=np.float64)
    for sym, wt in zip(avail_symbols, avail_weights, strict=True):
        if sym in symbol_returns:
            port_returns += wt * symbol_returns[sym]

    # Step 7: Compute metrics
    return _build_risk_metrics(
        port_returns=port_returns,
        bench_returns=bench_returns,
        risk_free_rate=risk_free_rate,
        lookback_days=lookback_days,
        aligned_dates=aligned_dates,
        warnings=warnings,
    )


def _build_risk_metrics(  # noqa: PLR0913
    port_returns: NDArray[np.float64],
    bench_returns: NDArray[np.float64],
    risk_free_rate: float,
    lookback_days: int,
    aligned_dates: list[str],
    warnings: list[str],
) -> RiskMetrics:
    """Build RiskMetrics from computed return series.

    Args:
        port_returns: Portfolio daily return series.
        bench_returns: Benchmark daily return series.
        risk_free_rate: Annual risk-free rate.
        lookback_days: Requested lookback period.
        aligned_dates: Aligned date strings.
        warnings: Accumulated warnings.

    Returns:
        Populated RiskMetrics.

    """
    daily_vol = float(np.std(port_returns, ddof=1))
    annual_vol = daily_vol * np.sqrt(TRADING_DAYS_PER_YEAR)

    # Sharpe ratio
    mean_daily = float(np.mean(port_returns))
    annualized_mean = mean_daily * TRADING_DAYS_PER_YEAR
    sharpe = (annualized_mean - risk_free_rate) / annual_vol if annual_vol > 0 else 0.0

    # Sortino ratio (downside deviation)
    negative_returns = port_returns[port_returns < 0]
    if len(negative_returns) > 0:
        downside_dev = float(np.std(negative_returns, ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
        sortino = (annualized_mean - risk_free_rate) / downside_dev if downside_dev > 0 else 0.0
    else:
        sortino = 0.0

    # Beta and R-squared
    if len(bench_returns) >= MIN_SERIES_LENGTH:
        cov_matrix = np.cov(port_returns, bench_returns)
        bench_var = float(np.var(bench_returns, ddof=1))
        beta = float(cov_matrix[0][1]) / bench_var if bench_var > 0 else 1.0
        corr = np.corrcoef(port_returns, bench_returns)
        r_squared = float(corr[0][1] ** 2)
    else:
        beta = 1.0
        r_squared = 0.0

    # Max drawdown
    cumulative = np.cumprod(1 + port_returns)
    # Use dates starting from index 1 (since returns are diff-based)
    return_dates = aligned_dates[1:]
    max_dd, current_dd, dd_start, dd_end = _compute_max_drawdown(cumulative, return_dates)

    # Total return and annualized return (CAGR)
    total_return = float(cumulative[-1]) - 1.0
    n_years = len(port_returns) / TRADING_DAYS_PER_YEAR
    if n_years > 0 and cumulative[-1] > 0:
        annualized_return = float(cumulative[-1] ** (1 / n_years)) - 1.0
    else:
        annualized_return = 0.0

    # VaR (95% confidence)
    var_95 = float(np.percentile(port_returns, 5))

    # Calmar ratio
    calmar = annualized_return / abs(max_dd) if max_dd != 0 else 0.0

    return RiskMetrics(
        annualized_volatility=Decimal(str(round(annual_vol, 6))),
        daily_volatility=Decimal(str(round(daily_vol, 6))),
        beta=Decimal(str(round(beta, 4))),
        r_squared=Decimal(str(round(r_squared, 4))),
        max_drawdown=Decimal(str(round(max_dd, 6))),
        max_drawdown_start=dd_start,
        max_drawdown_end=dd_end,
        current_drawdown=Decimal(str(round(current_dd, 6))),
        total_return=Decimal(str(round(total_return, 6))),
        annualized_return=Decimal(str(round(annualized_return, 6))),
        sharpe_ratio=Decimal(str(round(sharpe, 4))),
        sortino_ratio=Decimal(str(round(sortino, 4))),
        var_95=Decimal(str(round(var_95, 6))),
        calmar_ratio=Decimal(str(round(calmar, 4))),
        lookback_days=lookback_days,
        data_start=aligned_dates[0] if aligned_dates else "",
        data_end=aligned_dates[-1] if aligned_dates else "",
        warnings=warnings,
    )


def _empty_risk_metrics(
    lookback_days: int,
    data_start: str,
    data_end: str,
    warnings: list[str],
) -> RiskMetrics:
    """Build an empty RiskMetrics for edge cases with no usable data.

    Args:
        lookback_days: Requested lookback period.
        data_start: Requested start date.
        data_end: Requested end date.
        warnings: Accumulated warnings.

    Returns:
        RiskMetrics with zeroed-out values and warnings.

    """
    zero = Decimal("0")
    warnings.append("Insufficient data to compute risk metrics")
    return RiskMetrics(
        annualized_volatility=zero,
        daily_volatility=zero,
        beta=zero,
        r_squared=zero,
        max_drawdown=zero,
        max_drawdown_start="",
        max_drawdown_end="",
        current_drawdown=zero,
        total_return=zero,
        annualized_return=zero,
        sharpe_ratio=zero,
        lookback_days=lookback_days,
        data_start=data_start,
        data_end=data_end,
        warnings=warnings,
    )


async def compute_correlation_matrix(
    positions: list[Position],
    fmp_client: FMPClient,
    lookback_days: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any]:
    """Compute correlation matrix between held instruments.

    Args:
        positions: List of portfolio positions.
        fmp_client: FMP client for data fetching.
        lookback_days: Number of trading days to look back.

    Returns:
        Dict with 'symbols', 'matrix' (list of lists), and 'warnings'.

    """
    warnings: list[str] = []
    symbols = [p.symbol for p in positions]

    end_date = date.today()
    start_date = end_date - timedelta(days=int(lookback_days * 1.5))
    from_str = start_date.isoformat()
    to_str = end_date.isoformat()

    price_data = await fmp_client.get_historical_prices_multi(symbols, from_str, to_str)

    available = [s for s in symbols if s in price_data]
    if len(available) < MIN_SERIES_LENGTH:
        warnings.append("Need at least 2 symbols with data for correlation")
        return {"symbols": available, "matrix": [], "warnings": warnings}

    # Build aligned date->close maps
    date_maps = {s: {d["date"]: float(d["close"]) for d in price_data[s]} for s in available}

    # Find common dates
    common_dates = set(date_maps[available[0]].keys())
    for sym in available[1:]:
        common_dates &= set(date_maps[sym].keys())

    sorted_dates = sorted(common_dates)
    if len(sorted_dates) < MIN_DATA_POINTS:
        warnings.append(f"Only {len(sorted_dates)} common data points")

    if len(sorted_dates) < MIN_SERIES_LENGTH:
        return {"symbols": available, "matrix": [], "warnings": warnings}

    # Build return matrix
    returns_matrix = []
    for sym in available:
        prices = np.array([date_maps[sym][d] for d in sorted_dates], dtype=np.float64)
        returns_matrix.append(_compute_returns(prices))

    corr_result = np.corrcoef(returns_matrix)
    # corrcoef returns a matrix for 2+ series (guaranteed by MIN_SERIES_LENGTH check)
    corr_matrix: NDArray[np.float64] = np.asarray(corr_result, dtype=np.float64)

    return {
        "symbols": available,
        "matrix": [[round(float(v), 4) for v in row] for row in corr_matrix],
        "warnings": warnings,
    }
