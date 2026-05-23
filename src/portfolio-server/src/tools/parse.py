"""Portfolio position parsing tool."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..logging_config import get_logger
from ..models import Portfolio, Position, WeightType, detect_asset_type

logger = get_logger(__name__)

# Regex patterns for parsing positions
# A ticker is one to five letters with an optional `.X` share-class suffix
# (e.g. `BRK.B`). This keeps the parser permissive enough for common
# preferred/share-class tickers while still rejecting prose words.
_TICKER = r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?"
# Scale suffix that multiplies the captured amount. Allows `k`/`m`/`b` (case
# insensitive) on both `$50k AAPL` and `AAPL $1.2M`.
_AMOUNT = r"\d+(?:,\d{3})*(?:\.\d+)?(?:[kKmMbB])?"

PATTERNS = {
    # "AAPL 40%" or "AAPL: 40%" or "40% AAPL"
    "percentage": re.compile(
        rf"(?P<symbol>{_TICKER})\s*[:\s]+\s*(?P<weight>\d+(?:\.\d+)?)\s*%"
        r"|"
        rf"(?P<weight2>\d+(?:\.\d+)?)\s*%\s*(?P<symbol2>{_TICKER})",
        re.IGNORECASE,
    ),
    # "AAPL 0.40" or "AAPL: 0.4" (decimal format, must be < 1)
    "decimal": re.compile(
        rf"(?P<symbol>{_TICKER})\s*[:\s]+\s*(?P<weight>0\.\d+)",
        re.IGNORECASE,
    ),
    # "100 shares AAPL" or "AAPL 100 shares"
    "shares": re.compile(
        rf"(?P<shares>\d+(?:,\d{{3}})*)\s*shares?\s+(?P<symbol>{_TICKER})"
        r"|"
        rf"(?P<symbol2>{_TICKER})\s+(?P<shares2>\d+(?:,\d{{3}})*)\s*shares?",
        re.IGNORECASE,
    ),
    # "$50,000 AAPL" or "AAPL $50k" or "$1.2M in BRK.B"
    "dollars": re.compile(
        rf"\$(?P<amount>{_AMOUNT})\s*(?:in\s+)?(?P<symbol>{_TICKER})"
        r"|"
        rf"(?P<symbol2>{_TICKER})\s+\$(?P<amount2>{_AMOUNT})",
        re.IGNORECASE,
    ),
}


_SCALE_SUFFIXES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _parse_amount(amount_str: str) -> Decimal:
    """Parse dollar amount string to Decimal.

    Accepts comma-grouped numbers and the standard scale suffixes
    ``k``/``m``/``b`` (case insensitive). The suffix multiplier is applied
    after stripping group separators so ``$1.2M`` resolves to ``1_200_000``.
    """
    cleaned = amount_str.replace(",", "")
    if not cleaned:
        return Decimal(0)
    suffix = cleaned[-1].lower()
    multiplier = _SCALE_SUFFIXES.get(suffix)
    if multiplier is not None:
        return Decimal(cleaned[:-1]) * multiplier
    return Decimal(cleaned)


def _normalize_weights(positions: list[Position]) -> tuple[list[Position], bool]:
    """Normalize position weights to sum to 1.0.

    Args:
        positions: List of positions with raw weights.

    Returns:
        Tuple of (normalized positions, was_normalized).

    """
    total = sum(p.weight for p in positions)

    if total == 0:
        return positions, False

    # If already close to 1.0, don't normalize
    if Decimal("0.99") <= total <= Decimal("1.01"):
        return positions, False

    # Normalize
    normalized = []
    for p in positions:
        normalized.append(
            Position(
                symbol=p.symbol,
                weight=p.weight / total,
                asset_type=p.asset_type,
                original_input=p.original_input,
                weight_type=p.weight_type,
                shares=p.shares,
                dollar_value=p.dollar_value,
                price_used=p.price_used,
            )
        )

    return normalized, True


def parse_positions(  # noqa: PLR0912, PLR0915
    text: str,
    normalize: bool = True,
) -> dict[str, Any]:
    """Parse free-form text into normalized portfolio positions.

    Supports multiple formats:
    - Percentages: "AAPL 40%, QQQ 35%, BND 25%"
    - Decimals: "AAPL 0.40, QQQ 0.35, BND 0.25"
    - Shares: "100 shares AAPL, 50 shares MSFT" (requires price for weighting)
    - Dollars: "$50,000 AAPL, $30,000 QQQ" (requires price for weighting)

    Args:
        text: User's portfolio description.
        normalize: Whether to normalize weights to sum to 1.0.

    Returns:
        Dictionary with parsed portfolio and any warnings.

    """
    positions: list[Position] = []
    warnings: list[str] = []
    seen_symbols: set[str] = set()

    # Clean input
    text = text.strip()
    if not text:
        return {
            "isError": True,
            "error": "Empty input - please provide portfolio positions",
            "error_type": "validation_error",
        }

    # Try percentage format first
    for match in PATTERNS["percentage"].finditer(text):
        symbol = (match.group("symbol") or match.group("symbol2") or "").upper()
        weight_str = match.group("weight") or match.group("weight2")

        if not symbol or not weight_str:
            continue

        if symbol in seen_symbols:
            warnings.append(f"Duplicate symbol {symbol} - using first occurrence")
            continue

        try:
            # Convert percentage to decimal (40% -> 0.40)
            weight = Decimal(weight_str) / 100
            positions.append(
                Position(
                    symbol=symbol,
                    weight=weight,
                    asset_type=detect_asset_type(symbol),
                    original_input=match.group(0),
                    weight_type=WeightType.PERCENTAGE,
                )
            )
            seen_symbols.add(symbol)
        except InvalidOperation:
            warnings.append(f"Could not parse weight for {symbol}: {weight_str}")

    # Try decimal format
    for match in PATTERNS["decimal"].finditer(text):
        symbol = (match.group("symbol") or "").upper()
        weight_str = match.group("weight")

        if not symbol or not weight_str:
            continue

        if symbol in seen_symbols:
            continue  # Already parsed as percentage

        try:
            weight = Decimal(weight_str)
            positions.append(
                Position(
                    symbol=symbol,
                    weight=weight,
                    asset_type=detect_asset_type(symbol),
                    original_input=match.group(0),
                    weight_type=WeightType.PERCENTAGE,  # Treat decimal as percentage
                )
            )
            seen_symbols.add(symbol)
        except InvalidOperation:
            warnings.append(f"Could not parse weight for {symbol}: {weight_str}")

    # Try shares format
    for match in PATTERNS["shares"].finditer(text):
        symbol = (match.group("symbol") or match.group("symbol2") or "").upper()
        shares_str = match.group("shares") or match.group("shares2")

        if not symbol or not shares_str:
            continue

        if symbol in seen_symbols:
            continue

        try:
            shares = Decimal(shares_str.replace(",", ""))
            # We can't calculate weight without prices - set to 0 for now
            positions.append(
                Position(
                    symbol=symbol,
                    weight=Decimal("0"),  # Needs price lookup
                    asset_type=detect_asset_type(symbol),
                    original_input=match.group(0),
                    weight_type=WeightType.SHARES,
                    shares=shares,
                )
            )
            seen_symbols.add(symbol)
            warnings.append(
                f"{symbol}: shares specified - weight calculation requires current prices"
            )
        except InvalidOperation:
            warnings.append(f"Could not parse shares for {symbol}: {shares_str}")

    # Try dollars format
    for match in PATTERNS["dollars"].finditer(text):
        symbol = (match.group("symbol") or match.group("symbol2") or "").upper()
        amount_str = match.group("amount") or match.group("amount2")

        if not symbol or not amount_str:
            continue

        if symbol in seen_symbols:
            continue

        try:
            dollar_value = _parse_amount(amount_str)
            # We can calculate relative weights from dollar amounts
            positions.append(
                Position(
                    symbol=symbol,
                    weight=Decimal("0"),  # Will calculate after all positions parsed
                    asset_type=detect_asset_type(symbol),
                    original_input=match.group(0),
                    weight_type=WeightType.DOLLARS,
                    dollar_value=dollar_value,
                )
            )
            seen_symbols.add(symbol)
        except InvalidOperation:
            warnings.append(f"Could not parse dollar amount for {symbol}: {amount_str}")

    # Calculate weights for dollar-based positions
    dollar_positions = [p for p in positions if p.weight_type == WeightType.DOLLARS]
    if dollar_positions:
        total_dollars = sum(p.dollar_value or Decimal("0") for p in dollar_positions)
        if total_dollars > 0:
            for p in dollar_positions:
                if p.dollar_value:
                    p.weight = p.dollar_value / total_dollars

    # Validation
    if not positions:
        return {
            "isError": True,
            "error": (
                "Could not parse any positions from input. "
                "Try formats like: 'AAPL 40%, QQQ 35%, BND 25%' or 'AAPL 0.40, QQQ 0.35'"
            ),
            "error_type": "parse_error",
        }

    # Check for shares-only input (can't calculate weights)
    shares_only = all(p.weight_type == WeightType.SHARES for p in positions)
    if shares_only:
        warnings.append(
            "All positions specified in shares - cannot calculate allocation without prices. "
            "Consider using percentages or dollar amounts."
        )

    # Calculate total weight
    total_weight = sum((p.weight for p in positions), Decimal("0"))

    # Warn if weights don't sum to ~1.0
    if total_weight > Decimal("1.1"):
        warnings.append(f"Weights sum to {float(total_weight):.1%} (>100%) - will normalize")
    elif total_weight < Decimal("0.9") and total_weight > 0:
        warnings.append(f"Weights sum to {float(total_weight):.1%} (<90%) - will normalize")

    # Normalize if requested
    was_normalized = False
    if normalize and total_weight > 0:
        positions, was_normalized = _normalize_weights(positions)
        total_weight = sum((p.weight for p in positions), Decimal("0"))

    portfolio = Portfolio(
        positions=positions,
        total_weight=total_weight,
        parsing_warnings=warnings,
        normalized=was_normalized,
    )

    # Build summary
    position_summaries = [f"{p.symbol} ({float(p.weight):.1%})" for p in positions if p.weight > 0]
    asset_types: dict[str, int] = {}
    for p in positions:
        asset_types[p.asset_type.value] = asset_types.get(p.asset_type.value, 0) + 1

    summary = f"Parsed {len(positions)} positions: {', '.join(position_summaries)}"
    if asset_types:
        type_summary = ", ".join(f"{count} {atype}" for atype, count in asset_types.items())
        summary += f" ({type_summary})"

    return {
        "portfolio": portfolio.to_dict(),
        "summary": summary,
        "position_count": len(positions),
        "warnings": warnings,
    }
