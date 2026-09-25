"""Strict, frozen mirror of ``strategy.schema.json`` (ADR 0001 §4 step 5, design §9.1).

One model per schema object and one field per schema property. Every model is
``strict``, ``extra="forbid"`` and ``frozen``. The strict-mode traps are closed explicitly:

- Integer constants (``const: 1``) are ``Annotated[int, Field(ge=k, le=k)]``: strict
  ``Literal[1]`` accepts ``True``, ``1.0`` and ``Decimal("1.0")``.
- Schema ``number`` fields are exact ``Decimal``; a JSON integer is lifted to ``Decimal``
  first, and anything but ``int`` or ``Decimal`` (``bool``, ``str``, ``float``) is rejected.
- Decimal strings must fullmatch the schema pattern before becoming ``Usd``, ``Price`` or a
  signed ``Decimal``.
- ``oneOf`` groups are tagged unions keyed on ``method``, ``frequency`` or ``mode``; tags are
  ``"oneOf:<value>"`` so pointer mapping can drop them, and a missing or unknown tag raises
  ``ONE_OF_TAG_ERROR`` naming the discriminating member.

``StrategySpec`` is also the engine's read-only strategy value (ADR 0001 §4, last note). Its
``model_dump`` writes decimal-string fields as their schema strings; the dump is a view, neither
the canonical resolved spec (WP5) nor a document ``load_strategy`` accepts back.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    Tag,
    field_validator,
)

from options_backtest.money import Price, Usd

ONE_OF_TAG_PREFIX: Final = "oneOf:"
ONE_OF_TAG_ERROR: Final = "one_of_tag"

_UNSIGNED_DECIMAL: Final = re.compile(r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,9})?$")
_SIGNED_DECIMAL: Final = re.compile(r"^-?(0|[1-9][0-9]{0,14})(\.[0-9]{1,9})?$")


def _as_decimal(value: object) -> object:
    """Lift a JSON integer to ``Decimal``; leave every other input to strict validation."""
    return Decimal(value) if type(value) is int else value


def _decimal_string(value: object, pattern: re.Pattern[str]) -> Decimal:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"must be a decimal string matching {pattern.pattern}")
    number = Decimal(value)
    return number.copy_abs() if number.is_zero() else number


def _unsigned_usd(value: object) -> Usd:
    return Usd(_decimal_string(value, _UNSIGNED_DECIMAL))


def _unsigned_price(value: object) -> Price:
    return Price(_decimal_string(value, _UNSIGNED_DECIMAL))


def _signed_decimal(value: object) -> Decimal:
    return _decimal_string(value, _SIGNED_DECIMAL)


def _usd_text(value: Usd) -> str:
    return str(value.amount)


def _price_text(value: Price) -> str:
    return str(value.value)


def _decimal_text(value: Decimal) -> str:
    return str(value)


def _item_count(low: int, high: int) -> BeforeValidator:
    """Bound an array's length on its input items, as JSON Schema's minItems/maxItems do.

    Pydantic's own tuple length check counts only the items that validated, so invalid items
    would report a long enough array as too short and hide one that is too long.
    """

    def check(value: object) -> object:
        if isinstance(value, tuple) and not low <= len(value) <= high:
            raise ValueError(f"must have {low} to {high} items, got {len(value)}")
        return value

    return BeforeValidator(check)


def _one_of(key: str, *tags: str) -> Discriminator:
    """Discriminate a ``oneOf`` group on member ``key``; the error names ``key``."""

    def tag(value: object) -> str | None:  # a JSON tree to validate, a model to serialize
        found = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
        return f"{ONE_OF_TAG_PREFIX}{found}" if isinstance(found, str) else None

    expected = ", ".join(repr(value) for value in tags)
    return Discriminator(
        tag,
        custom_error_type=ONE_OF_TAG_ERROR,
        custom_error_message=f"{key} must be one of {expected}",
        custom_error_context={"key": key},
    )


Number = Annotated[Decimal, BeforeValidator(_as_decimal)]
UnsignedUsd = Annotated[
    Usd, PlainValidator(_unsigned_usd), PlainSerializer(_usd_text, return_type=str)
]
UnsignedPrice = Annotated[
    Price, PlainValidator(_unsigned_price), PlainSerializer(_price_text, return_type=str)
]
SignedDecimal = Annotated[
    Decimal, PlainValidator(_signed_decimal), PlainSerializer(_decimal_text, return_type=str)
]
PolicyId = Annotated[
    str, StringConstraints(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_:-]*$")
]
LegId = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")]
Symbol = Annotated[
    str, StringConstraints(min_length=1, max_length=20, pattern=r"^[A-Z][A-Z0-9.]*$")
]
Dte = Annotated[int, Field(ge=7, le=365)]
Basis = Literal["initial_credit", "initial_debit"]
Structure = Literal["single_long", "vertical", "iron_condor", "long_straddle", "long_strangle"]


class _SpecModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy unchanged; refuse ``update``, which pydantic applies without validation.

        ``copy.replace`` goes through here too, so a spec value changes only by validating a
        new document.

        Args:
            update: Must be empty or None.
            deep: Whether to copy nested values too.

        Returns:
            An equal copy.

        Raises:
            TypeError: If ``update`` names any change.

        """
        if update:
            raise TypeError(
                f"{type(self).__name__} changes need validation: edit the document and "
                "load_strategy it"
            )
        return super().model_copy(deep=deep)


