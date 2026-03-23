"""Portfolio allocation breakdown computation engine."""

from decimal import Decimal
from typing import Any

from ..clients.fmp_client import FMPClient
from ..logging_config import get_logger
from ..models.allocation import AllocationBreakdown, ConcentrationMetrics
from ..models.position import AssetType, Position, WeightType
from ..tools.exposure import calculate_effective_exposure

logger = get_logger(__name__)

# Number of top holdings to include
TOP_N = 10


def _validate_uniform_weight_types(positions: list[Position]) -> None:
    """Reject mixed percentage + shares/dollars inputs.

    Args:
        positions: List of portfolio positions.

    Raises:
        ValueError: If positions mix percentage and absolute formats.

    """
    weight_types = {p.weight_type for p in positions}
    has_pct = WeightType.PERCENTAGE in weight_types
    has_absolute = weight_types & {WeightType.SHARES, WeightType.DOLLARS}
    if has_pct and has_absolute:
        msg = (
            "Mixed position formats (percentages + shares/dollars) are ambiguous "
            "for allocation analysis. Use one format: all percentages, all share "
            "counts, or all dollar values."
        )
        raise ValueError(msg)


def _build_held_weights(positions: list[Position]) -> dict[str, Decimal]:
    """Build pre-expansion weight map from held instruments.

    Args:
        positions: List of portfolio positions.

    Returns:
        Map of symbol to weight (Decimal 0-1).

    """
    return {p.symbol: p.weight for p in positions if p.weight > 0}


def _build_positions_as_dicts(positions: list[Position]) -> list[dict[str, Any]]:
    """Convert Position objects to dicts matching exposure tool expectations.

    Args:
        positions: List of Position objects.

    Returns:
        List of position dicts with symbol, weight, and asset_type.

    """
    return [
        {
            "symbol": p.symbol,
            "weight": float(p.weight),
            "asset_type": p.asset_type.value,
        }
        for p in positions
    ]


def _build_look_through_weights(
    effective_exposure: list[dict[str, Any]],
) -> dict[str, Decimal]:
    """Convert effective exposure list to a weight dict.

    Args:
        effective_exposure: Exposure list from calculate_effective_exposure.

    Returns:
        Map of symbol to weight as Decimal (0-1 range).

    """
    result: dict[str, Decimal] = {}
    for exp in effective_exposure:
        # total_percent is in 0-100 range
        result[exp["symbol"]] = Decimal(str(exp["total_percent"])) / 100
    return result


async def _build_sector_breakdown(
    look_through: dict[str, Decimal],
    fmp_client: FMPClient,
) -> dict[str, Decimal]:
    """Aggregate weights by sector using company profiles.

    Args:
        look_through: Look-through weight map (symbol -> weight).
        fmp_client: FMP client for profile lookups.

    Returns:
        Map of sector name to aggregate weight.

    """
    symbols = list(look_through.keys())
    profiles = await fmp_client.get_company_profiles_batch(symbols)

    by_sector: dict[str, Decimal] = {}
    for symbol, weight in look_through.items():
        profile = profiles.get(symbol)
        sector = profile.get("sector", "Unknown") if profile else "Unknown"
        if not sector:
            sector = "Unknown"
        by_sector[sector] = by_sector.get(sector, Decimal("0")) + weight

    return dict(sorted(by_sector.items(), key=lambda x: x[1], reverse=True))


def _build_asset_class_breakdown(
    positions: list[Position],
    look_through: dict[str, Decimal],
) -> dict[str, Decimal]:
    """Aggregate weights by asset class.

    Uses the original position asset types. After look-through expansion,
    ETF weight gets reclassified as Equity.

    Args:
        positions: Original portfolio positions.
        look_through: Look-through weight map.

    Returns:
        Map of asset class name to aggregate weight.

    """
    by_class: dict[str, Decimal] = {}

    # Direct non-ETF positions keep their asset class
    for pos in positions:
        if pos.asset_type == AssetType.CASH:
            by_class["Cash"] = by_class.get("Cash", Decimal("0")) + pos.weight
        elif pos.asset_type == AssetType.BOND_ETF:
            by_class["Fixed Income"] = by_class.get("Fixed Income", Decimal("0")) + pos.weight
        elif pos.asset_type == AssetType.ETF:
            # ETF look-through is equity (for equity ETFs)
            by_class["Equity"] = by_class.get("Equity", Decimal("0")) + pos.weight
        else:
            # STOCK or UNKNOWN
            by_class["Equity"] = by_class.get("Equity", Decimal("0")) + pos.weight

    return dict(sorted(by_class.items(), key=lambda x: x[1], reverse=True))


