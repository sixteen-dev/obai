"""Contract terms, deliverables and quotes (ADR 0001 §3, design §8.2).

The premium multiplier appears only in premiums and marks; payoff and settlement use the
deliverable and the aggregate exercise amount (AEA). The three are independent: F05 has 50
deliverable units, AEA 6000 and multiplier 100.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum

from options_backtest.errors import MissingMarkError
from options_backtest.money import EXACT, ZERO_USD, Price, Usd


class OptionType(StrEnum):
    """Option right."""

    CALL = "call"
    PUT = "put"

    @property
    def payoff_sign(self) -> int:
        """Return the design's ``e``: +1 for a call, -1 for a put."""
        return 1 if self is OptionType.CALL else -1


class ExerciseStyle(StrEnum):
    """When the holder may exercise."""

    EUROPEAN = "european"
    AMERICAN = "american"


class SettlementType(StrEnum):
    """How exercise settles: cash difference or delivery of the deliverable."""

    CASH = "cash"
    PHYSICAL = "physical"


def require_type(value: object, expected: type[object], field: str) -> None:
    """Reject a value that is not an instance of ``expected``; a record field guard.

    Args:
        value: Field value.
        expected: Required type; subclasses pass.
        field: Field name used in the message.

    Raises:
        TypeError: If ``value`` is not an ``expected`` instance.

    """
    if not isinstance(value, expected):
        raise TypeError(f"{field} must be {expected.__name__}, got {type(value).__name__}")


def require_id(value: object, field: str) -> None:
    """Reject an identifier that is not a non-empty ``str``.

    Args:
        value: Field value.
        field: Field name used in the message.

    Raises:
        TypeError: If ``value`` is not a ``str``.
        ValueError: If ``value`` is empty.

    """
    require_type(value, str, field)
    if not value:
        raise ValueError(f"{field} must be non-empty")


def require_int(value: object, field: str) -> None:
    """Reject a value that is not exactly ``int`` (``bool`` included).

    Args:
        value: Field value.
        field: Field name used in the message.

    Raises:
        TypeError: If ``type(value)`` is not ``int``.

    """
    if type(value) is not int:
        raise TypeError(f"{field} must be int, got {type(value).__name__}")


def require_non_negative_usd(value: object, field: str) -> None:
    """Reject a value that is not a ``Usd`` amount >= 0.

    Args:
        value: Field value.
        field: Field name used in the message.

    Raises:
        TypeError: If ``value`` is not a ``Usd``.
        ValueError: If the amount is negative.

    """
    if not isinstance(value, Usd):
        raise TypeError(f"{field} must be Usd, got {type(value).__name__}")
    if value < ZERO_USD:
        raise ValueError(f"{field} must be >= 0, got {value.amount}")


def _require_positive_decimal(value: object, field: str) -> None:
    if type(value) is not Decimal:
        raise TypeError(f"{field} must be exactly Decimal, got {type(value).__name__}")
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and > 0, got {value}")


@dataclass(frozen=True, slots=True)
class DeliverableComponent:
    """Units of one asset delivered per contract.

    Attributes:
        asset_id: Delivered asset.
        units: Units per contract; exactly ``Decimal``, finite and > 0. Not the multiplier.

    """

    asset_id: str
    units: Decimal

    def __post_init__(self) -> None:
        """Validate the asset id and units."""
        require_id(self.asset_id, "DeliverableComponent.asset_id")
        _require_positive_decimal(self.units, "DeliverableComponent.units")


@dataclass(frozen=True, slots=True)
class Deliverable:
    """What one contract delivers on exercise: asset components plus a cash amount.

    Attributes:
        deliverable_id: Identifier shared by contracts with this deliverable.
        components: One or more components with distinct asset ids.
        cash: Cash delivered per contract, >= 0.

    """

    deliverable_id: str
    components: tuple[DeliverableComponent, ...]
    cash: Usd

    def __post_init__(self) -> None:
        """Validate the id, the components and the cash amount."""
        require_id(self.deliverable_id, "Deliverable.deliverable_id")
        require_type(self.components, tuple, "Deliverable.components")
        if not self.components:
            raise ValueError("Deliverable needs at least one component")
        for component in self.components:
            require_type(component, DeliverableComponent, "Deliverable.components item")
        asset_ids = [component.asset_id for component in self.components]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError(f"Deliverable has duplicate asset ids: {asset_ids}")
        require_non_negative_usd(self.cash, "Deliverable.cash")

    def value_usd(self, prices: Mapping[str, Price]) -> Usd:
        """Return the exact value of one deliverable: ``Σ units × price + cash``.

        Args:
            prices: Price per asset id; entries for other assets are ignored.

        Returns:
            The deliverable's value in USD.

        Raises:
            MissingMarkError: If any component's asset has no price.
            ValueError: If the value needs more than 9 decimal places or leaves DECIMAL(28,9).
            decimal.Inexact: If an exact product needs more than 50 significant digits.

        """
        missing = [c.asset_id for c in self.components if c.asset_id not in prices]
        if missing:
            raise MissingMarkError(missing)
        with localcontext(EXACT):
            total = sum(
                (c.units * prices[c.asset_id].value for c in self.components),
                start=self.cash.amount,
            )
        return Usd(total)