class Product(_SpecModel):
    """Product scope; roots are checked against the R1 set in ``strategy_checks``."""

    underlying_symbol: Symbol
    allowed_option_roots: Annotated[tuple[Symbol, ...], _item_count(1, 4)]
    family: Literal["us_european_pm_cash_index"]

    @field_validator("allowed_option_roots")
    @classmethod
    def _roots_unique(cls, roots: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(roots)) != len(roots):
            raise ValueError("allowed_option_roots items must be unique")
        return roots


class TargetDteExpiry(_SpecModel):
    """Expiry nearest ``target_dte`` calendar days within ``[min_dte, max_dte]``."""

    method: Literal["target_dte"]
    target_dte: Dte
    min_dte: Dte
    max_dte: Dte


class SameAsExpiry(_SpecModel):
    """The exact expiry resolved for ``anchor_leg_id``."""

    method: Literal["same_as"]
    anchor_leg_id: LegId


class DeltaStrike(_SpecModel):
    """Strike by signed normalized long-option spot delta: calls > 0, puts < 0."""

    method: Literal["delta"]
    target_delta: Annotated[Number, Field(gt=-1, lt=1)]
    tolerance: Annotated[Number, Field(gt=0, le=Decimal("0.25"))]

    @field_validator("target_delta")
    @classmethod
    def _delta_nonzero(cls, target_delta: Decimal) -> Decimal:
        if target_delta == 0:
            raise ValueError("target_delta must not be 0")
        return target_delta


class StrikeOffset(_SpecModel):
    """Anchor leg's strike plus an exact offset in the underlying's quote units."""

    method: Literal["strike_offset"]
    anchor_leg_id: LegId
    offset_price_units: SignedDecimal


class MoneynessStrike(_SpecModel):
    """Strike by strike-to-spot ratio."""

    method: Literal["moneyness"]
    target_strike_to_spot: Annotated[Number, Field(gt=0, le=10)]
    tolerance: Annotated[Number, Field(ge=0, le=Decimal("0.25"))]


ExpirySelection = Annotated[
    Annotated[TargetDteExpiry, Tag("oneOf:target_dte")]
    | Annotated[SameAsExpiry, Tag("oneOf:same_as")],
    _one_of("method", "target_dte", "same_as"),
]
StrikeSelection = Annotated[
    Annotated[DeltaStrike, Tag("oneOf:delta")]
    | Annotated[StrikeOffset, Tag("oneOf:strike_offset")]
    | Annotated[MoneynessStrike, Tag("oneOf:moneyness")],
    _one_of("method", "delta", "strike_offset", "moneyness"),
]


class Leg(_SpecModel):
    """One option leg of the package."""

    leg_id: LegId
    side: Literal["buy", "sell"]
    option_type: Literal["call", "put"]
    ratio: Annotated[int, Field(ge=1, le=1)]
    expiry_selection: ExpirySelection
    strike_selection: StrikeSelection


class DailySchedule(_SpecModel):
    """Every eligible session."""

    frequency: Literal["daily"]


class WeeklySchedule(_SpecModel):
    """One ISO weekday per week, moved to the next session of the same week on a holiday."""

    frequency: Literal["weekly"]
    weekday: Annotated[int, Field(ge=1, le=5)]
    holiday_policy: Literal["next_session_same_week"]


class MonthlySchedule(_SpecModel):
    """The Nth eligible common session of each calendar month."""

    frequency: Literal["monthly"]
    session_ordinal: Annotated[int, Field(ge=1, le=20)]


Schedule = Annotated[
    Annotated[DailySchedule, Tag("oneOf:daily")]
    | Annotated[WeeklySchedule, Tag("oneOf:weekly")]
    | Annotated[MonthlySchedule, Tag("oneOf:monthly")],
    _one_of("frequency", "daily", "weekly", "monthly"),
]


