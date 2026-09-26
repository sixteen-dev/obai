"""Strategies, markets, ledger states and decision contexts for the D2 unit tests (ADR 0002 §6-§8).

The default market is the e2e one (``tests/e2e/defaults.toml``) over five sessions: 2024-03-04
(Monday) to 2024-03-08, spot 5000.00 at every slot, zero rates (DF 1), one expiry 2024-03-15 and
the strikes 4890 ... 5110. The default strategy is the e2e SPXW put credit vertical: sell the put
at moneyness 0.98 +- 0.0005 (4900), buy the put 5 below (4895), fixed 1 contract, daily entries.
"""

import copy
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from data_builders import local_ns

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import FidelityClass, TradingSession
from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import OrderLeg, OrderPurpose
from options_backtest.engine.selector import SelectionContext
from options_backtest.engine.trades import book_deposit
from options_backtest.ingest import load_strategy
from options_backtest.models.ledger import LedgerState
from options_backtest.models.market import OptionType, Quote
from options_backtest.models.result import (
    CalculationStatus,
    ResultProvenance,
    RunWarning,
    SimulationResult,
    WarningCode,
)
from options_backtest.models.run import FEE_SCHEDULES, FLAT_FEE_SCHEDULE_ID
from options_backtest.models.strategy_checks import ValidatedStrategy
from options_backtest.money import Price, Usd
from options_backtest.reference.calendars import Slot, next_session, slot_times
from options_backtest.reference.products import SPXW_RULES
from options_backtest.synthetic.market import MarketSpec, QuotePin, generate

type Document = dict[str, Any]

MON = date(2024, 3, 4)
TUE = date(2024, 3, 5)
WED = date(2024, 3, 6)
THU = date(2024, 3, 7)
FRI = date(2024, 3, 8)
EXPIRY = date(2024, 3, 15)
LATER_EXPIRY = date(2024, 3, 21)
SCHEDULE: AssumedFlatFeeSchedule = FEE_SCHEDULES[FLAT_FEE_SCHEDULE_ID]

_BASE_DOCUMENT: Document = {
    "schema_version": 1,
    "name": "D2 unit tests: SPXW put credit vertical (synthetic, no performance implied)",
    "product": {
        "underlying_symbol": "SPX",
        "allowed_option_roots": ["SPXW"],
        "family": "us_european_pm_cash_index",
    },
    "structure": "vertical",
    "legs": [
        {
            "leg_id": "short_put",
            "side": "sell",
            "option_type": "put",
            "ratio": 1,
            "expiry_selection": {
                "method": "target_dte",
                "target_dte": 11,
                "min_dte": 9,
                "max_dte": 13,
            },
            "strike_selection": {
                "method": "moneyness",
                "target_strike_to_spot": 0.98,
                "tolerance": 0.0005,
            },
        },
        {
            "leg_id": "long_put",
            "side": "buy",
            "option_type": "put",
            "ratio": 1,
            "expiry_selection": {"method": "same_as", "anchor_leg_id": "short_put"},
            "strike_selection": {
                "method": "strike_offset",
                "anchor_leg_id": "short_put",
                "offset_price_units": "-5",
            },
        },
    ],
    "clock_profile": "scheduled_daily_v1",
    "entry": {"schedule": {"frequency": "daily"}, "all_conditions": []},
    "exits": {
        "take_profit": None,
        "stop_loss": None,
        "exit_dte": 0,
        "max_holding_sessions": 252,
    },
    "roll": {"mode": "disabled"},
    "account": {
        "currency": "USD",
        "initial_cash_usd": "10000.00",
        "policy": "fully_funded_v1",
        "max_campaign_risk_fraction": 0.10,
        "max_total_risk_fraction": 0.20,
        "max_concurrent_campaigns": 1,
    },
    "sizing": {"method": "fixed_contracts", "contracts": 1},
    "liquidity": {
        "min_open_interest": 0,
        "min_cumulative_volume": 0,
        "max_absolute_spread_price_units": "0.50",
        "max_relative_spread": 0.25,
        "require_positive_bid_for_entry": True,
    },
    "execution": {
        "model": "natural_package_limit_v1",
        "participation_fraction": 0.10,
        "max_contracts_per_order": 10,
        "price_allowance_usd": "0.00",
        "fill_attempts": 3,
        "quantity_policy": "all_or_none_complete_packages",
    },
    "fee_schedule_id": FLAT_FEE_SCHEDULE_ID,
    "funding_policy_id": "illustrative_zero_interest_no_borrow_v1",
    "comparison_policy_id": "historical_usd_cash_comparison_v1",
    "end_policy": "liquidate_at_final_session",
}


