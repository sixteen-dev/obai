"""Market specs and lookups for the synthetic generator and feature tests (ADR 0002 §5, §17).

``market(**changes)`` is a small five-session SPXW market: 2024-03-04 (Monday) to 2024-03-08,
spot fixed at 5000 (zero drift and vol), zero rates (DF = 1), one expiry 2024-03-15 and the five
strikes 4990 ... 5010. Instants are computed independently of the generator with ``zoneinfo``.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import (
    FeatureObservation,
    QuoteObservation,
    TradingSession,
    UnderlyingObservation,
)
from options_backtest.synthetic.market import MarketSpec

NY = ZoneInfo("America/New_York")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SECOND_NS = 10**9
MINUTE_NS = 60 * SECOND_NS
BEFORE_CLOSE_MIN = {"DEC": 15, "F1": 14, "F2": 13, "F3": 12, "CLOSE": 0}

MON = date(2024, 3, 4)
TUE = date(2024, 3, 5)
WED = date(2024, 3, 6)
THU = date(2024, 3, 7)
FRI = date(2024, 3, 8)
EXPIRY = date(2024, 3, 15)


def market(**changes: Any) -> MarketSpec:
    """Return the default small market with ``changes`` applied."""
    spec = MarketSpec(
        seed=1,
        first_session=MON,
        last_session=FRI,
        holidays=(),
        early_closes=(),
        index_start=Decimal(5000),
        daily_drift=Decimal(0),
        daily_vol=Decimal(0),
        sigma=Decimal("0.18"),
        rates=((28, Decimal(0)), (91, Decimal(0)), (182, Decimal(0))),
        roots=("SPXW",),
        weekly_dtes=(11,),
        strike_step=Decimal(5),
        strikes_each_side=2,
        tick=Decimal("0.05"),
        half_spread_abs=Decimal("0.05"),
        half_spread_rel=Decimal("0.02"),
        bid_size=50,
        ask_size=50,
        premium_multiplier=Decimal(100),
        deliverable_units=Decimal(100),
        overrides=(),
    )
    return replace(spec, **changes)


def weekdays(first: date, count: int) -> tuple[date, ...]:
    """Return ``count`` consecutive weekdays from ``first`` (a weekday)."""
    days: list[date] = []
    day = first
    for _ in range(count * 2):
        if len(days) == count:
            break
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    assert len(days) == count, f"{count} weekdays need more than {count * 2} days"
    return tuple(days)


def local_ns(day: date, hour: int, minute: int = 0, second: int = 0) -> int:
    """Return the UTC nanoseconds of a New York wall-clock time."""
    moment = datetime.combine(day, time(hour, minute, second), tzinfo=NY)
    return (moment - EPOCH) // timedelta(microseconds=1) * 1000


def slot_ns(day: date, slot: str, *, close_hour: int = 16) -> int:
    """Return a quote slot's instant on a session closing at ``close_hour``."""
    return local_ns(day, close_hour) - BEFORE_CLOSE_MIN[slot] * MINUTE_NS


def quote(dataset: FrozenDataset, contract_id: str, day: date, slot: str) -> QuoteObservation:
    """Return the quote ``q:{contract_id}:{day}:{slot}``; KeyError when absent."""
    wanted = f"q:{contract_id}:{day.isoformat()}:{slot}"
    return _by_id(dataset.quotes, wanted)


def quote_ids(dataset: FrozenDataset) -> set[str]:
    return {row.observation_id for row in dataset.quotes}


def index(dataset: FrozenDataset, underlying: str, field: str, day: date, slot: str) -> Any:
    """Return the underlying observation ``u:{underlying}:{field}:{day}:{slot}``."""
    wanted = f"u:{underlying}:{field}:{day.isoformat()}:{slot}"
    row: UnderlyingObservation = _by_id(dataset.underlying, wanted)
    return row


def session(dataset: FrozenDataset, day: date) -> TradingSession:
    return next(row for row in dataset.sessions if row.session_date == day)


def features(dataset: FrozenDataset, feature_id: str) -> tuple[FeatureObservation, ...]:
    """Return one feature's observations in session order."""
    return tuple(row for row in dataset.features if row.feature_id == feature_id)


def _by_id(rows: Iterable[Any], wanted: str) -> Any:
    matches = [row for row in rows if row.observation_id == wanted]
    if not matches:
        raise KeyError(wanted)
    return matches[0]
