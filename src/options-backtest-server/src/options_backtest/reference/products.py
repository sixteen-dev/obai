"""Product rules of the R1 roots and contract terms (ADR 0002 §3, design §12.1).

SPXW and XSP are European, PM cash-settled options on the S&P 500 index. SPXW settles on Cboe's
official SPX close; XSP on one tenth of it, rounded by the stored rule. Both templates are
``template_unverified`` until WP2 sources Cboe. The engine reads multiplier and deliverable only
from ``ContractTerms``; a generator may replace the template's ``premium_multiplier`` and
``deliverable_units`` (``dataclasses.replace``) to build TLA-scale markets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from options_backtest.models.market import ContractTerms, OptionType
from options_backtest.money import Price

PRODUCT_RULES_VERSION: Final = "cboe_template_unverified_v1"
R1_FAMILY: Final = "us_european_pm_cash_index"


@dataclass(frozen=True, slots=True)
class ProductRules:
    """Contract rules of one option root.

    Attributes:
        root: Option root.
        underlying_id: Underlying index and deliverable asset id.
        family: Product family (``us_european_pm_cash_index``).
        settlement_series: Settlement series of every contract of the root.
        settlement_divisor: The root's index value is the SPX value divided by this.
        settlement_places: Decimal places of the settlement value.
        settlement_rounding: ``decimal`` rounding mode of the settlement value.
        premium_multiplier: Premium dollars per contract per price unit.
        deliverable_units: Units of ``underlying_id`` per contract; AEA = units × strike.
        last_trade_local: Last trading time on the expiry date, America/New_York.
        status: Verification status of the rules.

    """

    root: str
    underlying_id: str
    family: str
    settlement_series: str
    settlement_divisor: Decimal
    settlement_places: int
    settlement_rounding: str
    premium_multiplier: Decimal
    deliverable_units: Decimal
    last_trade_local: time
    status: str

    def terms(
        self, strike: Price, right: OptionType, expires_at_ns: int, *, expiry: date
    ) -> ContractTerms:
        """Return the terms of one contract of this root.

        European, cash-settled; multiplier ``premium_multiplier``; deliverable
        ``Deliverable(f"{underlying_id}:{units}", (DeliverableComponent(underlying_id,
        units),), Usd(0))`` with ``units = deliverable_units``; AEA = units × strike (100 × K for
        the templates); id ``contract_id(root, expiry, right, strike)``.

        Args:
            strike: Strike in the root's index points.
            right: Call or put.
            expires_at_ns: Expiration instant: the expiry date's close.
            expiry: Expiry date, used in the contract id.

        Returns:
            The terms.

        """
        raise NotImplementedError


SPXW_RULES: Final = ProductRules(
    root="SPXW",
    underlying_id="SPX",
    family=R1_FAMILY,
    settlement_series="SPX_PM",
    settlement_divisor=Decimal(1),
    settlement_places=2,
    settlement_rounding=ROUND_HALF_UP,
    premium_multiplier=Decimal(100),
    deliverable_units=Decimal(100),
    last_trade_local=time(16, 0),
    status="template_unverified",
)
XSP_RULES: Final = ProductRules(
    root="XSP",
    underlying_id="XSP",
    family=R1_FAMILY,
    settlement_series="XSP_PM",
    settlement_divisor=Decimal(10),
    settlement_places=2,
    settlement_rounding=ROUND_HALF_UP,
    premium_multiplier=Decimal(100),
    deliverable_units=Decimal(100),
    last_trade_local=time(16, 0),
    status="template_unverified",
)


def product_rules(root: str) -> ProductRules:
    """Return the template rules of an R1 root.

    Args:
        root: ``SPXW`` or ``XSP``.

    Returns:
        ``SPXW_RULES`` or ``XSP_RULES``.

    Raises:
        ValueError: For any other root.

    """
    raise NotImplementedError


def strike_text(strike: Price) -> str:
    """Return the strike's normalized text: ``format(strike.value.normalize(), "f")``.

    No exponent and no trailing zeros: 4900.00 is "4900", 451.50 is "451.5".

    Args:
        strike: Strike.

    Returns:
        The text used in contract ids.

    """
    raise NotImplementedError


def contract_id(root: str, expiry: date, right: OptionType, strike: Price) -> str:
    """Return ``{root}:{expiry YYYY-MM-DD}:{C|P}:{strike_text}``, e.g. ``SPXW:2024-04-19:P:4900``.

    Args:
        root: Option root.
        expiry: Expiry date.
        right: Call (``C``) or put (``P``).
        strike: Strike.

    Returns:
        The contract id.

    """
    raise NotImplementedError


def settlement_price(rules: ProductRules, spx_official: Price) -> Price:
    """Return the root's settlement value from Cboe's official SPX close.

    ``(spx_official / settlement_divisor)`` quantized to ``settlement_places`` with
    ``settlement_rounding``: SPXW 4897 gives 4897.00; XSP 4512.37 gives 451.24 (451.237 half up).

    Args:
        rules: The root's rules.
        spx_official: Official SPX close.

    Returns:
        The settlement value.

    """
    raise NotImplementedError
