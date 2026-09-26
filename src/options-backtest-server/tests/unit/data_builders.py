"""Record and dataset builders for the data and reference unit tests (ADR 0002 §2, §17).

Instants are computed independently of ``reference.calendars``: sessions from America/New_York
wall-clock times with ``zoneinfo``, slots as the close minus 15/14/13/12 minutes.
"""

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    CoverageState,
    FeatureObservation,
    FidelityClass,
    Provenance,
    QuoteObservation,
    RateObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingField,
    UnderlyingObservation,
)
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import Price, Usd

NY = ZoneInfo("America/New_York")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SECOND_NS = 10**9
MINUTE_NS = 60 * SECOND_NS
DIGEST = hashlib.sha256(b"synthetic market spec").hexdigest()
SLOT_BEFORE_CLOSE_MIN = {"DEC": 15, "F1": 14, "F2": 13, "F3": 12, "CLOSE": 0}

MON = date(2024, 3, 4)
TUE = date(2024, 3, 5)
WED = date(2024, 3, 6)
EXPIRY = date(2024, 4, 19)


def local_ns(day: date, hour: int, minute: int = 0, second: int = 0) -> int:
    """Return the UTC nanoseconds of a New York wall-clock time."""
    moment = datetime.combine(day, time(hour, minute, second), tzinfo=NY)
    return (moment - EPOCH) // timedelta(microseconds=1) * 1000


def trading_session(day: date, *, early_close: bool = False) -> TradingSession:
    return TradingSession(
        session_date=day,
        open_ns=local_ns(day, 9, 30),
        close_ns=local_ns(day, 13 if early_close else 16),
        cutoff_ns=local_ns(day, 23, 59, 59),
        early_close=early_close,
    )


def slot_ns(session: TradingSession, slot: str) -> int:
    return session.close_ns - SLOT_BEFORE_CLOSE_MIN[slot] * MINUTE_NS


def provenance(source_id: str = "synthetic") -> Provenance:
    return Provenance(source_id, "market_spec_v1", DIGEST, "synthetic_market_v1", "0")


def option_terms(
    expiry: date, right: OptionType, strike: str, *, root: str = "SPXW", underlying: str = "SPX"
) -> ContractTerms:
    letter = "C" if right is OptionType.CALL else "P"
    units = Decimal(100)
    return ContractTerms(
        contract_id=f"{root}:{expiry.isoformat()}:{letter}:{strike}",
        option_type=right,
        strike=Price(Decimal(strike)),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        premium_multiplier=Decimal(100),
        deliverable=Deliverable(
            f"{underlying}:100", (DeliverableComponent(underlying, units),), Usd(Decimal(0))
        ),
        aggregate_exercise_amount=Usd(units * Decimal(strike)),
        expires_at_ns=local_ns(expiry, 16),
    )


def contract_version(  # noqa: PLR0913 — one keyword per field a test varies
    terms: ContractTerms,
    *,
    version: int = 1,
    root: str = "SPXW",
    listed_at_ns: int | None = None,
    effective_from_ns: int | None = None,
    effective_to_ns: int | None = None,
    known_from_ns: int | None = None,
    source_id: str = "synthetic",
) -> ContractVersion:
    listed = local_ns(MON, 9, 30) if listed_at_ns is None else listed_at_ns
    effective = listed if effective_from_ns is None else effective_from_ns
    return ContractVersion(
        version_id=f"{terms.contract_id}@v{version}",
        terms=terms,
        root=root,
        underlying_id=terms.deliverable.components[0].asset_id,
        listed_at_ns=listed,
        last_tradable_at_ns=terms.expires_at_ns,
        settlement_series=f"{terms.deliverable.components[0].asset_id}_PM",
        effective_from_ns=effective,
        effective_to_ns=effective_to_ns,
        known_from_ns=effective if known_from_ns is None else known_from_ns,
        provenance=provenance(source_id),
    )


def quote_obs(  # noqa: PLR0913 — one keyword per raw field a test pins
    contract_id: str,
    session: TradingSession,
    slot: str,
    bid: str = "2.00",
    ask: str = "2.20",
    *,
    bid_size: int = 50,
    ask_size: int = 50,
    observed_at_ns: int | None = None,
    available_at_ns: int | None = None,
    source_id: str = "synthetic",
) -> QuoteObservation:
    observed = slot_ns(session, slot) if observed_at_ns is None else observed_at_ns
    return QuoteObservation(
        observation_id=f"q:{contract_id}:{session.session_date.isoformat()}:{slot}",
        contract_id=contract_id,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=bid_size,
        ask_size=ask_size,
        observed_at_ns=observed,
        available_at_ns=observed if available_at_ns is None else available_at_ns,
        session_date=session.session_date,
        provenance=provenance(source_id),
    )


def index_obs(  # noqa: PLR0913 — one keyword per field a test varies
    underlying_id: str,
    session: TradingSession,
    slot: str,
    value: str,
    *,
    field: UnderlyingField = UnderlyingField.INDEX_VALUE,
    available_at_ns: int | None = None,
) -> UnderlyingObservation:
    observed = slot_ns(session, slot)
    return UnderlyingObservation(
        observation_id=(
            f"u:{underlying_id}:{field.value}:{session.session_date.isoformat()}:{slot}"
        ),
        underlying_id=underlying_id,
        field=field,
        value=Price(Decimal(value)),
        observed_at_ns=observed,
        available_at_ns=observed if available_at_ns is None else available_at_ns,
        session_date=session.session_date,
        provenance=provenance(),
    )