class Condition(_SpecModel):
    """A comparison on a prior-completed-session feature."""

    feature: Literal[
        "underlying.return_20s",
        "underlying.close_to_sma_50s",
        "options.atm30_iv_rank_252s",
        "options.atm30_iv_percentile_252s",
    ]
    operator: Literal["gt", "gte", "lt", "lte"]
    value: Number


class Entry(_SpecModel):
    """Entry schedule and conditions; an empty condition list is true."""

    schedule: Schedule
    all_conditions: Annotated[tuple[Condition, ...], _item_count(0, 8)]


class ProfitRule(_SpecModel):
    """Take profit at ``fraction`` of the initial premium before fees."""

    basis: Basis
    fraction: Annotated[Number, Field(gt=0, le=10)]


class LossRule(_SpecModel):
    """Stop at a net loss of ``multiple`` times the initial premium before fees."""

    basis: Basis
    multiple: Annotated[Number, Field(gt=0, le=100)]


class Exits(_SpecModel):
    """Exit rules; a null rule is disabled."""

    take_profit: ProfitRule | None
    stop_loss: LossRule | None
    exit_dte: Annotated[int, Field(ge=0, le=365)]
    max_holding_sessions: Annotated[int, Field(ge=1, le=252)]


class DisabledRoll(_SpecModel):
    """No rolling."""

    mode: Literal["disabled"]


class SequentialRoll(_SpecModel):
    """Close, then reopen with the original selectors at a later decision."""

    mode: Literal["sequential"]
    trigger_dte: Annotated[int, Field(ge=1, le=365)]
    max_rolls: Annotated[int, Field(ge=1, le=12)]
    max_campaign_sessions: Annotated[int, Field(ge=1, le=756)]


Roll = Annotated[
    Annotated[DisabledRoll, Tag("oneOf:disabled")]
    | Annotated[SequentialRoll, Tag("oneOf:sequential")],
    _one_of("mode", "disabled", "sequential"),
]


class Account(_SpecModel):
    """Account cash, policy and risk caps."""

    currency: Literal["USD"]
    initial_cash_usd: UnsignedUsd
    policy: Literal["fully_funded_v1"]
    max_campaign_risk_fraction: Annotated[Number, Field(gt=0, le=1)]
    max_total_risk_fraction: Annotated[Number, Field(gt=0, le=1)]
    max_concurrent_campaigns: Annotated[int, Field(ge=1, le=1)]


class FixedContracts(_SpecModel):
    """A fixed package count per entry."""

    method: Literal["fixed_contracts"]
    contracts: Annotated[int, Field(ge=1, le=10)]


class RiskBudget(_SpecModel):
    """Package count from the risk caps, at most ``max_contracts``."""

    method: Literal["risk_budget"]
    max_contracts: Annotated[int, Field(ge=1, le=10)]


Sizing = Annotated[
    Annotated[FixedContracts, Tag("oneOf:fixed_contracts")]
    | Annotated[RiskBudget, Tag("oneOf:risk_budget")],
    _one_of("method", "fixed_contracts", "risk_budget"),
]


class Liquidity(_SpecModel):
    """Quote filters; a zero minimum disables that activity filter."""

    min_open_interest: Annotated[int, Field(ge=0)]
    min_cumulative_volume: Annotated[int, Field(ge=0)]
    max_absolute_spread_price_units: UnsignedPrice
    max_relative_spread: Annotated[Number, Field(gt=0, le=5)]
    require_positive_bid_for_entry: bool


class Execution(_SpecModel):
    """Order policy for the natural package limit model."""

    model: Literal["natural_package_limit_v1"]
    participation_fraction: Annotated[Number, Field(gt=0, le=1)]
    max_contracts_per_order: Annotated[int, Field(ge=1, le=10)]
    price_allowance_usd: UnsignedUsd
    fill_attempts: Annotated[int, Field(ge=3, le=3)]
    quantity_policy: Literal["all_or_none_complete_packages"]


class StrategySpec(_SpecModel):
    """A syntactically valid R1 strategy; semantic checks live in ``strategy_checks``."""

    schema_version: Annotated[int, Field(ge=1, le=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    product: Product
    structure: Structure
    legs: Annotated[tuple[Leg, ...], _item_count(1, 4)]
    clock_profile: Literal["scheduled_daily_v1"]
    entry: Entry
    exits: Exits
    roll: Roll
    account: Account
    sizing: Sizing
    liquidity: Liquidity
    execution: Execution
    fee_schedule_id: PolicyId
    funding_policy_id: PolicyId
    comparison_policy_id: PolicyId
    end_policy: Literal["liquidate_at_final_session", "mark_open_positions"]
