"""The one ledger transition, the append-only journal and replay (ADR 0001 §5).

``apply_entry`` is pure and is the only way a ``LedgerState`` advances; ``replay`` commits the
entries through a fresh ``Journal``, so a replayed journal reproduces the incremental state
exactly (C34) and repeats no event id. It never checks funding: that is
``funding.funding_headroom`` on the previewed post-entry state.
"""

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Final

from options_backtest.engine.positions import apply_quantity_events
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import (
    CASH_KINDS,
    COST_KINDS,
    AccountKey,
    AccountKind,
    EntryKind,
    LedgerEntry,
    LedgerState,
    Lot,
    Posting,
    QuantityKind,
    merge_postings,
)
from options_backtest.models.market import ContractTerms, ExerciseStyle
from options_backtest.money import ZERO_USD, Usd

_RETIRING: Final = frozenset({QuantityKind.EXPIRATION, QuantityKind.ADJUST_OUT})


def apply_entry(state: LedgerState, entry: LedgerEntry) -> LedgerState:
    """Return the state after ``entry``, or raise if it would break a ledger invariant.

    Checks, in order: ``sequence == entry_count + 1`` and time never goes back; postings are
    nonzero, whole cents on CASH/RECEIVABLE/PAYABLE, canonical (merged per account and sorted)
    and sum to exactly zero (``Usd`` already bounds them to 9 places and |x| < 1e19); the FEES
    postings are exactly the fee lines merged per component; every opened lot but an ADJUST_IN
    joins the entry's campaign; no touched contract is retired (``SettledOnce``); a registered
    contract keeps equal terms; a trade comes before each touched contract's expiry, a cash
    settlement or a European exercise at or after it (``NoPositionPastExpiry``); every relief is
    the recomputed FIFO relief, no lot crosses zero and an opened lot's id is not already held
    in its instrument; no instrument but a registered contract is held short (design §12.3);
    each cost account moves exactly as its lots' ``quantity x unit_cost``; RECEIVABLE stays
    >= 0 and PAYABLE <= 0; a retired contract is flat. That ``unit_cost`` is economically right
    (``multiplier x fill price``) is the posting functions' job, not a ledger invariant.

    Args:
        state: State before the entry.
        entry: Entry to apply.

    Returns:
        The new state; ``state`` is unchanged.

    Raises:
        LedgerInvariantError: If any check fails.

    """
    _check_order(state, entry)
    _check_postings(entry.postings)
    _check_fee_lines(entry)
    _check_campaigns(entry)
    _check_not_retired(state, entry)
    contracts = _register(state, entry)
    _check_expiry(entry, contracts)
    lots = apply_quantity_events(state.lots, entry.quantity_events)
    _check_no_short_stock(lots, contracts)
    _check_costs(state.lots, lots, contracts, entry)
    balances = _post(state.balances, entry.postings)
    retired = _retire(state.retired, entry, contracts, lots)
    return LedgerState(entry.sequence, entry.at_ns, balances, lots, contracts, retired)


def _check_order(state: LedgerState, entry: LedgerEntry) -> None:
    if entry.sequence != state.entry_count + 1:
        raise LedgerInvariantError(
            f"entry {entry.event_id} has sequence {entry.sequence}, "
            f"expected {state.entry_count + 1}"
        )
    if entry.at_ns < state.last_at_ns:
        raise LedgerInvariantError(
            f"entry {entry.event_id} at_ns {entry.at_ns} precedes the last entry's "
            f"{state.last_at_ns}"
        )


def _check_postings(postings: Sequence[Posting]) -> None:
    for posting in postings:
        if posting.amount == ZERO_USD:
            raise LedgerInvariantError(f"zero posting to {posting.account}")
        if posting.account.kind in CASH_KINDS and not posting.amount.is_cents():
            raise LedgerInvariantError(
                f"sub-cent posting {posting.amount.amount} to {posting.account}"
            )
    if any(earlier.account >= later.account for earlier, later in pairwise(postings)):
        raise LedgerInvariantError("postings are not canonical: merge per account and sort")
    total = sum((posting.amount for posting in postings), start=ZERO_USD)
    if total != ZERO_USD:
        raise LedgerInvariantError(f"postings sum to {total.amount}, not zero")


