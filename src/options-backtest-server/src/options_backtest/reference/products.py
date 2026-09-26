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
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    Inexact,
    localcontext,
)
from typing import Final

from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
    require_id,
    require_type,
)
from options_backtest.money import EXACT, ZERO_USD, Price, Usd

PRODUCT_RULES_VERSION: Final = "cboe_template_unverified_v1"
R1_FAMILY: Final = "us_european_pm_cash_index"
_ROUNDING_MODES: Final = frozenset(
    {
        ROUND_05UP,
        ROUND_CEILING,
        ROUND_DOWN,
        ROUND_FLOOR,
        ROUND_HALF_DOWN,
        ROUND_HALF_EVEN,
        ROUND_HALF_UP,
        ROUND_UP,
    }
)
_MAX_SETTLEMENT_PLACES: Final = 9
"""A settlement value is a ``Price``: at most 9 decimal places."""


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

    def __post_init__(self) -> None:
        """Validate ids, positive exact decimals, places, the rounding mode and the time."""
        require_id(self.root, "ProductRules.root")
        require_id(self.underlying_id, "ProductRules.underlying_id")
        require_id(self.family, "ProductRules.family")
        require_id(self.settlement_series, "ProductRules.settlement_series")
        _require_positive_decimal(self.settlement_divisor, "ProductRules.settlement_divisor")
        places = self.settlement_places
        if type(places) is not int:
            raise TypeError(f"ProductRules.settlement_places must be int, got {places!r}")
        if not 0 <= places <= _MAX_SETTLEMENT_PLACES:
            raise ValueError(f"ProductRules.settlement_places must be in [0, 9], got {places}")
        if self.settlement_rounding not in _ROUNDING_MODES:
            raise ValueError(
                "ProductRules.settlement_rounding must be a decimal rounding mode, "
                f"got {self.settlement_rounding!r}"
            )
        _require_positive_decimal(self.premium_multiplier, "ProductRules.premium_multiplier")
        _require_positive_decimal(self.deliverable_units, "ProductRules.deliverable_units")
        require_type(self.last_trade_local, time, "ProductRules.last_trade_local")
        require_id(self.status, "ProductRules.status")

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

        Raises:
            TypeError: If ``expiry`` is not exactly a ``date`` or another argument has the wrong
                type.
            ValueError: If the AEA leaves DECIMAL(28,9).

        """
        if type(expiry) is not date:
            raise TypeError(f"ProductRules.terms expiry must be exactly date, got {expiry!r}")
        require_type(strike, Price, "ProductRules.terms strike")
        units = self.deliverable_units
        with localcontext(EXACT):
            aggregate_exercise_amount = Usd(units * strike.value)
        return ContractTerms(
            contract_id=contract_id(self.root, expiry, right, strike),
            option_type=right,
            strike=strike,
            exercise_style=ExerciseStyle.EUROPEAN,
            settlement_type=SettlementType.CASH,
            premium_multiplier=self.premium_multiplier,
            deliverable=Deliverable(
                f"{self.underlying_id}:{units}",
                (DeliverableComponent(self.underlying_id, units),),
                ZERO_USD,
            ),
            aggregate_exercise_amount=aggregate_exercise_amount,
            expires_at_ns=expires_at_ns,
        )


def _require_positive_decimal(value: object, field: str) -> None:
    if type(value) is not Decimal:
        raise TypeError(f"{field} must be exactly Decimal, got {type(value).__name__}")
    if not (value.is_finite() and value > 0):
        raise ValueError(f"{field} must be finite and > 0, got {value}")


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
    match root:
        case "SPXW":
            return SPXW_RULES
        case "XSP":
            return XSP_RULES
    raise ValueError(f"no R1 product rules for root {root!r}")


def strike_text(strike: Price) -> str:
    """Return the strike's normalized text: ``format(strike.value.normalize(), "f")``.

    No exponent and no trailing zeros: 4900.00 is "4900", 451.50 is "451.5".

    Args:
        strike: Strike.

    Returns:
        The text used in contract ids.

    Raises:
        TypeError: If ``strike`` is not a ``Price``.

    """
    require_type(strike, Price, "strike_text strike")
    return format(strike.value.normalize(EXACT), "f")


def contract_id(root: str, expiry: date, right: OptionType, strike: Price) -> str:
    """Return ``{root}:{expiry YYYY-MM-DD}:{C|P}:{strike_text}``, e.g. ``SPXW:2024-04-19:P:4900``.

    Args:
        root: Option root.
        expiry: Expiry date.
        right: Call (``C``) or put (``P``).
        strike: Strike.

    Returns:
        The contract id.

    Raises:
        TypeError: If an argument has the wrong type (``expiry`` must be exactly a ``date``).
        ValueError: If ``root`` is empty.

    """
    require_id(root, "contract_id root")
    if type(expiry) is not date:
        raise TypeError(f"contract_id expiry must be exactly date, got {expiry!r}")
    require_type(right, OptionType, "contract_id right")
    letter = "C" if right is OptionType.CALL else "P"
    return f"{root}:{expiry.isoformat()}:{letter}:{strike_text(strike)}"


def settlement_price(rules: ProductRules, spx_official: Price) -> Price:
    """Return the root's settlement value from Cboe's official SPX close.

    ``(spx_official / settlement_divisor)`` quantized to ``settlement_places`` with
    ``settlement_rounding``: SPXW 4897 gives 4897.00; XSP 4512.37 gives 451.24 (451.237 half up).

    Args:
        rules: The root's rules.
        spx_official: Official SPX close.

    Returns:
        The settlement value.

    Raises:
        TypeError: If ``rules`` or ``spx_official`` has the wrong type.
        decimal.Inexact: If ``spx_official / settlement_divisor`` does not terminate (never for
            the divisors 1 and 10): the quotient is rounded once, by the product's rule.

    """
    require_type(rules, ProductRules, "settlement_price rules")
    require_type(spx_official, Price, "settlement_price spx_official")
    quantum = Decimal(1).scaleb(-rules.settlement_places, EXACT)
    with localcontext(EXACT) as context:
        quotient = spx_official.value / rules.settlement_divisor
        context.traps[Inexact] = False  # the product's declared rounding is the one inexact step
        value = quotient.quantize(quantum, rounding=rules.settlement_rounding)
    return Price(value)