def _compute_concentration(
    look_through: dict[str, Decimal],
) -> ConcentrationMetrics:
    """Compute concentration metrics from look-through weights.

    Args:
        look_through: Look-through weight map (symbol -> weight 0-1).

    Returns:
        ConcentrationMetrics with HHI, effective positions, top N weights.

    """
    sorted_weights = sorted(look_through.values(), reverse=True)

    hhi = sum((w**2 for w in sorted_weights), Decimal("0"))
    effective_positions = int(Decimal("1") / hhi) if hhi > 0 else 0
    top_5 = sum(sorted_weights[:5], Decimal("0"))
    top_10 = sum(sorted_weights[:TOP_N], Decimal("0"))

    return ConcentrationMetrics(
        top_5_weight=top_5,
        top_10_weight=top_10,
        herfindahl_index=hhi,
        effective_positions=effective_positions,
    )


def _build_etf_attribution(
    positions: list[Position],
    etf_holdings_map: dict[str, list[dict[str, Any]]],
) -> dict[str, list[str]]:
    """Map each underlying stock to the ETFs that contribute exposure.

    Args:
        positions: Original portfolio positions.
        etf_holdings_map: Map of ETF symbol to holdings list.

    Returns:
        Map of stock symbol to list of contributing ETF symbols.

    """
    attribution: dict[str, list[str]] = {}
    etf_symbols = {p.symbol for p in positions if p.asset_type == AssetType.ETF}

    for etf_symbol in etf_symbols:
        holdings = etf_holdings_map.get(etf_symbol, [])
        for holding in holdings:
            stock = holding.get("symbol", "")
            if stock:
                if stock not in attribution:
                    attribution[stock] = []
                if etf_symbol not in attribution[stock]:
                    attribution[stock].append(etf_symbol)

    return attribution


async def compute_allocation_breakdown(
    positions: list[Position],
    etf_holdings_map: dict[str, list[dict[str, Any]]],
    fmp_client: FMPClient,
) -> AllocationBreakdown:
    """Compute full allocation breakdown with look-through analysis.

    Args:
        positions: List of portfolio positions.
        etf_holdings_map: Map of ETF symbol to holdings list (from _fetch_etf_holdings).
        fmp_client: FMP client for sector lookups.

    Returns:
        Populated AllocationBreakdown dataclass.

    """
    # Step 0: Reject mixed input formats
    _validate_uniform_weight_types(positions)

    # Step 1: Pre-expansion weights
    by_ticker_held = _build_held_weights(positions)

    # Step 2: Look-through via effective exposure
    pos_dicts = _build_positions_as_dicts(positions)
    effective_exposure = calculate_effective_exposure(pos_dicts, etf_holdings_map)
    look_through = _build_look_through_weights(effective_exposure)

    # Step 3-4: Sector breakdown from profiles
    by_sector = await _build_sector_breakdown(look_through, fmp_client)

    # Step 5: Asset class breakdown
    by_asset_class = _build_asset_class_breakdown(positions, look_through)

    # Step 6: Top holdings
    sorted_holdings = sorted(look_through.items(), key=lambda x: x[1], reverse=True)
    top_holdings = sorted_holdings[:TOP_N]

    # Step 7: Concentration metrics
    concentration = _compute_concentration(look_through)

    # Step 8: ETF attribution
    etf_attribution = _build_etf_attribution(positions, etf_holdings_map)

    return AllocationBreakdown(
        by_ticker=look_through,
        by_sector=by_sector,
        by_asset_class=by_asset_class,
        top_holdings=top_holdings,
        concentration=concentration,
        by_ticker_held=by_ticker_held,
        etf_attribution=etf_attribution,
    )