@dataclass(frozen=True, slots=True)
class ContractTerms:
    """Economic terms of one option contract version.

    No relation between strike, deliverable units and AEA is assumed or enforced.

    Attributes:
        contract_id: Stable contract identifier.
        option_type: Call or put.
        strike: Listed strike, informational for payoff (payoff uses deliverable and AEA).
        exercise_style: European or American.
        settlement_type: Cash or physical.
        premium_multiplier: Premium dollars per contract per price unit; ``Decimal`` > 0.
        deliverable: What one contract delivers.
        aggregate_exercise_amount: Cash exchanged per contract on exercise, >= 0.
        expires_at_ns: Expiration, UTC integer nanoseconds.

    """

    contract_id: str
    option_type: OptionType
    strike: Price
    exercise_style: ExerciseStyle
    settlement_type: SettlementType
    premium_multiplier: Decimal
    deliverable: Deliverable
    aggregate_exercise_amount: Usd
    expires_at_ns: int

    def __post_init__(self) -> None:
        """Validate every field's type and range."""
        require_id(self.contract_id, "ContractTerms.contract_id")
        require_type(self.option_type, OptionType, "ContractTerms.option_type")
        require_type(self.strike, Price, "ContractTerms.strike")
        require_type(self.exercise_style, ExerciseStyle, "ContractTerms.exercise_style")
        require_type(self.settlement_type, SettlementType, "ContractTerms.settlement_type")
        _require_positive_decimal(self.premium_multiplier, "ContractTerms.premium_multiplier")
        require_type(self.deliverable, Deliverable, "ContractTerms.deliverable")
        require_non_negative_usd(
            self.aggregate_exercise_amount, "ContractTerms.aggregate_exercise_amount"
        )
        require_int(self.expires_at_ns, "ContractTerms.expires_at_ns")

    def premium_usd(self, price: Price, contracts: int) -> Usd:
        """Return the premium for a signed contract count: ``contracts × multiplier × price``.

        Args:
            price: Premium price per unit.
            contracts: Signed count (+ buy, − sell).

        Returns:
            The exact signed premium.

        Raises:
            TypeError: If ``price`` is not a ``Price`` or ``contracts`` is not exactly ``int``.
            ValueError: If the premium needs more than 9 decimal places or leaves DECIMAL(28,9).
            decimal.Inexact: If the exact product needs more than 50 significant digits.

        """
        require_type(price, Price, "premium_usd price")
        require_int(contracts, "premium_usd contracts")
        with localcontext(EXACT):
            return Usd(contracts * self.premium_multiplier * price.value)

    def intrinsic_usd(self, prices: Mapping[str, Price]) -> Usd:
        """Return one contract's exercise value: ``max(e·(deliverable value − AEA), 0)``.

        Args:
            prices: Price per deliverable asset id.

        Returns:
            The non-negative intrinsic value of one long contract.

        Raises:
            MissingMarkError: If a deliverable asset has no price.

        """
        exercise_value = self.deliverable.value_usd(prices) - self.aggregate_exercise_amount
        return max(exercise_value.scaled_by(self.option_type.payoff_sign), ZERO_USD)


@dataclass(frozen=True, slots=True)
class Quote:
    """A valid two-sided quote: ``0 <= bid <= ask`` and ``ask > 0`` (design §8.4).

    Attributes:
        bid: Bid price; zero means no displayed bid.
        ask: Ask price; zero is invalid.

    """

    bid: Price
    ask: Price

    def __post_init__(self) -> None:
        """Reject non-price sides, a zero ask and a crossed market."""
        require_type(self.bid, Price, "Quote.bid")
        require_type(self.ask, Price, "Quote.ask")
        if self.ask.value <= 0:
            raise ValueError(f"Quote.ask must be > 0, got {self.ask.value}")
        if self.bid > self.ask:
            raise ValueError(f"Quote is crossed: bid {self.bid.value} > ask {self.ask.value}")
