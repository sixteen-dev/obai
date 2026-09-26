"""Event phases, event ids, quote-age policies and the run's session plan (ADR 0002 §7).

Events are keyed ``(at_ns, phase, seq)`` and strictly increase through a run: slots are visited
in time order (OPEN, DEC, F1, F2, F3, CLOSE, CUT) and, at one instant, by phase then ``seq``.
``seq`` counts from 1 within one (session, slot, phase), so an id is derivable by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from typing import Final

from options_backtest.data.records import TradingSession
from options_backtest.reference.calendars import Slot

QUOTE_MAX_AGE_NS: Final = 120 * 10**9
"""Largest age of an option quote used to select, decide, fill or mark (design §8.4), inclusive."""
SPOT_MAX_AGE_NS: Final = 60 * 10**9
"""Largest age of the index value used to select or to compute features, inclusive."""
MAX_SETTLEMENT_SESSIONS: Final = 5
"""Sessions after the window that may run only to settle dues; more raise."""


class Phase(IntEnum):
    """Event phases of design §10.2; OPEN 1, DEC 2-3-5, F1-F3 4, CLOSE 3, CUT 6-7."""

    SETTLE_DUE = 1
    PUBLISH = 2
    MARK = 3
    FILL = 4
    DECIDE = 5
    LIFECYCLE = 6
    SNAPSHOT = 7


def event_id(session_date: date, slot: Slot, phase: Phase, seq: int) -> str:
    """Return ``{YYYY-MM-DD}:{slot}:{phase}:{seq}``, e.g. ``2024-03-04:F1:4:1``.

    Also the ``event_id`` of the ledger entry the event books.

    Args:
        session_date: Session.
        slot: Slot.
        phase: Phase.
        seq: 1-based count within (session, slot, phase).

    Returns:
        The event id.

    Raises:
        ValueError: If ``seq < 1``.

    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RunCalendar:
    """The sessions a run visits.

    Attributes:
        window: Table sessions from ``start_date`` to ``end_date`` inclusive; the last is the
            final session.
        after: Up to ``MAX_SETTLEMENT_SESSIONS`` following table sessions, used only while a
            RECEIVABLE or PAYABLE is outstanding.

    """

    window: tuple[TradingSession, ...]
    after: tuple[TradingSession, ...]


def run_calendar(
    sessions: Sequence[TradingSession], start_date: date, end_date: date
) -> RunCalendar:
    """Return the window and the settle-only sessions after it.

    Args:
        sessions: The dataset's session table.
        start_date: First window session; must be a table session.
        end_date: Final window session; a table session >= ``start_date``.

    Returns:
        The run's sessions.

    Raises:
        ValueError: If a window bound is not a table session, ``start_date > end_date``, or the
            table has no session after ``end_date`` (T+1 dues of the final session need one).

    """
    raise NotImplementedError