def activity_obs(
    contract_id: str,
    session: TradingSession,
    slot: str,
    volume: int,
    *,
    available_at_ns: int | None = None,
) -> ActivityObservation:
    measured = slot_ns(session, slot)
    return ActivityObservation(
        observation_id=f"a:{contract_id}:{session.session_date.isoformat()}:{slot}",
        contract_id=contract_id,
        cumulative_volume=volume,
        measured_through_ns=measured,
        available_at_ns=measured if available_at_ns is None else available_at_ns,
        provenance=provenance(),
    )


def settlement_obs(  # noqa: PLR0913 — one keyword per field a test varies
    series: str,
    day: date,
    value: str,
    *,
    correction: int = 0,
    final: bool = True,
    available_at_ns: int | None = None,
) -> SettlementObservation:
    return SettlementObservation(
        observation_id=f"s:{series}:{day.isoformat()}:c{correction}",
        settlement_series=series,
        session_date=day,
        value=Price(Decimal(value)),
        available_at_ns=local_ns(day, 17) if available_at_ns is None else available_at_ns,
        payable_date=day + timedelta(days=1),
        final=final,
        correction_version=correction,
        provenance=provenance(),
    )


def rate_obs(
    tenor_days: int, bey: str, observation_date: date, available_at_ns: int
) -> RateObservation:
    return RateObservation(
        observation_id=f"r:UST_CMT:{tenor_days}:{observation_date.isoformat()}",
        curve_id="UST_CMT",
        tenor_days=tenor_days,
        bey=Decimal(bey),
        observation_date=observation_date,
        available_at_ns=available_at_ns,
        provenance=provenance(),
    )


def feature_obs(
    feature_id: str,
    day: date,
    value: str | None,
    *,
    max_input_available_at_ns: int,
    version: str = "1",
) -> FeatureObservation:
    return FeatureObservation(
        feature_id=feature_id,
        feature_version=version,
        session_date=day,
        value=None if value is None else Decimal(value),
        max_input_available_at_ns=max_input_available_at_ns,
        warmup_count=0 if value is None else 252,
        missing_reason="warmup" if value is None else None,
        input_digest=DIGEST,
    )


def coverage(
    day: date, status: CoverageState = CoverageState.COMPLETE, *, table: str = "quotes"
) -> CoveragePartition:
    return CoveragePartition(table=table, session_date=day, status=status, note="")


def freeze(**overrides: Any) -> FrozenDataset:
    """Freeze the given tables with synthetic-dataset defaults for everything else."""
    arguments: dict[str, Any] = {
        "sessions": (),
        "contracts": (),
        "quotes": (),
        "underlying": (),
        "activity": (),
        "settlements": (),
        "rates": (),
        "features": (),
        "coverage": (),
        "fidelity": FidelityClass.SYNTHETIC_FIXTURE,
        "limitations": (),
        "calendar_version": "synthetic_weekdays_v1",
        "product_rules_version": "cboe_template_unverified_v1",
        "feature_versions": (),
        "license_policy_id": "synthetic_public",
    }
    arguments.update(overrides)
    return FrozenDataset.freeze(**arguments)


def sample_tables() -> dict[str, tuple[Any, ...]]:
    """Return every table of a small two-session synthetic market, one row or more each."""
    mon, tue = trading_session(MON), trading_session(TUE)
    put = option_terms(EXPIRY, OptionType.PUT, "4900")
    call = option_terms(EXPIRY, OptionType.CALL, "5100")
    put_id = put.contract_id
    return {
        "sessions": (mon, tue),
        "contracts": (contract_version(put), contract_version(call)),
        "quotes": (
            quote_obs(put_id, mon, "DEC"),
            quote_obs(put_id, mon, "F1", "2.05", "2.25"),
            quote_obs(call.contract_id, mon, "DEC", "0", "0.05", bid_size=0),
            quote_obs(put_id, tue, "DEC", "1.90", "2.10"),
        ),
        "underlying": (
            index_obs("SPX", mon, "DEC", "5000.00"),
            index_obs(
                "SPX",
                mon,
                "CLOSE",
                "5001.25",
                field=UnderlyingField.OFFICIAL_CLOSE,
                available_at_ns=local_ns(MON, 17),
            ),
        ),
        "activity": (activity_obs(put_id, mon, "F1", 12),),
        "settlements": (settlement_obs("SPX_PM", MON, "5001.25"),),
        "rates": (rate_obs(28, "0.0525", date(2024, 3, 1), slot_ns(mon, "DEC")),),
        "features": (
            feature_obs(
                "SPX:underlying.return_20s", MON, "0.0125", max_input_available_at_ns=mon.close_ns
            ),
        ),
        "coverage": (coverage(MON), coverage(TUE, CoverageState.GAP)),
    }


def sample_dataset(**overrides: Any) -> FrozenDataset:
    tables: dict[str, Any] = dict(sample_tables())
    tables["feature_versions"] = (("underlying.return_20s", "1"),)
    tables.update(overrides)
    return freeze(**tables)


def with_field(record: Any, **changes: Any) -> Any:
    """Return ``dataclasses.replace(record, **changes)``, which re-runs ``__post_init__``."""
    return replace(record, **changes)
