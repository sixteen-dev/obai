"""The restricted as-of view (ADR 0002 §2, §17 items 11-13, design §8.3)."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from data_builders import (
    EXPIRY,
    MON,
    SECOND_NS,
    TUE,
    activity_obs,
    contract_version,
    coverage,
    feature_obs,
    freeze,
    index_obs,
    local_ns,
    option_terms,
    quote_obs,
    rate_obs,
    settlement_obs,
    slot_ns,
    trading_session,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import CoverageState, QuoteStatus, UnderlyingField
from options_backtest.models.market import Deliverable, DeliverableComponent, OptionType
from options_backtest.money import ZERO_USD
from options_backtest.reference.rates import CMT_CURVE_ID, DiscountCurve, bill_df

SESSION_MON = trading_session(MON)
SESSION_TUE = trading_session(TUE)
MON_DEC = slot_ns(SESSION_MON, "DEC")
TUE_DEC = slot_ns(SESSION_TUE, "DEC")
TUE_OPEN = SESSION_TUE.open_ns
DAY_NS = 86_400 * SECOND_NS
QUOTE_AGE = 120 * SECOND_NS

PUT = option_terms(EXPIRY, OptionType.PUT, "4900")
CALL = option_terms(EXPIRY, OptionType.CALL, "5100")
SMALL = option_terms(EXPIRY, OptionType.PUT, "500")
LARGE = option_terms(EXPIRY, OptionType.PUT, "5000")
TODAY = option_terms(MON, OptionType.PUT, "4950")
LATER = option_terms(EXPIRY, OptionType.PUT, "4800")
XSP = option_terms(EXPIRY, OptionType.CALL, "451.5", root="XSP", underlying="XSP")
PUT_V2_TERMS = replace(
    PUT, deliverable=Deliverable("SPX:50", (DeliverableComponent("SPX", Decimal(50)),), ZERO_USD)
)
FEATURE = "SPX:underlying.return_20s"


def _contracts() -> tuple[object, ...]:
    return (
        contract_version(PUT, effective_to_ns=TUE_OPEN),
        contract_version(PUT_V2_TERMS, version=2, effective_from_ns=TUE_OPEN),
        contract_version(CALL, known_from_ns=local_ns(MON, 12)),
        contract_version(SMALL),
        contract_version(LARGE, effective_to_ns=TUE_OPEN),
        contract_version(TODAY),
        contract_version(LATER, listed_at_ns=TUE_OPEN),
        contract_version(XSP, root="XSP"),
    )


def _quotes() -> tuple[object, ...]:
    put, call = PUT.contract_id, CALL.contract_id
    return (
        quote_obs(put, SESSION_MON, "DEC"),
        quote_obs(put, SESSION_MON, "F1", "2.30", "2.20"),
        quote_obs(put, SESSION_MON, "F2", "2.10", "2.30", available_at_ns=local_ns(MON, 15, 48)),
        quote_obs(put, SESSION_MON, "CLOSE", "2.00", "2.10"),
        quote_obs(put, SESSION_TUE, "DEC", "1.80", "1.95"),
        quote_obs(call, SESSION_MON, "DEC", "1.00", "1.10"),
        replace(
            quote_obs(call, SESSION_MON, "DEC", "1.05", "1.15"),
            observation_id=f"q:{call}:2024-03-04:DEC-late",
            available_at_ns=MON_DEC + 30 * SECOND_NS,
        ),
    )


def _underlying() -> tuple[object, ...]:
    return (
        index_obs("SPX", SESSION_MON, "DEC", "5000"),
        index_obs("SPX", SESSION_MON, "CLOSE", "5001"),
        index_obs(
            "SPX",
            SESSION_MON,
            "CLOSE",
            "5001.25",
            field=UnderlyingField.OFFICIAL_CLOSE,
            available_at_ns=local_ns(MON, 17),
        ),
        index_obs("XSP", SESSION_MON, "DEC", "500"),
        index_obs("SPX", SESSION_TUE, "DEC", "4990"),
    )


def _dataset() -> FrozenDataset:
    put = PUT.contract_id
    return freeze(
        sessions=(SESSION_MON, SESSION_TUE),
        contracts=_contracts(),
        quotes=_quotes(),
        underlying=_underlying(),
        activity=(
            activity_obs(put, SESSION_MON, "F1", 12),
            activity_obs(put, SESSION_MON, "CLOSE", 30, available_at_ns=local_ns(MON, 16, 5)),
        ),
        settlements=(
            settlement_obs("SPX_PM", MON, "5001.25"),
            settlement_obs(
                "SPX_PM",
                MON,
                "5001.40",
                correction=1,
                final=False,
                available_at_ns=local_ns(MON, 18),
            ),
            settlement_obs("SPX_PM", MON, "5001.50", correction=2, available_at_ns=TUE_DEC),
        ),
        rates=(
            rate_obs(28, "0.05", date(2024, 3, 1), MON_DEC),
            rate_obs(91, "0.051", date(2024, 3, 1), MON_DEC),
            rate_obs(28, "0.049", MON, local_ns(MON, 15, 50)),
            rate_obs(28, "0.06", TUE, TUE_DEC),
        ),
        features=(
            feature_obs(FEATURE, MON, "0.0125", max_input_available_at_ns=SESSION_MON.close_ns),
            feature_obs(FEATURE, TUE, None, max_input_available_at_ns=SESSION_TUE.close_ns),
        ),
        coverage=(coverage(MON), coverage(TUE, CoverageState.GAP)),
    )


DATASET = _dataset()


def view(at_ns: int) -> AsOfView:
    return AsOfView(DATASET, at_ns)


@pytest.mark.parametrize(
    ("at_ns", "session_date"),
    [
        (SESSION_MON.open_ns, MON),
        (MON_DEC, MON),
        (SESSION_MON.cutoff_ns, MON),
        (TUE_OPEN, TUE),
    ],
)
def test_a_view_belongs_to_the_session_holding_its_instant(at_ns: int, session_date: date) -> None:
    assert view(at_ns).session.session_date == session_date


@pytest.mark.parametrize(
    "at_ns",
    [
        SESSION_MON.open_ns - 1,
        SESSION_MON.cutoff_ns + 1,
        TUE_OPEN - 1,
        SESSION_TUE.cutoff_ns + 1,
    ],
)
def test_a_view_outside_every_session_is_refused(at_ns: int) -> None:
    with pytest.raises(ValueError, match="session"):
        view(at_ns)


def test_a_view_needs_a_dataset_and_an_int_instant() -> None:
    with pytest.raises(TypeError):
        AsOfView(DATASET, float(MON_DEC))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AsOfView(DATASET.sessions, MON_DEC)  # type: ignore[arg-type]


def _quote_id(at_ns: int, contract_id: str, **kwargs: int | None) -> str | None:
    observation = view(at_ns).quote(contract_id, max_age_ns=QUOTE_AGE, **kwargs)
    return None if observation is None else observation.observation_id


PUT_Q = f"q:{PUT.contract_id}:2024-03-04"


@pytest.mark.parametrize(
    ("at_ns", "expected"),
    [
        (MON_DEC - 1, None),
        (MON_DEC, f"{PUT_Q}:DEC"),
        (slot_ns(SESSION_MON, "F1"), f"{PUT_Q}:F1"),
        (slot_ns(SESSION_MON, "F2"), f"{PUT_Q}:F1"),
        (local_ns(MON, 15, 47, 59), f"{PUT_Q}:F1"),
        (local_ns(MON, 15, 48), f"{PUT_Q}:F2"),
        (SESSION_MON.close_ns, f"{PUT_Q}:CLOSE"),
        (SESSION_MON.close_ns + QUOTE_AGE, f"{PUT_Q}:CLOSE"),
        (SESSION_MON.close_ns + QUOTE_AGE + SECOND_NS, None),
        (TUE_OPEN, None),
        (TUE_DEC, f"q:{PUT.contract_id}:2024-03-05:DEC"),
    ],
)
def test_quote_is_the_latest_visible_observation_of_the_session(
    at_ns: int, expected: str | None
) -> None:
    assert _quote_id(at_ns, PUT.contract_id) == expected


def test_an_invalid_latest_quote_is_returned_not_an_older_valid_one() -> None:
    observation = view(slot_ns(SESSION_MON, "F1")).quote(PUT.contract_id, max_age_ns=QUOTE_AGE)
    assert observation is not None
    assert observation.status() is QuoteStatus.CROSSED


@pytest.mark.parametrize(
    ("age_s", "suffix"),
    [(0, "DEC"), (29, "DEC"), (30, "DEC-late"), (120, "DEC-late"), (121, None)],
)
def test_quote_age_is_measured_from_observed_at_and_bound_inclusively(
    age_s: int, suffix: str | None
) -> None:
    expected = None if suffix is None else f"q:{CALL.contract_id}:2024-03-04:{suffix}"
    assert _quote_id(MON_DEC + age_s * SECOND_NS, CALL.contract_id) == expected


def test_quote_never_crosses_a_session() -> None:
    week = 7 * DAY_NS
    assert view(TUE_OPEN).quote(PUT.contract_id, max_age_ns=week) is None
    last_mon = view(SESSION_MON.cutoff_ns).quote(PUT.contract_id, max_age_ns=week)
    assert last_mon is not None
    assert last_mon.observation_id == f"{PUT_Q}:CLOSE"


def test_quote_ties_on_observed_at_break_by_available_at() -> None:
    assert _quote_id(MON_DEC, CALL.contract_id) == f"q:{CALL.contract_id}:2024-03-04:DEC"
    late = MON_DEC + 30 * SECOND_NS
    assert _quote_id(late, CALL.contract_id) == f"q:{CALL.contract_id}:2024-03-04:DEC-late"


def test_observed_after_is_strict() -> None:
    f1 = slot_ns(SESSION_MON, "F1")
    assert _quote_id(f1, PUT.contract_id, observed_after_ns=MON_DEC) == f"{PUT_Q}:F1"
    assert _quote_id(f1, PUT.contract_id, observed_after_ns=f1) is None
    assert _quote_id(MON_DEC, PUT.contract_id, observed_after_ns=MON_DEC) is None
    assert _quote_id(MON_DEC, PUT.contract_id, observed_after_ns=MON_DEC - 1) == f"{PUT_Q}:DEC"


def test_quote_of_an_unquoted_contract_is_none() -> None:
    assert _quote_id(MON_DEC, LARGE.contract_id) is None


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_age_ns": -1}, ValueError),
        ({"max_age_ns": 1.0}, TypeError),
        ({"max_age_ns": 1, "observed_after_ns": 1.0}, TypeError),
    ],
)
def test_quote_arguments_are_checked(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        view(MON_DEC).quote(PUT.contract_id, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("at_ns", "max_age_s", "expected"),
    [
        (MON_DEC, 60, "5000"),
        (MON_DEC + 60 * SECOND_NS, 60, "5000"),
        (MON_DEC + 61 * SECOND_NS, 60, None),
        (local_ns(MON, 17, 30), 86_400, "5001"),
        (local_ns(MON, 17, 30), 60, None),
        (TUE_OPEN, 86_400, None),
        (TUE_DEC, 60, "4990"),
    ],
)
def test_index_value_is_the_latest_fresh_index_value_never_the_official_close(
    at_ns: int, max_age_s: int, expected: str | None
) -> None:
    observation = view(at_ns).index_value("SPX", max_age_ns=max_age_s * SECOND_NS)
    assert (None if observation is None else str(observation.value.value)) == expected
    if observation is not None:
        assert observation.field is UnderlyingField.INDEX_VALUE


def test_index_value_is_per_underlying() -> None:
    observation = view(MON_DEC).index_value("XSP", max_age_ns=0)
    assert observation is not None
    assert observation.value.value == Decimal(500)
    with pytest.raises(ValueError):
        view(MON_DEC).index_value("SPX", max_age_ns=-1)


@pytest.mark.parametrize(
    ("at_ns", "volume"),
    [
        (MON_DEC, None),
        (slot_ns(SESSION_MON, "F1"), 12),
        (SESSION_MON.close_ns, 12),
        (local_ns(MON, 16, 5), 30),
        (TUE_DEC, None),
    ],
)
def test_activity_is_the_latest_visible_measurement_of_the_session(
    at_ns: int, volume: int | None
) -> None:
    observation = view(at_ns).activity(PUT.contract_id)
    assert (None if observation is None else observation.cumulative_volume) == volume


@pytest.mark.parametrize(
    ("at_ns", "value"),
    [
        (local_ns(MON, 16, 59, 59), None),
        (local_ns(MON, 17), "5001.25"),
        (local_ns(MON, 18, 30), "5001.25"),
        (TUE_DEC - 1, "5001.25"),
        (TUE_DEC, "5001.50"),
    ],
)
def test_settlement_is_the_highest_final_published_correction(
    at_ns: int, value: str | None
) -> None:
    observation = view(at_ns).settlement("SPX_PM", MON)
    assert (None if observation is None else str(observation.value.value)) == value


def test_settlement_of_another_series_or_date_is_none() -> None:
    at = view(TUE_DEC)
    assert at.settlement("XSP_PM", MON) is None
    assert at.settlement("SPX_PM", TUE) is None


def _points(at_ns: int) -> tuple[tuple[int, float], ...] | None:
    curve = view(at_ns).curve(CMT_CURVE_ID)
    return None if curve is None else curve.points


def test_curve_uses_only_rates_published_in_this_session() -> None:
    assert _points(MON_DEC - 1) is None
    assert _points(MON_DEC) == (
        (28, bill_df(Decimal("0.05"), 28)),
        (91, bill_df(Decimal("0.051"), 91)),
    )
    assert _points(local_ns(MON, 15, 50)) == (
        (28, bill_df(Decimal("0.049"), 28)),
        (91, bill_df(Decimal("0.051"), 91)),
    )
    assert _points(TUE_OPEN) is None
    assert _points(TUE_DEC) == ((28, bill_df(Decimal("0.06"), 28)),)
    assert view(MON_DEC).curve("OTHER") is None
    assert isinstance(view(MON_DEC).curve(CMT_CURVE_ID), DiscountCurve)


def test_feature_is_visible_once_its_inputs_are_available() -> None:
    assert view(MON_DEC).feature(FEATURE, MON) is None
    at_close = view(SESSION_MON.close_ns).feature(FEATURE, MON)
    assert at_close is not None
    assert at_close.value == Decimal("0.0125")
    assert view(TUE_DEC).feature(FEATURE, TUE) is None
    missing = view(SESSION_TUE.cutoff_ns).feature(FEATURE, TUE)
    assert missing is not None
    assert (missing.value, missing.missing_reason) == (None, "warmup")
    assert view(TUE_DEC).feature("SPX:underlying.close_to_sma_50s", MON) is None


def test_feature_with_two_versions_on_one_date_is_ambiguous() -> None:
    first = feature_obs(FEATURE, MON, "1", max_input_available_at_ns=1)
    dataset = freeze(sessions=(SESSION_MON,), features=(first, replace(first, feature_version="2")))
    with pytest.raises(ValueError, match="versions"):
        AsOfView(dataset, MON_DEC).feature(FEATURE, MON)


def test_coverage_is_metadata_visible_at_any_instant() -> None:
    at = view(SESSION_MON.open_ns)
    tue = at.coverage("quotes", TUE)
    assert tue is not None
    assert tue.status is CoverageState.GAP
    assert at.coverage("underlying", MON) is None


def _version_id(at_ns: int, contract_id: str) -> str | None:
    version = view(at_ns).contract(contract_id)
    return None if version is None else version.version_id


@pytest.mark.parametrize(
    ("at_ns", "contract_id", "expected"),
    [
        (MON_DEC, PUT.contract_id, f"{PUT.contract_id}@v1"),
        (TUE_OPEN - DAY_NS // 2, PUT.contract_id, f"{PUT.contract_id}@v1"),
        (TUE_OPEN, PUT.contract_id, f"{PUT.contract_id}@v2"),
        (local_ns(MON, 11, 59), CALL.contract_id, None),
        (local_ns(MON, 12), CALL.contract_id, f"{CALL.contract_id}@v1"),
        (MON_DEC, LARGE.contract_id, f"{LARGE.contract_id}@v1"),
        (TUE_DEC, LARGE.contract_id, None),
        (MON_DEC, "SPXW:2024-04-19:P:1", None),
    ],
)
def test_contract_is_the_known_and_effective_version(
    at_ns: int, contract_id: str, expected: str | None
) -> None:
    assert _version_id(at_ns, contract_id) == expected


def test_a_revised_version_carries_its_new_terms() -> None:
    version = view(TUE_DEC).contract(PUT.contract_id)
    assert version is not None
    assert version.terms.deliverable.components[0].units == Decimal(50)


def _listed(at_ns: int, root: str) -> list[str]:
    return [version.version_id for version in view(at_ns).listed(root)]


def test_listed_is_sorted_by_contract_id_not_version_id() -> None:
    assert _listed(MON_DEC, "SPXW") == [
        f"{TODAY.contract_id}@v1",
        f"{CALL.contract_id}@v1",
        f"{PUT.contract_id}@v1",
        f"{SMALL.contract_id}@v1",
        f"{LARGE.contract_id}@v1",
    ]


def test_listed_excludes_expired_unlisted_unknown_and_ended_versions() -> None:
    assert _listed(SESSION_MON.cutoff_ns, "SPXW") == [
        f"{CALL.contract_id}@v1",
        f"{PUT.contract_id}@v1",
        f"{SMALL.contract_id}@v1",
        f"{LARGE.contract_id}@v1",
    ]
    assert _listed(local_ns(MON, 10), "SPXW") == [
        f"{TODAY.contract_id}@v1",
        f"{PUT.contract_id}@v1",
        f"{SMALL.contract_id}@v1",
        f"{LARGE.contract_id}@v1",
    ]
    assert _listed(TUE_DEC, "SPXW") == [
        f"{CALL.contract_id}@v1",
        f"{LATER.contract_id}@v1",
        f"{PUT.contract_id}@v2",
        f"{SMALL.contract_id}@v1",
    ]


def test_listed_is_per_root() -> None:
    assert _listed(MON_DEC, "XSP") == [f"{XSP.contract_id}@v1"]
    assert _listed(MON_DEC, "SPX") == []


@pytest.mark.parametrize(
    "call",
    [
        lambda at: at.settlement("SPX_PM", datetime(2024, 3, 4)),
        lambda at: at.feature(FEATURE, "2024-03-04"),
        lambda at: at.coverage("quotes", datetime(2024, 3, 4)),
    ],
)
def test_date_arguments_must_be_exactly_dates(call: Callable[[AsOfView], object]) -> None:
    with pytest.raises(TypeError, match="exactly date"):
        call(view(MON_DEC))