def _check_fee_lines(entry: LedgerEntry) -> None:
    """Require the FEES postings to be exactly the entry's fee lines, merged per component."""
    lines = merge_postings(
        (AccountKey(AccountKind.FEES, line.component_id), line.amount) for line in entry.fee_lines
    )
    posted = tuple(p for p in entry.postings if p.account.kind is AccountKind.FEES)
    if lines != posted:
        raise LedgerInvariantError(
            f"entry {entry.event_id} fee lines do not match its FEES postings"
        )


def _check_campaigns(entry: LedgerEntry) -> None:
    """Require every lot the entry opens to join the entry's campaign; ADJUST_IN keeps its own."""
    for event in entry.quantity_events:
        if event.opened is None or event.kind is QuantityKind.ADJUST_IN:
            continue
        if event.opened.campaign_id != entry.campaign_id:
            raise LedgerInvariantError(f"entry {entry.event_id} opens a lot outside its campaign")


def _touched(entry: LedgerEntry) -> set[str]:
    """Return every instrument the entry's quantity events or contract terms name."""
    touched = {event.instrument_id for event in entry.quantity_events}
    return touched | {terms.contract_id for terms in entry.contracts}


def _check_not_retired(state: LedgerState, entry: LedgerEntry) -> None:
    stale = sorted(_touched(entry) & state.retired)
    if stale:
        raise LedgerInvariantError(f"entry {entry.event_id} touches retired contracts {stale}")


def _register(state: LedgerState, entry: LedgerEntry) -> dict[str, ContractTerms]:
    """Return the registered contracts plus the entry's new ones; known ids keep equal terms."""
    contracts = dict(state.contracts)
    for terms in entry.contracts:
        registered = contracts.get(terms.contract_id)
        if registered is None and terms.contract_id in state.lots:
            raise LedgerInvariantError(
                f"contract id {terms.contract_id} collides with a held stock instrument"
            )
        if registered is not None and registered != terms:
            raise LedgerInvariantError(
                f"contract {terms.contract_id} carries terms other than its registered terms"
            )
        contracts[terms.contract_id] = terms
    return contracts


def _check_expiry(entry: LedgerEntry, contracts: Mapping[str, ContractTerms]) -> None:
    """Require each touched contract's expiry to fit the entry (``NoPositionPastExpiry``).

    A trade precedes expiry; a cash settlement, or a European exercise, is at or after it. WP3
    owns the stricter last-tradable and exercise-cutoff times.
    """
    terms = [contracts[i] for i in sorted(_touched(entry)) if i in contracts]
    at_ns = entry.at_ns
    if entry.kind is EntryKind.TRADE:
        wrong = [t.contract_id for t in terms if t.expires_at_ns <= at_ns]
        rule = "trade only before their expiry"
    elif entry.kind is EntryKind.CASH_SETTLEMENT:
        wrong = [t.contract_id for t in terms if t.expires_at_ns > at_ns]
        rule = "cash-settle only at or after their expiry"
    elif entry.kind is EntryKind.PHYSICAL_EXERCISE:
        european = [t for t in terms if t.exercise_style is ExerciseStyle.EUROPEAN]
        wrong = [t.contract_id for t in european if t.expires_at_ns > at_ns]
        rule = "exercise European contracts only at or after their expiry"
    else:
        return
    if wrong:
        raise LedgerInvariantError(f"entry {entry.event_id} at {at_ns}: {rule}; {wrong} are not")


def _check_no_short_stock(
    lots: Mapping[str, tuple[Lot, ...]], contracts: Mapping[str, ContractTerms]
) -> None:
    """Reject a short position in any instrument that is not a registered contract."""
    short = sorted(i for i, held in lots.items() if i not in contracts and held[0].quantity < 0)
    if short:
        raise LedgerInvariantError(f"short stock {short} is unsupported (design §12.3)")


def _lots_cost(lots: Iterable[Lot]) -> Usd:
    return sum((lot.signed_cost for lot in lots), start=ZERO_USD)


