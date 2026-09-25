"""Physical exercise and assignment at the ledger level (ADR 0001 §5; design §12.2, §12.3).

With position sign s, payoff sign e (+1 call, -1 put) and n contracts, shares change
``s·e·units·n`` and cash ``-s·e·AEA·n``: the four rows of the §12.2 table. Delivered shares relieve
held stock lots FIFO (short stock is unsupported); acquired shares open one lot at
``AEA / units``. The option's own cost is realized separately. *When* exercise or assignment
happens is WP7. WP1 books single-component, zero-cash, integral-share deliverables only.
"""

from datetime import date
from decimal import Inexact, localcontext

from options_backtest.engine.positions import fifo_relief, held_contract, open_remainder
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import (
    AccountKind,
    EntryKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
    Lot,
    LotRelief,
    Posting,
    QuantityEvent,
    QuantityKind,
    due_cash,
    fee_amounts,
    leg_amounts,
    merge_postings,
)
from options_backtest.models.market import ContractTerms, SettlementType
from options_backtest.money import EXACT, ZERO_USD, Usd


def book_physical_exercise(  # noqa: PLR0913 — signature fixed by ADR 0001 §5
    state: LedgerState,
    *,
    event_id: str,
    at_ns: int,
    contract_id: str,
    contracts: int,
    fees: tuple[FeeLine, ...],
    settles_on: date,
) -> LedgerEntry:
    """Book the exercise (held long) or assignment (held short) of ``contracts`` contracts.

    Args:
        state: Current state.
        event_id: Event identifier; also names an acquired stock lot.
        at_ns: Event time, UTC nanoseconds.
        contract_id: Physically settled contract exercised or assigned.
        contracts: Contracts exercised or assigned, > 0 and at most those held.
        fees: Exercise/assignment fee lines.
        settles_on: Settlement date of the exercise cash and fees.

    Returns:
        The exercise entry; it relieves the option lots FIFO and moves the stock.

    Raises:
        TypeError: If ``contracts`` is not exactly ``int``.
        ValueError: If ``contracts`` is not positive.
        LedgerInvariantError: If the contract is unregistered, retired, not physically settled
            or held in fewer than ``contracts``.
        UnsupportedLifecycle: If delivery needs short stock (UNSUPPORTED_ACCOUNT_STATE), the
            exercised lots span campaigns (UNSUPPORTED_ACCOUNT_STATE) or the deliverable is not
            one integral stock component with an exact unit cost (UNSUPPORTED_CORPORATE_ACTION).

    """
    terms, lots = _exercisable(state, contract_id, contracts)
    asset_id, units = _stock_component(terms)
    position_sign = 1 if lots[0].quantity > 0 else -1
    option_event, campaign_id = _exercised_option(contract_id, lots, -position_sign * contracts)
    direction = position_sign * terms.option_type.payoff_sign
    shares = direction * units * contracts
    stock_reliefs = fifo_relief(state.lots.get(asset_id, ()), shares)
    acquired = _acquired_shares(asset_id, shares, stock_reliefs)
    opened = None
    if acquired:
        unit_cost = _unit_cost(terms, units)
        opened = Lot(f"{event_id}:{asset_id}", asset_id, acquired, unit_cost, campaign_id, at_ns)
    stock_event = QuantityEvent(asset_id, QuantityKind.DELIVERY, shares, opened, stock_reliefs)
    cash = terms.aggregate_exercise_amount.scaled_by(-direction * contracts)
    return LedgerEntry(
        event_id=event_id,
        sequence=state.entry_count + 1,
        kind=EntryKind.PHYSICAL_EXERCISE,
        at_ns=at_ns,
        campaign_id=campaign_id,
        postings=_exercise_postings(option_event, stock_event, cash, fees, settles_on),
        quantity_events=(option_event, stock_event),
        contracts=(terms,),
        fee_lines=fees,
        input_refs=(),
    )


