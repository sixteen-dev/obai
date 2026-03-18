"""Position and Portfolio models."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class AssetType(str, Enum):
    """Type of asset in the portfolio."""

    STOCK = "stock"
    ETF = "etf"
    BOND_ETF = "bond_etf"
    CASH = "cash"
    UNKNOWN = "unknown"


class WeightType(str, Enum):
    """How the user specified the position weight."""

    PERCENTAGE = "percentage"  # "40%" or "0.40"
    SHARES = "shares"  # "100 shares" - requires price lookup
    DOLLARS = "dollars"  # "$50,000" - requires price lookup


# Known ETF sets for asset type detection
KNOWN_ETFS = {
    # US Total Market
    "VTI",
    "ITOT",
    "SPTM",
    # S&P 500
    "SPY",
    "VOO",
    "IVV",
    "SPLG",
    # Nasdaq
    "QQQ",
    "QQQM",
    # Small Cap
    "IWM",
    "VB",
    "IJR",
    # International
    "VEA",
    "IEFA",
    "VWO",
    "IEMG",
    "VXUS",
    # Sector
    "XLK",
    "XLF",
    "XLV",
    "XLE",
    "XLY",
    "XLP",
    "XLI",
    "XLB",
    "XLU",
    "XLRE",
    # Thematic
    "ARKK",
    "ARKW",
    "ARKG",
    "ARKF",
}

BOND_ETFS = {
    # Total Bond
    "BND",
    "AGG",
    "SCHZ",
    # Treasury
    "TLT",
    "IEF",
    "SHY",
    "GOVT",
    "VGSH",
    "VGIT",
    "VGLT",
    # Corporate
    "LQD",
    "VCIT",
    "VCSH",
    # High Yield
    "HYG",
    "JNK",
    # TIPS
    "TIP",
    "SCHP",
    # International
    "BNDX",
    "IAGG",
    # Muni
    "MUB",
    "VTEB",
}

CASH_SYMBOLS = {
    "CASH",
    "USD",
    "MONEY MARKET",
    "MM",
    # Fidelity
    "SPAXX",
    "FDRXX",
    "FZFXX",
    # Vanguard
    "VMFXX",
    "VMMXX",
    # Schwab
    "SWVXX",
}


def detect_asset_type(symbol: str) -> AssetType:
    """Detect asset type from symbol.

    Args:
        symbol: Ticker symbol.

    Returns:
        Detected asset type.

    """
    symbol_upper = symbol.upper().strip()

    if symbol_upper in CASH_SYMBOLS:
        return AssetType.CASH
    if symbol_upper in BOND_ETFS:
        return AssetType.BOND_ETF
    if symbol_upper in KNOWN_ETFS:
        return AssetType.ETF
    # Default: assume stock (can be verified via API if needed)
    return AssetType.STOCK


@dataclass
class Position:
    """A single position in the portfolio."""

    symbol: str
    weight: Decimal  # Normalized to 0-1 range
    asset_type: AssetType
    original_input: str  # Raw user input for this position
    weight_type: WeightType  # How user specified the weight
    shares: Decimal | None = None  # If specified in shares
    dollar_value: Decimal | None = None  # If specified in dollars
    price_used: Decimal | None = None  # Price used for normalization (if any)

    def to_dict(self) -> dict[str, str | float | None]:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "weight": float(self.weight),
            "asset_type": self.asset_type.value,
            "original_input": self.original_input,
            "weight_type": self.weight_type.value,
            "shares": float(self.shares) if self.shares else None,
            "dollar_value": float(self.dollar_value) if self.dollar_value else None,
            "price_used": float(self.price_used) if self.price_used else None,
        }


@dataclass
class Portfolio:
    """A complete portfolio with positions."""

    positions: list[Position]
    total_weight: Decimal  # Should be ~1.0
    parsing_warnings: list[str] = field(default_factory=list)
    normalized: bool = False  # Whether weights were normalized

    def to_dict(self) -> dict[str, list[dict[str, str | float | None]] | float | list[str] | bool]:
        """Convert to dictionary for JSON serialization."""
        return {
            "positions": [p.to_dict() for p in self.positions],
            "total_weight": float(self.total_weight),
            "parsing_warnings": self.parsing_warnings,
            "normalized": self.normalized,
        }