def merged(base: Mapping[str, Any], patch: Mapping[str, Any]) -> Document:
    """Return ``base`` with ``patch`` merged in: objects key by key, anything else replaced."""
    result: Document = copy.deepcopy(dict(base))
    for key, value in patch.items():
        current = result.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            result[key] = merged(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def document(**patch: Any) -> Document:
    """Return the default strategy document with a merge patch applied."""
    return merged(_BASE_DOCUMENT, patch)


def strategy(**patch: Any) -> ValidatedStrategy:
    """Return the default strategy, merge-patched, through ``load_strategy``."""
    return load_strategy(json.dumps(document(**patch)).encode("utf-8"))


def strategy_with(whole: Mapping[str, Any], **patch: Any) -> ValidatedStrategy:
    """Return the default strategy, merge-patched, with the ``whole`` top-level fields replaced."""
    return load_strategy(json.dumps({**document(**patch), **whole}).encode("utf-8"))


def target(dte: int, low: int, high: int) -> Document:
    """Return a ``target_dte`` expiry selection."""
    return {"method": "target_dte", "target_dte": dte, "min_dte": low, "max_dte": high}


def same_as(anchor: str) -> Document:
    """Return a ``same_as`` expiry selection."""
    return {"method": "same_as", "anchor_leg_id": anchor}


def leg(  # noqa: PLR0913 — one keyword per leg field a test varies
    leg_id: str,
    side: str,
    option_type: str,
    *,
    expiry: Document | None = None,
    moneyness: float | None = None,
    tolerance: float = 0.0005,
    offset: tuple[str, str] | None = None,
    delta: tuple[float, float] | None = None,
) -> Document:
    """Return one strategy leg: target_dte 11 in [9, 13] (or ``expiry``), one strike selector."""
    if offset is not None:
        strike: Document = {
            "method": "strike_offset",
            "anchor_leg_id": offset[0],
            "offset_price_units": offset[1],
        }
    elif delta is not None:
        strike = {"method": "delta", "target_delta": delta[0], "tolerance": delta[1]}
    else:
        strike = {"method": "moneyness", "target_strike_to_spot": moneyness, "tolerance": tolerance}
    return {
        "leg_id": leg_id,
        "side": side,
        "option_type": option_type,
        "ratio": 1,
        "expiry_selection": expiry or target(11, 9, 13),
        "strike_selection": strike,
    }


def market(**changes: Any) -> MarketSpec:
    """Return the e2e default market (five sessions from MON) with ``changes`` applied."""
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
        strikes_each_side=22,
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


def put_id(strike: int | str, expiry: date = EXPIRY) -> str:
    """Return the SPXW put's contract id."""
    return f"SPXW:{expiry.isoformat()}:P:{strike}"


def call_id(strike: int | str, expiry: date = EXPIRY) -> str:
    """Return the SPXW call's contract id."""
    return f"SPXW:{expiry.isoformat()}:C:{strike}"


def quote_id(contract: str, day: date = MON, slot: str = "DEC") -> str:
    """Return a generated quote's observation id."""
    return f"q:{contract}:{day.isoformat()}:{slot}"


def pin(  # noqa: PLR0913 — one keyword per QuotePin field a test varies
    contract: str,
    bid: str,
    ask: str,
    *,
    day: date = MON,
    slots: tuple[Slot, ...] = (Slot.DEC,),
    bid_size: int = 50,
    ask_size: int = 50,
) -> QuotePin:
    """Return a quote pin, at DEC on MON by default."""
    return QuotePin(
        contract=contract,
        session=day,
        slots=slots,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=bid_size,
        ask_size=ask_size,
    )


def g01_pins(day: date = MON, expiry: date = EXPIRY) -> tuple[QuotePin, ...]:
    """Return G01's entry pins at DEC: P4900 2.00/2.20 and P4895 1.00/1.10, sizes 50."""
    return (
        pin(put_id(4900, expiry), "2.00", "2.20", day=day),
        pin(put_id(4895, expiry), "1.00", "1.10", day=day),
    )


def dataset(*overrides: Any, **changes: Any) -> FrozenDataset:
    """Generate the default market with ``overrides`` (in order) and field ``changes``."""
    return generate(market(overrides=tuple(overrides), **changes))


def refrozen(frozen: FrozenDataset, **tables: Any) -> FrozenDataset:
    """Return ``frozen`` re-frozen with the named tables replaced (same manifest fields)."""
    manifest = frozen.manifest
    current = {
        "sessions": frozen.sessions,
        "contracts": frozen.contracts,
        "quotes": frozen.quotes,
        "underlying": frozen.underlying,
        "activity": frozen.activity,
        "settlements": frozen.settlements,
        "rates": frozen.rates,
        "features": frozen.features,
        "coverage": frozen.coverage,
    }
    return FrozenDataset.freeze(
        **{**current, **tables},
        fidelity=manifest.fidelity,
        limitations=manifest.limitations,
        calendar_version=manifest.calendar_version,
        product_rules_version=manifest.product_rules_version,
        feature_versions=manifest.feature_versions,
        license_policy_id=manifest.license_policy_id,
    )


def with_features(
    frozen: FrozenDataset,
    values: Mapping[tuple[str, date], Decimal | None],
    *,
    available_at: Mapping[tuple[str, date], int] | None = None,
) -> FrozenDataset:
    """Return ``frozen`` with the given (feature id, session) values and availability instants."""
    late = available_at or {}
    rows = []
    for row in frozen.features:
        key = (row.feature_id, row.session_date)
        changed = row
        if key in values:
            value = values[key]
            reason = None if value is not None else "warmup"
            changed = replace(changed, value=value, missing_reason=reason)
        if key in late:
            changed = replace(changed, max_input_available_at_ns=late[key])
        rows.append(changed)
    return refrozen(frozen, features=tuple(rows))


def session(frozen: FrozenDataset, day: date) -> TradingSession:
    """Return the dataset's session of ``day``."""
    return next(row for row in frozen.sessions if row.session_date == day)


def deposited(cash: str = "10000.00", *, at_ns: int = 0) -> LedgerState:
    """Return the ledger state after one cash deposit."""
    entry = book_deposit(event_id="deposit", at_ns=at_ns, cash=Usd(Decimal(cash)))
    return apply_entry(LedgerState.empty(), entry)


def decision(  # noqa: PLR0913 — one keyword per context field a test varies
    frozen: FrozenDataset,
    day: date = MON,
    *,
    purpose: OrderPurpose = OrderPurpose.ENTRY,
    cash: str = "10000.00",
    state: LedgerState | None = None,
    campaign_id: str = "c1.g1",
) -> tuple[AsOfView, SelectionContext]:
    """Return the DEC view of ``day`` and a selection context over a deposited, flat account."""
    today = session(frozen, day)
    earlier = [s.session_date for s in frozen.sessions if s.session_date < day]
    following = next_session(frozen.sessions, day)
    assert following is not None, f"{day} needs a later session to settle on"
    ctx = SelectionContext(
        decision_id=f"{day.isoformat()}:DEC:5:1",
        session=today,
        prior_session=earlier[-1] if earlier else None,
        settles_on=following.session_date,
        purpose=purpose,
        campaign_id=campaign_id,
        state=deposited(cash, at_ns=today.open_ns) if state is None else state,
        schedule=SCHEDULE,
    )
    return AsOfView(frozen, slot_times(today).dec), ctx


def order_leg(right: OptionType, strike: str, ratio: int, expiry: date = EXPIRY) -> OrderLeg:
    """Return an SPXW order leg on version 1, expiring at the date's 16:00 close."""
    terms = SPXW_RULES.terms(Price(Decimal(strike)), right, local_ns(expiry, 16), expiry=expiry)
    return OrderLeg(terms, f"{terms.contract_id}@v1", ratio)


def put_leg(strike: str, ratio: int, expiry: date = EXPIRY) -> OrderLeg:
    """Return an SPXW put order leg."""
    return order_leg(OptionType.PUT, strike, ratio, expiry)


def call_leg(strike: str, ratio: int, expiry: date = EXPIRY) -> OrderLeg:
    """Return an SPXW call order leg."""
    return order_leg(OptionType.CALL, strike, ratio, expiry)


def quote(bid: str, ask: str) -> Quote:
    """Return a two-sided quote."""
    return Quote(Price(Decimal(bid)), Price(Decimal(ask)))


def usd(text: str) -> Usd:
    """Return a ``Usd`` amount."""
    return Usd(Decimal(text))


def flat_result(**changes: Any) -> SimulationResult:
    """Return a valid synthetic result that ends flat (G01's numbers), with ``changes``."""
    manifest_id = "a" * 64
    result = SimulationResult(
        calculation_status=CalculationStatus.VALID,
        data_fidelity=FidelityClass.SYNTHETIC_FIXTURE,
        limitations=(),
        execution_basis="synthetic_natural_package",
        calibration_status="uncalibrated",
        cost_basis="assumed_schedule",
        assignment_basis="not_applicable",
        window_requested=(MON, WED),
        window_simulated=(MON, WED),
        valuation_clock="scheduled_daily_v1",
        initial_equity_usd=usd("10000.00"),
        final_equity_usd=usd("10006.00"),
        open_positions=(),
        unsettled_cash=(),
        end_policy="liquidate_at_final_session",
        warnings=(
            RunWarning(
                WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL,
                MON,
                "synthetic fixture data; no historical performance is implied",
                (manifest_id,),
            ),
        ),
        invalid_reasons=(),
        headline_eligible=True,
        provenance=ResultProvenance(
            manifest_id=manifest_id,
            engine_version="0.1.0",
            policy_versions=(("fee_schedule", "f"), ("funding_policy", "z")),
            calendar_version="synthetic_weekdays_v1",
            product_rules_version="cboe_template_unverified_v1",
            feature_versions=(),
            license_policy_id="synthetic_public",
        ),
    )
    return replace(result, **changes)
