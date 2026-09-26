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
from itertools import pairwise
from typing import Final

from options_backtest.data.records import TradingSession
from options_backtest.models.market import require_type
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
_MINUTE_NS: Final = 60 * 10**9
_DECISION_LEAD_NS: Final = 15 * _MINUTE_NS
"""DEC is the close minus 15 minutes; F1-F3 follow it at one-minute steps."""


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

    Raises:
        TypeError: If ``session`` is not a ``TradingSession``.
        ValueError: If the session closes 15 minutes or less after it opens (DEC would not
            follow OPEN).

    """
    require_type(session, TradingSession, "slot_times session")
    dec = session.close_ns - _DECISION_LEAD_NS
    if dec <= session.open_ns:
        raise ValueError(f"session {session.session_date} closes within 15 minutes of its open")
    return Slots(
        dec=dec,
        f1=dec + _MINUTE_NS,
        f2=dec + 2 * _MINUTE_NS,
        f3=dec + 3 * _MINUTE_NS,
        close=session.close_ns,
        cut=session.cutoff_ns,
    )


def slot_instant(session: TradingSession, slot: Slot) -> int:
    """Return one slot's instant; OPEN is ``open_ns``, the rest as ``slot_times``.

    Args:
        session: Session.
        slot: Slot.

    Returns:
        The instant, UTC nanoseconds.

    Raises:
        TypeError: If ``slot`` is not a ``Slot`` or ``session`` not a ``TradingSession``.

    """
    require_type(slot, Slot, "slot_instant slot")
    slots = slot_times(session)
    instants = {
        Slot.OPEN: session.open_ns,
        Slot.DEC: slots.dec,
        Slot.F1: slots.f1,
        Slot.F2: slots.f2,
        Slot.F3: slots.f3,
        Slot.CLOSE: slots.close,
        Slot.CUT: slots.cut,
    }
    return instants[slot]


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

    Raises:
        TypeError: If a bound is not a ``date`` or a row not a ``TradingSession``.
        ValueError: If ``sessions`` is not sorted by date without repeats.

    """
    _require_table(sessions)
    _require_day(start, "sessions_between start")
    _require_day(end, "sessions_between end")
    return tuple(session for session in sessions if start <= session.session_date <= end)


def next_session(sessions: Sequence[TradingSession], session_date: date) -> TradingSession | None:
    """Return the first table session strictly after ``session_date``.

    Args:
        sessions: Session table.
        session_date: Any date.

    Returns:
        The session; None when the table has none later.

    Raises:
        TypeError: If ``session_date`` is not a ``date`` or a row not a ``TradingSession``.
        ValueError: If ``sessions`` is not sorted by date without repeats.

    """
    _require_table(sessions)
    _require_day(session_date, "next_session session_date")
    return next((s for s in sessions if s.session_date > session_date), None)


def dte(session_date: date, expiry_date: date) -> int:
    """Return days to expiry: the calendar-date difference in product-local dates (§9.1 item 5).

    Args:
        session_date: Decision session.
        expiry_date: Expiry date.

    Returns:
        ``(expiry_date - session_date).days``; negative after expiry.

    Raises:
        TypeError: If either argument is not exactly a ``date``.

    """
    _require_day(session_date, "dte session_date")
    _require_day(expiry_date, "dte expiry_date")
    return (expiry_date - session_date).days


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
        TypeError: If ``schedule`` is not one of the three schedules.
        ValueError: If ``session`` is not in ``sessions`` or ``sessions`` is not sorted by date
            without repeats.

    """
    _require_table(sessions)
    if session not in sessions:
        raise ValueError(f"session {session.session_date} is not in the session table")
    match schedule:
        case DailySchedule():
            return True
        case WeeklySchedule():
            return _weekly_entry(schedule.weekday, session, sessions) == session
        case MonthlySchedule():
            return _monthly_entry(schedule.session_ordinal, session, sessions) == session
    raise TypeError(f"unsupported entry schedule {type(schedule).__name__}")


def _weekly_entry(
    weekday: int, session: TradingSession, sessions: Sequence[TradingSession]
) -> TradingSession | None:
    """Return the first session of ``session``'s ISO week whose ISO weekday is >= ``weekday``."""
    week = session.session_date.isocalendar()[:2]
    return next(
        (
            s
            for s in sessions
            if s.session_date.isocalendar()[:2] == week and s.session_date.isoweekday() >= weekday
        ),
        None,
    )


def _monthly_entry(
    ordinal: int, session: TradingSession, sessions: Sequence[TradingSession]
) -> TradingSession | None:
    """Return the ``ordinal``-th table session of ``session``'s calendar month, if any."""
    month = (session.session_date.year, session.session_date.month)
    in_month = [s for s in sessions if (s.session_date.year, s.session_date.month) == month]
    return in_month[ordinal - 1] if ordinal <= len(in_month) else None


def _require_table(sessions: Sequence[TradingSession]) -> None:
    for session in sessions:
        require_type(session, TradingSession, "sessions item")
    for previous, current in pairwise(sessions):
        if current.session_date <= previous.session_date:
            raise ValueError(
                "sessions must be sorted by date without repeats, got "
                f"{previous.session_date} then {current.session_date}"
            )


def _require_day(value: object, field: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{field} must be exactly date, got {type(value).__name__}")
