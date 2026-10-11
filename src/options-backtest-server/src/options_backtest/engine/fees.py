"""Assumed flat fee schedule (ADR 0001 §5, §9; design §11.5).

``cost_basis = assumed_schedule``: an explicitly named research assumption applied across all
history, not a historical brokerage schedule. Each assessed leg or lifecycle event yields one
``FeeLine``, kept even at a $0 rate so the entry records what was assessed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, assert_never

from options_backtest.models.ledger import FeeEvent, FeeLine, LegFill
from options_backtest.models.market import require_id, require_non_negative_usd
from options_backtest.money import Usd

_LIFECYCLE_EVENTS: Final = frozenset({FeeEvent.EXERCISE_ASSIGNMENT, FeeEvent.CASH_SETTLEMENT})


def _require_rate(rate: Usd, field: str) -> None:
    require_non_negative_usd(rate, field)  # also rejects a non-Usd value at runtime
    if not rate.is_cents():
        raise ValueError(f"{field} must be whole cents, got {rate.amount}")


@dataclass(frozen=True, slots=True)
class AssumedFlatFeeSchedule:
    """Flat per-contract fees for trades, exercise/assignment and cash settlement.

    Attributes:
        schedule_id: Identifier; prefixes every fee component id.
        trade_per_contract: Fee per option contract per traded side; whole cents >= 0.
        exercise_assignment_per_contract: Fee per exercised or assigned contract; whole cents
            >= 0.
        cash_settlement_per_contract: Fee per cash-settled contract; whole cents >= 0.
        cost_basis: Always ``"assumed_schedule"`` (design §11.5).

    """

    schedule_id: str
    trade_per_contract: Usd
    exercise_assignment_per_contract: Usd
    cash_settlement_per_contract: Usd

    cost_basis: ClassVar[str] = "assumed_schedule"

    def __post_init__(self) -> None:
        """Validate the id and that every rate is whole non-negative cents."""
        require_id(self.schedule_id, "schedule_id")
        _require_rate(self.trade_per_contract, "trade_per_contract")
        _require_rate(self.exercise_assignment_per_contract, "exercise_assignment_per_contract")
        _require_rate(self.cash_settlement_per_contract, "cash_settlement_per_contract")


def _line(schedule: AssumedFlatFeeSchedule, event: FeeEvent, contracts: int) -> FeeLine:
    """Return the fee line for ``contracts`` contracts of ``event`` at the schedule's rate."""
    if event is FeeEvent.TRADE:
        rate = schedule.trade_per_contract
    elif event is FeeEvent.EXERCISE_ASSIGNMENT:
        rate = schedule.exercise_assignment_per_contract
    elif event is FeeEvent.CASH_SETTLEMENT:
        rate = schedule.cash_settlement_per_contract
    else:  # pragma: no cover - mypy proves every FeeEvent is handled above
        assert_never(event)
    component_id = f"{schedule.schedule_id}:{event.value}"
    return FeeLine(component_id, event, contracts, rate, rate.scaled_by(contracts))


def trade_fees(schedule: AssumedFlatFeeSchedule, legs: Sequence[LegFill]) -> tuple[FeeLine, ...]:
    """Return one trade fee line per filled leg, on its absolute contract count.

    Args:
        schedule: Fee schedule.
        legs: Filled legs of one package; at least one.

    Returns:
        The fee lines, in leg order.

    Raises:
        ValueError: If ``legs`` is empty.

    """
    if not legs:
        raise ValueError("trade_fees needs at least one leg")
    return tuple(_line(schedule, FeeEvent.TRADE, abs(leg.contracts)) for leg in legs)


def lifecycle_fees(
    schedule: AssumedFlatFeeSchedule, event: FeeEvent, contracts: int
) -> tuple[FeeLine, ...]:
    """Return the fee line of a lifecycle event over ``contracts`` contracts.

    Args:
        schedule: Fee schedule.
        event: ``EXERCISE_ASSIGNMENT`` or ``CASH_SETTLEMENT``.
        contracts: Contracts affected (a package's Σ|quantity|), > 0.

    Returns:
        One fee line.

    Raises:
        TypeError: If ``event`` is not a ``FeeEvent`` or ``contracts`` is not exactly ``int``.
        ValueError: If ``event`` is a trade or ``contracts`` is not positive.

    """
    if not isinstance(event, FeeEvent):
        raise TypeError(f"lifecycle_fees event must be a FeeEvent, got {event!r}")
    if event not in _LIFECYCLE_EVENTS:
        raise ValueError(f"{event} fees come from trade_fees, not lifecycle_fees")
    if type(contracts) is not int:
        raise TypeError(f"lifecycle_fees contracts must be int, got {type(contracts).__name__}")
    if contracts <= 0:
        raise ValueError(f"lifecycle_fees contracts must be > 0, got {contracts}")
    return (_line(schedule, event, contracts),)
