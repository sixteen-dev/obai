"""Exact expiry bounds, campaign encumbrances and funding headroom (ADR 0001 §5; design §11.2).

Payoff at expiry of one expiry and one single-component deliverable is
``h·S + Σ q_i x intrinsic_i(S)``, piecewise linear in the deliverable price S with kinks at
``(AEA_i - cash_i) / units_i``; its extremes over S >= 0 lie at a kink unless the upper-tail
slope ``h + Σ_calls q_i x units_i`` makes it unbounded. Reserves are computed from lots, never
stored, and never net between campaigns. A fill is funded iff ``funding_headroom`` of the
post-fill state is >= 0 (``R1Campaign.Funded``, ``FullyFunded``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.valuation import stock_value
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import CASH_ACCOUNT, AccountKind, LedgerState
from options_backtest.models.market import ContractTerms, OptionType, SettlementType
from options_backtest.money import EXACT, ZERO_USD, Price, Usd


@dataclass(frozen=True, slots=True)
class ExpiryBounds:
    """Range of expiry value of one same-expiry, same-deliverable position.

    Attributes:
        min_value: Lowest value over S >= 0, including ``entry_cash``; None when unbounded.
        max_value: Highest value over S >= 0, including ``entry_cash``; None when unbounded.
        upper_slope: d(value)/dS as S -> infinity.
        breakpoints: 0 and every kink above 0, ascending.

    """

    min_value: Usd | None
    max_value: Usd | None
    upper_slope: Decimal
    breakpoints: tuple[Price, ...]


@dataclass(frozen=True, slots=True)
class Encumbrance:
    """Cash one campaign's open position reserves; never an asset or an expense.

    Attributes:
        settlement: Maximum contractual expiry outflow, ``max(0, -min payoff)``.
        fee_provision: Estimated exit fees, ``Σ_i |q_i| x max(trade_per_contract, lifecycle_i)``
            with ``lifecycle_i`` the cash-settlement rate of a CASH contract and the
            exercise/assignment rate of a PHYSICAL one: the costliest way each contract can end.

    """

    settlement: Usd
    fee_provision: Usd


def expiry_bounds(
    holdings: Sequence[tuple[ContractTerms, int]], stock_shares: int, entry_cash: Usd
) -> ExpiryBounds:
    """Return the exact expiry value range, checking every kink and the upper-tail slope.

    Args:
        holdings: Option contracts and signed quantities; one expiry, one single-component
            deliverable.
        stock_shares: Long shares of the deliverable asset held alongside, >= 0.
        entry_cash: Signed net cash received at entry (F01: -500 + 90 gives max loss 410).

    Returns:
        The bounds; a negative upper slope leaves ``min_value`` None, a positive one
        ``max_value``.

    Raises:
        TypeError: If a quantity or ``stock_shares`` is not exactly ``int``.
        ValueError: If ``holdings`` is empty or a quantity is zero; or a kink is not an exact
            9-place price.
        decimal.Inexact: If a kink ``(AEA - cash) / units`` does not terminate.
        UnsupportedLifecycle: UNSUPPORTED_ACCOUNT_STATE for several expiries or deliverables,
            a multi-component deliverable or short stock.

    """
    asset_id, units = _single_deliverable(holdings)
    if type(stock_shares) is not int:
        raise TypeError(f"stock_shares must be int, got {type(stock_shares).__name__}")
    if stock_shares < 0:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE, "short stock needs a borrow policy"
        )
    breakpoints = _breakpoints(holdings)
    values = [
        _payoff(holdings, asset_id, stock_shares, level) + entry_cash for level in breakpoints
    ]
    call_quantity = sum(q for terms, q in holdings if terms.option_type is OptionType.CALL)
    with localcontext(EXACT):
        slope = stock_shares + call_quantity * units
    return ExpiryBounds(
        min_value=None if slope < 0 else min(values),
        max_value=None if slope > 0 else max(values),
        upper_slope=slope,
        breakpoints=breakpoints,
    )


def _single_deliverable(holdings: Sequence[tuple[ContractTerms, int]]) -> tuple[str, Decimal]:
    """Return the one deliverable's asset and units after validating the holdings' shape."""
    if not holdings:
        raise ValueError("expiry_bounds needs at least one option holding")
    if any(type(quantity) is not int for _, quantity in holdings):
        raise TypeError("expiry_bounds quantities must be int")
    if any(quantity == 0 for _, quantity in holdings):
        raise ValueError("expiry_bounds quantities must be nonzero")
    expiries = {terms.expires_at_ns for terms, _ in holdings}
    deliverables = {terms.deliverable for terms, _ in holdings}
    if len(expiries) != 1 or len(deliverables) != 1:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
            f"expiry bounds need one expiry and one deliverable, got {len(expiries)} "
            f"and {len(deliverables)}",
        )
    components = deliverables.pop().components
    if len(components) != 1:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE, "expiry bounds need a single-component deliverable"
        )
    return components[0].asset_id, components[0].units


def _breakpoints(holdings: Sequence[tuple[ContractTerms, int]]) -> tuple[Price, ...]:
    """Return 0 and every positive kink ``(AEA - cash) / units``, ascending."""
    kinks = {Decimal(0)}
    for terms, _ in holdings:
        deliverable = terms.deliverable
        with localcontext(EXACT):
            kink = (
                terms.aggregate_exercise_amount.amount - deliverable.cash.amount
            ) / deliverable.components[0].units
        if kink > 0:
            kinks.add(kink)
    return tuple(Price(kink) for kink in sorted(kinks))


def _payoff(
    holdings: Sequence[tuple[ContractTerms, int]], asset_id: str, stock_shares: int, level: Price
) -> Usd:
    """Return ``h·S + Σ q_i x intrinsic_i(S)`` at deliverable price ``level``."""
    prices = {asset_id: level}
    options = sum(
        (terms.intrinsic_usd(prices).scaled_by(quantity) for terms, quantity in holdings),
        start=ZERO_USD,
    )
    return stock_value(asset_id, stock_shares, level) + options


def campaign_encumbrances(
    state: LedgerState, schedule: AssumedFlatFeeSchedule
) -> Mapping[str, Encumbrance]:
    """Return each campaign's reserve over its open option lots; campaigns never net.

    The settlement reserve is ``max(0, -min payoff)`` with ``entry_cash = 0``, so a credit is
    never subtracted twice (F01: 500; a physical short put: its full AEA). The fee provision
    reserves each contract's costliest exit, a trade close or its settlement or assignment, so a
    lifecycle fee above the trade fee stays funded (ADR 0001 §11). Stock cover and stock
    encumbrance are WP7.

    Args:
        state: Ledger state.
        schedule: Fee schedule for the exit-fee provision.

    Returns:
        Encumbrance per campaign holding option lots, in campaign order.

    Raises:
        LedgerInvariantError: If an option lot carries no campaign.
        UnsupportedLifecycle: UNSUPPORTED_ACCOUNT_STATE if a campaign's expiry loss is
            unbounded or its lots do not fit ``expiry_bounds``.

    """
    positions = _campaign_positions(state)
    encumbrances: dict[str, Encumbrance] = {}
    for campaign_id in sorted(positions):
        holdings = tuple(
            (state.contracts[contract_id], quantity)
            for contract_id, quantity in sorted(positions[campaign_id].items())
        )
        bounds = expiry_bounds(holdings, 0, ZERO_USD)
        if bounds.min_value is None:
            raise UnsupportedLifecycle(
                ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
                f"campaign {campaign_id} has an unbounded expiry loss; it cannot be reserved",
            )
        fee_provision = sum(
            (_exit_fee(terms, schedule).scaled_by(abs(quantity)) for terms, quantity in holdings),
            start=ZERO_USD,
        )
        encumbrances[campaign_id] = Encumbrance(
            settlement=max(ZERO_USD, -bounds.min_value), fee_provision=fee_provision
        )
    return encumbrances


def _exit_fee(terms: ContractTerms, schedule: AssumedFlatFeeSchedule) -> Usd:
    """Return the costliest per-contract fee to end ``terms``: a trade close or its lifecycle."""
    if terms.settlement_type is SettlementType.CASH:
        lifecycle = schedule.cash_settlement_per_contract
    else:
        lifecycle = schedule.exercise_assignment_per_contract
    return max(schedule.trade_per_contract, lifecycle)


def _campaign_positions(state: LedgerState) -> dict[str, dict[str, int]]:
    """Return the signed option quantity per contract of every campaign."""
    option_lots = [
        lot for cid, lots in state.lots.items() if cid in state.contracts for lot in lots
    ]
    positions: dict[str, dict[str, int]] = {}
    for lot in option_lots:
        if lot.campaign_id is None:
            raise LedgerInvariantError(
                f"option lot {lot.lot_id} of {lot.instrument_id} has no campaign"
            )
        held = positions.setdefault(lot.campaign_id, {})
        held[lot.instrument_id] = held.get(lot.instrument_id, 0) + lot.quantity
    return positions


def funding_headroom(state: LedgerState, schedule: AssumedFlatFeeSchedule) -> Usd:
    """Return ``CASH - Σ|PAYABLE| - Σ encumbrances``; receivables never count.

    Args:
        state: Ledger state, normally the post-fill preview.
        schedule: Fee schedule for the exit-fee provisions.

    Returns:
        The headroom; the state is fully funded iff it is >= 0.

    Raises:
        LedgerInvariantError: As ``campaign_encumbrances``.
        UnsupportedLifecycle: As ``campaign_encumbrances``.

    """
    payables = sum(
        (
            amount
            for account, amount in state.balances.items()
            if account.kind is AccountKind.PAYABLE
        ),
        start=ZERO_USD,
    )
    reserved = sum(
        (e.settlement + e.fee_provision for e in campaign_encumbrances(state, schedule).values()),
        start=ZERO_USD,
    )
    return state.balances.get(CASH_ACCOUNT, ZERO_USD) + payables - reserved
