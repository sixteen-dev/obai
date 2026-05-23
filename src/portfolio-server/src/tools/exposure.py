"""Effective exposure calculation for portfolio look-through analysis."""

from typing import Any


def calculate_effective_exposure(
    positions: list[dict[str, Any]],
    etf_holdings_map: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Calculate effective exposure including ETF look-through.

    Args:
        positions: Parsed portfolio positions.
        etf_holdings_map: Map of ETF symbol to holdings list.

    Returns:
        List of exposures sorted by total weight descending.

    """
    exposure: dict[str, dict[str, float]] = {}

    for pos in positions:
        symbol = pos["symbol"]
        weight = pos.get("weight", 0) * 100  # Convert to percent
        asset_type = pos.get("asset_type", "stock")

        if asset_type == "etf" and symbol in etf_holdings_map:
            _add_etf_exposure(exposure, weight, etf_holdings_map[symbol])
        elif asset_type in ("stock", "unknown"):
            _add_direct_exposure(exposure, symbol, weight)
        elif asset_type in ("cash", "bond_etf"):
            # Cash and bond ETFs still occupy portfolio weight. Tracking
            # them as their own buckets keeps "total exposure" reflective
            # of the full economic portfolio so concentration flags aren't
            # computed against an incomplete denominator.
            _add_direct_exposure(exposure, symbol, weight)

    return _build_exposure_list(exposure)


def _add_etf_exposure(
    exposure: dict[str, dict[str, float]],
    etf_weight: float,
    holdings: list[dict[str, Any]],
) -> None:
    """Add look-through exposure for ETF holdings.

    Args:
        exposure: Exposure dict to update.
        etf_weight: Weight of ETF in portfolio (percent).
        holdings: List of ETF holdings with weight_percent.

    """
    for holding in holdings:
        underlying = holding["symbol"]
        underlying_weight = holding["weight_percent"]
        effective = (etf_weight / 100) * underlying_weight

        if underlying not in exposure:
            exposure[underlying] = {"direct": 0.0, "via_etf": 0.0}
        exposure[underlying]["via_etf"] += effective


def _add_direct_exposure(
    exposure: dict[str, dict[str, float]],
    symbol: str,
    weight: float,
) -> None:
    """Add direct stock exposure.

    Args:
        exposure: Exposure dict to update.
        symbol: Stock symbol.
        weight: Weight in portfolio (percent).

    """
    if symbol not in exposure:
        exposure[symbol] = {"direct": 0.0, "via_etf": 0.0}
    exposure[symbol]["direct"] += weight


def _build_exposure_list(
    exposure: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Build sorted exposure list from exposure dict.

    Args:
        exposure: Dict of symbol to direct/via_etf weights.

    Returns:
        Sorted list of exposure dicts.

    """
    result: list[dict[str, Any]] = []
    for symbol, exp in exposure.items():
        total = exp["direct"] + exp["via_etf"]
        result.append(
            {
                "symbol": symbol,
                "direct_percent": round(exp["direct"], 2),
                "via_etf_percent": round(exp["via_etf"], 2),
                "total_percent": round(total, 2),
            }
        )

    result.sort(key=lambda x: float(x["total_percent"]), reverse=True)
    return result


def generate_concentration_flags(
    effective_exposure: list[dict[str, Any]],
    concentration_threshold: float,
    top_n_threshold: float,
) -> list[str]:
    """Generate concentration risk flags.

    Args:
        effective_exposure: List of exposures with total_percent.
        concentration_threshold: Single stock threshold (percent).
        top_n_threshold: Top 3 holdings threshold (percent).

    Returns:
        List of concentration warning strings.

    """
    flags: list[str] = []

    # Single stock concentration
    for exp in effective_exposure:
        if exp["total_percent"] >= concentration_threshold:
            flags.append(_format_stock_flag(exp, concentration_threshold))

    # Top 3 concentration
    top_3_total = sum(e["total_percent"] for e in effective_exposure[:3])
    if top_3_total >= top_n_threshold:
        top_3_symbols = ", ".join(e["symbol"] for e in effective_exposure[:3])
        flags.append(f"Top 3 ({top_3_symbols}): {top_3_total:.1f}% exceeds {top_n_threshold}%")

    return flags


def _format_stock_flag(exp: dict[str, Any], threshold: float) -> str:
    """Format a single stock concentration flag.

    Args:
        exp: Exposure dict with symbol, total_percent, via_etf_percent, direct_percent.
        threshold: Threshold that was exceeded.

    Returns:
        Formatted warning string.

    """
    flag = f"{exp['symbol']}: {exp['total_percent']:.1f}% exceeds {threshold}%"
    if exp["via_etf_percent"] > 0:
        direct = exp["direct_percent"]
        via_etf = exp["via_etf_percent"]
        flag += f" ({direct:.1f}% direct + {via_etf:.1f}% via ETF)"
    return flag
