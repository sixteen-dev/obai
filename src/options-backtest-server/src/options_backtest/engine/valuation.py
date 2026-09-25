"""Account valuation and reconciliation views (ADR 0001 §5 "Views"; design §11.1).

``nlv = CASH + ΣRECEIVABLE + ΣPAYABLE + Σ quantity x multiplier x mark + Σ shares x price``.
Reserves never enter it, and realized/unrealized P&L are reconciliation views of the same
ledger, never added to it again. A held instrument without a mark raises ``MissingMarkError``;
it is never valued at zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from options_backtest.errors import MissingMarkError
from options_backtest.models.ledger import CAPITAL_ACCOUNT, CASH_KINDS, AccountKind, LedgerState
from options_backtest.models.market import Deliverable, DeliverableComponent, Quote
from options_backtest.money import ZERO_USD, Price, Usd


class MarkBasis(StrEnum):
    """Option mark policy: MID, or NATURAL (bid for longs, ask for shorts)."""

    MID = "mid"
    NATURAL = "natural"


@dataclass(frozen=True, slots=True)
class Valuation:
    """An account valuation at one set of marks.

    Attributes:
        basis: Option mark policy used.
        nlv: Net liquidation value.
        unrealized: Marked value of every open lot less its cost.

    """

    basis: MarkBasis
    nlv: Usd
    unrealized: Usd


def stock_value(asset_id: str, shares: int, price: Price) -> Usd:
    """Return the exact value of a long stock holding, ``shares x price``.

    Stock has no contract terms, so the product goes through ``Deliverable.value_usd``, one of
    the two sanctioned price-to-money conversions (ADR 0001 §2), as one component of
    ``shares`` units.

    Args:
        asset_id: Stock asset.
        shares: Shares held, >= 0 (WP1 has no short stock).
        price: Price per share.

    Returns:
        The holding's value.

    Raises:
        TypeError: If ``shares`` is not exactly ``int``.
        ValueError: If ``shares`` is negative.

    """
    if type(shares) is not int:
        raise TypeError(f"stock_value shares must be int, got {type(shares).__name__}")
    if shares < 0:
        raise ValueError(f"stock_value needs shares >= 0, got {shares}")
    if shares == 0:
        return ZERO_USD
    component = DeliverableComponent(asset_id, Decimal(shares))
    return Deliverable(f"holding:{asset_id}", (component,), ZERO_USD).value_usd({asset_id: price})


def _has_mark(
    state: LedgerState,
    instrument: str,
    quotes: Mapping[str, Quote],
    stock_prices: Mapping[str, Price],
) -> bool:
    if instrument in state.contracts:
        return instrument in quotes
    return instrument in stock_prices


def _option_mark(quote: Quote, quantity: int, basis: MarkBasis) -> Price:
    if basis is MarkBasis.MID:
        return Price.mid(quote.bid, quote.ask)
    return quote.bid if quantity > 0 else quote.ask


def _kind_total(state: LedgerState, kinds: frozenset[AccountKind]) -> Usd:
    return sum(
        (amount for account, amount in state.balances.items() if account.kind in kinds),
        start=ZERO_USD,
    )


def value_account(
    state: LedgerState,
    quotes: Mapping[str, Quote],
    stock_prices: Mapping[str, Price],
    basis: MarkBasis,
) -> Valuation:
    """Value every open lot at ``basis`` marks and return NLV and unrealized P&L.

    Args:
        state: Ledger state.
        quotes: Quote per held option ``contract_id``; others are ignored.
        stock_prices: Price per held stock ``asset_id``; others are ignored.
        basis: Option mark policy; stock is always marked at its price.

    Returns:
        The valuation.

    Raises:
        TypeError: If ``basis`` is not a ``MarkBasis``.
        MissingMarkError: Naming every held instrument without a mark, in instrument order.
        ValueError: If a mid mark needs more than 9 decimal places.

    """
    if not isinstance(basis, MarkBasis):
        raise TypeError(f"basis must be a MarkBasis, got {basis!r}")
    held = sorted(state.lots)
    missing = [i for i in held if not _has_mark(state, i, quotes, stock_prices)]
    if missing:
        raise MissingMarkError(missing)
    marked = ZERO_USD
    for instrument in held:
        quantity = sum(lot.quantity for lot in state.lots[instrument])
        terms = state.contracts.get(instrument)
        if terms is None:
            marked += stock_value(instrument, quantity, stock_prices[instrument])
        else:
            marked += terms.premium_usd(_option_mark(quotes[instrument], quantity, basis), quantity)
    cost = sum((lot.signed_cost for lots in state.lots.values() for lot in lots), start=ZERO_USD)
    return Valuation(basis, _kind_total(state, CASH_KINDS) + marked, marked - cost)


def net_pnl(valuation: Valuation, state: LedgerState) -> Usd:
    """Return ``nlv - capital``, with ``capital = -balance(CAPITAL)``.

    Args:
        valuation: Valuation of ``state``.
        state: Ledger state.

    Returns:
        Net profit (negative for a loss).

    """
    capital = -state.balances.get(CAPITAL_ACCOUNT, ZERO_USD)
    return valuation.nlv - capital


def reconcile(valuation: Valuation, state: LedgerState) -> Usd:
    """Return ``nlv - (capital + realized - fees + unrealized)``; exactly zero when consistent.

    Args:
        valuation: Valuation of ``state``.
        state: Ledger state.

    Returns:
        The reconciliation difference.

    """
    capital = -state.balances.get(CAPITAL_ACCOUNT, ZERO_USD)
    realized = -_kind_total(state, frozenset({AccountKind.REALIZED_PNL}))
    fees = _kind_total(state, frozenset({AccountKind.FEES}))
    return valuation.nlv - (capital + realized - fees + valuation.unrealized)
