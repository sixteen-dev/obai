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
from types import MappingProxyType
from typing import Final

from options_backtest.data.records import TradingSession
from options_backtest.models.market import require_type
from options_backtest.reference.calendars import Slot, sessions_between

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


_SLOT_PHASES: Final = MappingProxyType(
    {
        Slot.OPEN: frozenset({Phase.SETTLE_DUE}),
        Slot.DEC: frozenset({Phase.PUBLISH, Phase.MARK, Phase.DECIDE}),
        Slot.F1: frozenset({Phase.FILL}),
        Slot.F2: frozenset({Phase.FILL}),
        Slot.F3: frozenset({Phase.FILL}),
        Slot.CLOSE: frozenset({Phase.MARK}),
        Slot.CUT: frozenset({Phase.LIFECYCLE, Phase.SNAPSHOT}),
    }
)
"""The phases each slot runs (``Phase``'s docstring, design §10.2)."""


def event_id(session_date: date, slot: Slot, phase: Phase, seq: int) -> str:
    """Return ``{YYYY-MM-DD}:{slot}:{phase}:{seq}``, e.g. ``2024-03-04:F1:4:1``.

    Also the ``event_id`` of the ledger entry the event books.

    Args:
        session_date: Session.
        slot: Slot.
        phase: Phase; one the slot runs (OPEN 1, DEC 2-3-5, F1-F3 4, CLOSE 3, CUT 6-7).
        seq: 1-based count within (session, slot, phase).

    Returns:
        The event id.

    Raises:
        TypeError: If an argument has the wrong type (``session_date`` exactly a ``date``).
        ValueError: If ``seq < 1`` or the slot runs no such phase.

    """
    if type(session_date) is not date:
        raise TypeError(f"event_id session_date must be a date, got {type(session_date).__name__}")
    require_type(slot, Slot, "event_id slot")
    require_type(phase, Phase, "event_id phase")
    if type(seq) is not int:
        raise TypeError(f"event_id seq must be int, got {type(seq).__name__}")
    if seq < 1:
        raise ValueError(f"event_id seq must be >= 1, got {seq}")
    if phase not in _SLOT_PHASES[slot]:
        raise ValueError(f"slot {slot.value} runs no phase {phase.value} ({phase.name})")
    return f"{session_date.isoformat()}:{slot.value}:{phase.value}:{seq}"


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
    window = sessions_between(sessions, start_date, end_date)  # validates the table and dates
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")
    bounds = (window[0].session_date, window[-1].session_date) if window else None
    if bounds != (start_date, end_date):
        raise ValueError(f"window bounds {start_date} and {end_date} must be table sessions")
    later = tuple(session for session in sessions if session.session_date > end_date)
    if not later:
        raise ValueError(f"the table has no session after end_date {end_date} for T+1 dues")
    return RunCalendar(window=window, after=later[:MAX_SETTLEMENT_SESSIONS])