def _check_costs(
    before: Mapping[str, tuple[Lot, ...]],
    after: Mapping[str, tuple[Lot, ...]],
    contracts: Mapping[str, ContractTerms],
    entry: LedgerEntry,
) -> None:
    """Require each touched cost account to move exactly as its instrument's lot cost."""
    posted = {p.account: p.amount for p in entry.postings if p.account.kind in COST_KINDS}
    touched = {event.instrument_id for event in entry.quantity_events}
    touched |= {account.ref for account in posted}
    for instrument in sorted(touched):
        kind = AccountKind.OPTION_COST if instrument in contracts else AccountKind.STOCK_COST
        change = _lots_cost(after.get(instrument, ())) - _lots_cost(before.get(instrument, ()))
        amount = posted.pop(AccountKey(kind, instrument), ZERO_USD)
        if amount != change:
            raise LedgerInvariantError(
                f"{kind} of {instrument} moves by {amount.amount}, "
                f"its lots' cost by {change.amount}"
            )
    if posted:
        raise LedgerInvariantError(f"cost postings on the wrong cost kind: {sorted(posted)}")


def _post(balances: Mapping[AccountKey, Usd], postings: Sequence[Posting]) -> dict[AccountKey, Usd]:
    """Return the balances after ``postings``; zero balances are dropped; open items keep sign."""
    result = dict(balances)
    for posting in postings:
        total = result.pop(posting.account, ZERO_USD) + posting.amount
        if total != ZERO_USD:
            result[posting.account] = total
        if posting.account.kind is AccountKind.RECEIVABLE and total < ZERO_USD:
            raise LedgerInvariantError(f"RECEIVABLE {posting.account.ref} would be {total.amount}")
        if posting.account.kind is AccountKind.PAYABLE and total > ZERO_USD:
            raise LedgerInvariantError(f"PAYABLE {posting.account.ref} would be {total.amount}")
    return result


def _retire(
    retired: frozenset[str],
    entry: LedgerEntry,
    contracts: Mapping[str, ContractTerms],
    lots: Mapping[str, tuple[Lot, ...]],
) -> frozenset[str]:
    """Return ``retired`` plus the contracts the entry expires or adjusts away; they are flat."""
    retiring = {e.instrument_id for e in entry.quantity_events if e.kind in _RETIRING}
    unknown = sorted(instrument for instrument in retiring if instrument not in contracts)
    if unknown:
        raise LedgerInvariantError(f"only registered contracts retire; {unknown} are not")
    still_held = sorted(instrument for instrument in retiring if instrument in lots)
    if still_held:
        raise LedgerInvariantError(f"contracts {still_held} still hold lots and cannot retire")
    return retired | retiring


class Journal:
    """Append-only journal: the ledger's only mutable object.

    It rejects a repeated ``event_id`` and applies each entry with ``apply_entry``; a rejected
    entry leaves the journal unchanged.
    """

    def __init__(self) -> None:
        """Start an empty journal."""
        self._entries: list[LedgerEntry] = []
        self._event_ids: set[str] = set()
        self._state = LedgerState.empty()

    @property
    def state(self) -> LedgerState:
        """Return the state after every committed entry."""
        return self._state

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """Return the committed entries in order."""
        return tuple(self._entries)

    def commit(self, entry: LedgerEntry) -> LedgerState:
        """Apply and append ``entry``.

        Args:
            entry: Next entry.

        Returns:
            The state after the entry.

        Raises:
            LedgerInvariantError: If the event id was already committed or ``apply_entry``
                rejects the entry.

        """
        if entry.event_id in self._event_ids:
            raise LedgerInvariantError(f"event {entry.event_id} is already committed")
        state = apply_entry(self._state, entry)
        self._entries.append(entry)
        self._event_ids.add(entry.event_id)
        self._state = state
        return state


def replay(entries: Iterable[LedgerEntry]) -> LedgerState:
    """Commit ``entries`` through a fresh ``Journal``: a replayed journal is one it would accept.

    Args:
        entries: Entries in journal order.

    Returns:
        The final state; equal to the incremental state of the same entries.

    Raises:
        LedgerInvariantError: If an ``event_id`` repeats or ``apply_entry`` rejects an entry.

    """
    journal = Journal()
    for entry in entries:
        journal.commit(entry)
    return journal.state