def _exercisable(
    state: LedgerState, contract_id: str, contracts: int
) -> tuple[ContractTerms, tuple[Lot, ...]]:
    """Return the terms and lots of a physically settled contract held in >= ``contracts``."""
    if type(contracts) is not int:
        raise TypeError(f"exercised contracts must be int, got {type(contracts).__name__}")
    if contracts <= 0:
        raise ValueError(f"exercised contracts must be > 0, got {contracts}")
    terms, lots = held_contract(state, contract_id)
    if terms.settlement_type is not SettlementType.PHYSICAL:
        raise LedgerInvariantError(f"contract {contract_id} is not physically settled")
    held = abs(sum(lot.quantity for lot in lots))
    if contracts > held:
        raise LedgerInvariantError(
            f"cannot exercise {contracts} contracts of {contract_id}: {held} held"
        )
    return terms, lots


def _stock_component(terms: ContractTerms) -> tuple[str, int]:
    """Return the deliverable's one stock asset and its integral shares per contract."""
    deliverable = terms.deliverable
    if len(deliverable.components) != 1 or deliverable.cash != ZERO_USD:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
            f"{terms.contract_id}: WP1 books single-component, zero-cash deliverables only",
        )
    component = deliverable.components[0]
    shares = int(component.units)
    if shares != component.units:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
            f"{terms.contract_id}: fractional deliverable of {component.units} shares",
        )
    return component.asset_id, shares


def _exercised_option(
    contract_id: str, lots: tuple[Lot, ...], delta: int
) -> tuple[QuantityEvent, str | None]:
    """Return the option lots' FIFO relief and their one campaign.

    ``delta`` shrinks the position: negative exercises a held long, positive assigns a short.
    """
    kind = QuantityKind.EXERCISE if delta < 0 else QuantityKind.ASSIGNMENT
    event = QuantityEvent(contract_id, kind, delta, None, fifo_relief(lots, delta))
    return event, _exercised_campaign(lots, len(event.reliefs))


def _exercised_campaign(lots: tuple[Lot, ...], relieved: int) -> str | None:
    """Return the one campaign of the first ``relieved`` lots, which the delivered stock joins."""
    campaigns = {lot.campaign_id for lot in lots[:relieved]}
    if len(campaigns) != 1:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
            f"exercised lots span campaigns {sorted(map(str, campaigns))}",
        )
    return campaigns.pop()


def _acquired_shares(asset_id: str, delta: int, reliefs: tuple[LotRelief, ...]) -> int:
    """Return the shares left to open after relieving held stock; delivery never goes short."""
    remainder = open_remainder(delta, reliefs)
    if remainder < 0:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
            f"delivering {-delta} {asset_id} shares needs {-remainder} more than held; "
            "short stock needs a borrow policy",
        )
    return remainder


def _exercise_postings(
    option_event: QuantityEvent,
    stock_event: QuantityEvent,
    cash: Usd,
    fees: tuple[FeeLine, ...],
    settles_on: date,
) -> tuple[Posting, ...]:
    """Return the exercise cash due, the stock leg against it, the option cost realized, fees."""
    return merge_postings(
        [
            due_cash(cash, settles_on),
            *leg_amounts(stock_event, AccountKind.STOCK_COST, cash),
            *leg_amounts(option_event, AccountKind.OPTION_COST, ZERO_USD),
            *fee_amounts(fees, settles_on),
        ]
    )


def _unit_cost(terms: ContractTerms, units: int) -> Usd:
    """Return ``AEA / units`` exactly; an inexact or sub-9-place cost is unsupported (WP7)."""
    try:
        with localcontext(EXACT):
            return Usd(terms.aggregate_exercise_amount.amount / units)
    except (Inexact, ValueError) as e:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
            f"{terms.contract_id}: AEA {terms.aggregate_exercise_amount.amount} / {units} shares "
            "is not an exact unit cost",
        ) from e
