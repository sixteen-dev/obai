"""The R1 event loop: one fully funded account, one campaign at a time (ADR 0002 §7, design §10).

The only module that joins selection and execution. No formula lives here: each step calls the
module that owns it, and the loop orders the calls, books the entries and records the events.
"""

from __future__ import annotations

from options_backtest.data.manifest import FrozenDataset
from options_backtest.models.artifacts import ArtifactBundle
from options_backtest.models.run import ResolvedRun


def run(resolved: ResolvedRun, dataset: FrozenDataset) -> ArtifactBundle:
    """Simulate a resolved run on a frozen dataset.

    Setup: ``run_calendar(dataset.sessions, start_date, end_date)``; a ``Journal``; the DEPOSIT
    event ``{start}:OPEN:1:1`` books ``book_deposit(cash=account.initial_cash_usd)`` at the
    first session's ``open_ns``; a SYNTHETIC_FIXTURE dataset emits the
    SYNTHETIC_FIXTURE_NOT_HISTORICAL warning first, dated ``start_date``. Every window session
    ``d``, in order, with ``settles_on`` the next table session and every view
    ``AsOfView(dataset, slot instant)``; within one (session, slot, phase) ``seq`` follows the
    order events are listed here:

    - OPEN 1: ``book_settle_due(through=d)`` → SETTLE_DUE when not None; then a held contract
      in ``revised_contracts`` → INVALIDATED (UNSUPPORTED_CORPORATE_ACTION).
    - DEC 2: ``coverage_verdict(coverage("quotes", d))`` INVALID → INVALIDATED
      (DATA_COVERAGE_GAP). DEC 3, when held and every held leg has a ``usable_quote`` for its
      closing side (at most 120 s old; ``decide_held``'s ``quote_ok``): MARKED with the natural
      marks; when any leg lacks one, nothing (DEC 5 then defers, never MISSING_VALUATION).
      ``funding_headroom >= 0`` is asserted every DEC. DEC 5, held: ``evaluate_triggers`` then
      ``decide_held`` → ORDER_SUBMITTED (closing legs; limit = close ``D`` at DEC + allowance,
      None for FINAL, whose ``input_refs`` are the DEC observation ids that exist) or
      EXIT_DEFERRED (+ warning; detail purpose EXIT with its trigger when one holds, else
      ROLL_CLOSE) or nothing. Flat: ``decide_flat`` → for ENTRY/ROLL_OPEN, a GAP verdict gives
      ENTRY_SKIPPED/CAMPAIGN_ENDED (DATA_COVERAGE_GAP, + warning), else ``select`` (one
      ``CandidateDecision`` per call) → ORDER_SUBMITTED or ENTRY_SKIPPED/CAMPAIGN_ENDED with
      its reason (+ its warnings); END_CAMPAIGN → CAMPAIGN_ENDED; SKIP → ENTRY_SKIPPED; IDLE →
      nothing. DEC 5 emits at most one event.
    - F1, F2, F3 4, while an order is live: ``try_fill`` → FILLED (commit, consume capacity,
      campaign transition, assert ``funding_headroom >= 0`` else ``SimulationInvariantError``)
      or NOT_FILLED; an F3 nonfill adds ORDER_CANCELLED (+ EXIT_UNFILLED for a closing order),
      then, for a cancelled ROLL_OPEN, CAMPAIGN_ENDED (ROLL_OPEN_CANCELLED).
    - CLOSE 3, when unexpired legs are held (expiry date > d): each needs a quote at most 120 s
      old with status VALID, LOCKED or NO_BID, else INVALIDATED (MISSING_VALUATION); else
      MARKED, plus a MARK_OUT_OF_RANGE finding when ``package_mark_in_range`` is false.
    - CUT 6: ``settle_expiring`` → SETTLED (commit), or INVALIDATED (MISSING_SETTLEMENT) when
      the final value is not available by the cutoff. CUT 7: SNAPSHOT with the AccountPoint
      and PositionRows (marks: the CLOSE quotes).

    An INVALIDATED event stops the loop at once (no later event; the curve so far is kept).
    After the final window session's CUT, ``end_status`` applies (liquidation still held →
    INCOMPLETE, the issue of ADR 0002 §17 item 43). Unless invalid, each ``after`` session runs
    while any RECEIVABLE or PAYABLE is outstanding: OPEN 1 SETTLE_DUE and CUT 7 SNAPSHOT (NLVs
    None if a position is held); anything still due after them raises
    ``SimulationInvariantError``.

    A position reaches CUT 6 only through a close that failed: on its expiry session
    ``TimeExit`` holds (``dte = 0 <= exit_dte``), and on the final session under
    ``liquidate_at_final_session`` FINAL is submitted, so settlement always follows an
    EXIT_DEFERRED, EXIT_UNFILLED or INSUFFICIENT_CAPITAL disclosure (ADR 0002 §17 item 40).

    Args:
        resolved: The resolved run.
        dataset: The dataset its ``manifest_id`` names.

    Returns:
        The artifacts; ``result.final_equity_usd`` is the final window session's mid NLV when
        VALID.

    Raises:
        ValueError: If ``resolved.manifest_id`` is not the dataset's, or ``run_calendar``
            rejects the window.
        SimulationInvariantError: On an engine invariant breach (negative headroom after a
            commit, a ledger error on the engine's own entry, a fill raising mid NLV, dues
            outstanding after the settle-only sessions).

    """
    raise NotImplementedError
