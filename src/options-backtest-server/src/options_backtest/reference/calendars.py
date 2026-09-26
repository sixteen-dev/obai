"""Session calendar, slot clock and entry schedules, pure over the session table (ADR 0002 §3).

``scheduled_daily_v1`` (design §10.1): decision at DEC = close − 15 min, order submission at that
instant, fill attempts at F1-F3 = close − 14/13/12 min, market mark at CLOSE, ledger cutoff at
CUT = 23:59:59 local. On a 16:00 close that is 15:45, 15:46-15:48, 16:00; on a 13:00 early close
12:45, 12:46-12:48, 13:00. ``sessions`` arguments are session tables sorted by date.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from options_backtest.data.records import TradingSession
from options_backtest.models.strategy import DailySchedule, MonthlySchedule, WeeklySchedule


class Slot(StrEnum):
    """The session's event slots, in time order."""

    OPEN = "OPEN"
    DEC = "DEC"
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    CLOSE = "CLOSE"
    CUT = "CUT"


FILL_SLOTS: Final = (Slot.F1, Slot.F2, Slot.F3)
QUOTE_SLOTS: Final = (Slot.DEC, Slot.F1, Slot.F2, Slot.F3, Slot.CLOSE)
"""Slots at which the generator observes the chain and the index."""


@dataclass(frozen=True, slots=True)
class Slots:
    """The decision-to-cutoff instants of one session, UTC nanoseconds.

    Attributes:
        dec: ``close_ns - 15 min``: decision and order submission.
        f1: ``close_ns - 14 min``: first fill attempt.
        f2: ``close_ns - 13 min``: second fill attempt.
        f3: ``close_ns - 12 min``: last fill attempt; an unfilled order is cancelled.
        close: ``close_ns``: market mark.
        cut: ``cutoff_ns``: settlement cutoff and account snapshot.

    """

    dec: int
    f1: int
    f2: int
    f3: int
    close: int
    cut: int


def slot_times(session: TradingSession) -> Slots:
    """Return the session's DEC..CUT instants.

    Args:
        session: Session.

    Returns:
        Its slots, computed from ``close_ns`` and ``cutoff_ns`` only.

    """
    raise NotImplementedError


def slot_instant(session: TradingSession, slot: Slot) -> int:
    """Return one slot's instant; OPEN is ``open_ns``, the rest as ``slot_times``.

    Args:
        session: Session.
        slot: Slot.

    Returns:
        The instant, UTC nanoseconds.

    """
    raise NotImplementedError


def sessions_between(
    sessions: Sequence[TradingSession], start: date, end: date
) -> tuple[TradingSession, ...]:
    """Return the table's sessions with ``start <= session_date <= end``, in date order.

    Args:
        sessions: Session table.
        start: First date, inclusive.
        end: Last date, inclusive.

    Returns:
        The sessions; () when ``start > end`` or none falls inside.

    """
    raise NotImplementedError


def next_session(sessions: Sequence[TradingSession], session_date: date) -> TradingSession | None:
    """Return the first table session strictly after ``session_date``.

    Args:
        sessions: Session table.
        session_date: Any date.

    Returns:
        The session; None when the table has none later.

    """
    raise NotImplementedError


def previous_session(
    sessions: Sequence[TradingSession], session_date: date
) -> TradingSession | None:
    """Return the last table session strictly before ``session_date``.

    Args:
        sessions: Session table.
        session_date: Any date.

    Returns:
        The session; None when the table has none earlier.

    """
    raise NotImplementedError


def dte(session_date: date, expiry_date: date) -> int:
    """Return days to expiry: the calendar-date difference in product-local dates (§9.1 item 5).

    Args:
        session_date: Decision session.
        expiry_date: Expiry date.

    Returns:
        ``(expiry_date - session_date).days``; negative after expiry.

    """
    raise NotImplementedError


def scheduled(
    schedule: DailySchedule | WeeklySchedule | MonthlySchedule,
    session: TradingSession,
    sessions: Sequence[TradingSession],
) -> bool:
    """Return whether ``session`` is an entry session of ``schedule``, counting table sessions.

    Daily: every session. Weekly: the first table session of its ISO week whose ISO weekday is
    >= ``weekday`` (the weekday itself, else the next session of that week; none if the week has
    no such session). Monthly: the ``session_ordinal``-th table session of its calendar month
    (a table that starts mid-month counts from its first session).

    Args:
        schedule: The strategy's entry schedule.
        session: Session judged; must be in ``sessions``.
        sessions: Session table.

    Returns:
        Whether an entry is scheduled.

    Raises:
        ValueError: If ``session`` is not in ``sessions``.

    """
    raise NotImplementedError
