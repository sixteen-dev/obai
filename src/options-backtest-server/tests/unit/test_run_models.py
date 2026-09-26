"""The resolved run and its policy registries (ADR 0002 §6, §17 item 28)."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from selection_builders import document, strategy

from options_backtest.errors import ErrorCode, SpecRejected
from options_backtest.models.run import (
    ENGINE_VERSION,
    FEE_SCHEDULES,
    FLAT_FEE_SCHEDULE_ID,
    FUNDING_POLICIES,
    ZERO_INTEREST_FUNDING_ID,
    Policies,
    resolve,
    resolve_policies,
)
from options_backtest.money import Usd

MON = date(2024, 3, 4)
WED = date(2024, 3, 6)
MANIFEST_ID = "0123456789abcdef" * 4
SERVICE_DIR = Path(__file__).resolve().parents[2]


def _issues(error: SpecRejected) -> list[tuple[ErrorCode, str]]:
    return [(issue.code, issue.json_pointer) for issue in error.issues]


def test_the_registries_hold_the_two_illustrative_policies() -> None:
    schedule = FEE_SCHEDULES[FLAT_FEE_SCHEDULE_ID]

    assert set(FEE_SCHEDULES) == {"illustrative_flat_1usd_per_contract_side_v1"}
    assert set(FUNDING_POLICIES) == {"illustrative_zero_interest_no_borrow_v1"}
    assert schedule.schedule_id == FLAT_FEE_SCHEDULE_ID
    assert schedule.trade_per_contract == Usd(Decimal("1.00"))
    assert schedule.exercise_assignment_per_contract == Usd(Decimal(0))
    assert schedule.cash_settlement_per_contract == Usd(Decimal(0))


def test_the_engine_version_is_the_service_version_file() -> None:
    # Stamped into every result's provenance, so a VERSION bump must move it too (§17 item 28).
    assert (SERVICE_DIR / "VERSION").read_text(encoding="utf-8").strip() == ENGINE_VERSION


def test_resolve_policies_binds_the_strategy_ids() -> None:
    policies = resolve_policies(strategy().spec)

    assert policies == Policies(FEE_SCHEDULES[FLAT_FEE_SCHEDULE_ID], ZERO_INTEREST_FUNDING_ID)


def test_resolve_policies_rejects_every_unknown_id_at_its_pointer() -> None:
    spec = strategy(fee_schedule_id="broker_fees_v9", funding_policy_id="margin_v2").spec

    with pytest.raises(SpecRejected) as caught:
        resolve_policies(spec)

    assert _issues(caught.value) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/fee_schedule_id"),
        (ErrorCode.INVALID_STRATEGY_RULE, "/funding_policy_id"),
    ]
    assert "broker_fees_v9" in caught.value.issues[0].message


def test_resolve_policies_leaves_the_comparison_policy_to_wp4() -> None:
    spec = strategy(comparison_policy_id="anything_wp4_defines_v1").spec

    assert resolve_policies(spec).funding_policy_id == ZERO_INTEREST_FUNDING_ID


def test_resolve_binds_window_dataset_policies_and_engine_version() -> None:
    validated = strategy()

    run = resolve(validated, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID)

    assert run.strategy is validated
    assert (run.start_date, run.end_date, run.manifest_id) == (MON, WED, MANIFEST_ID)
    assert run.fee_schedule == FEE_SCHEDULES[FLAT_FEE_SCHEDULE_ID]
    assert run.policy_versions == (
        ("fee_schedule", FLAT_FEE_SCHEDULE_ID),
        ("funding_policy", ZERO_INTEREST_FUNDING_ID),
    )
    assert run.engine_version == ENGINE_VERSION


def test_resolve_accepts_a_one_session_window() -> None:
    run = resolve(strategy(), start_date=MON, end_date=MON, manifest_id=MANIFEST_ID)

    assert run.start_date == run.end_date == MON


def test_resolve_rejects_a_root_whose_underlying_is_not_the_product_underlying() -> None:
    spx_with_xsp = strategy(product={"allowed_option_roots": ["SPXW", "XSP"]})
    xsp_with_spxw = strategy(
        product={"underlying_symbol": "XSP", "allowed_option_roots": ["XSP", "SPXW"]}
    )

    with pytest.raises(SpecRejected) as spx:
        resolve(spx_with_xsp, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID)
    with pytest.raises(SpecRejected) as xsp:
        resolve(xsp_with_spxw, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID)

    assert _issues(spx.value) == [
        (ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/1")
    ]
    assert _issues(xsp.value) == [
        (ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/1")
    ]


def test_resolve_accepts_xsp_on_its_own_underlying() -> None:
    xsp = strategy(product={"underlying_symbol": "XSP", "allowed_option_roots": ["XSP"]})

    assert resolve(xsp, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID).strategy is xsp


def test_resolve_reports_policy_and_root_issues_together_sorted_by_pointer() -> None:
    validated = strategy(
        fee_schedule_id="broker_fees_v9", product={"allowed_option_roots": ["XSP"]}
    )

    with pytest.raises(SpecRejected) as caught:
        resolve(validated, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID)

    assert _issues(caught.value) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/fee_schedule_id"),
        (ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/0"),
    ]


def test_resolve_refuses_an_inverted_window() -> None:
    with pytest.raises(ValueError, match="start_date"):
        resolve(strategy(), start_date=WED, end_date=MON, manifest_id=MANIFEST_ID)


@pytest.mark.parametrize("manifest_id", ["A" * 64, "a" * 63, "g" * 64, ""])
def test_resolve_refuses_a_malformed_manifest_id(manifest_id: str) -> None:
    with pytest.raises(ValueError, match="manifest_id"):
        resolve(strategy(), start_date=MON, end_date=WED, manifest_id=manifest_id)


def test_resolve_refuses_arguments_of_the_wrong_type() -> None:
    validated = strategy()
    noon = datetime(2024, 3, 6, 12)

    with pytest.raises(TypeError, match="end_date"):
        resolve(validated, start_date=MON, end_date=noon, manifest_id=MANIFEST_ID)
    with pytest.raises(TypeError, match="strategy"):
        resolve(validated.spec, start_date=MON, end_date=WED, manifest_id=MANIFEST_ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="manifest_id"):
        resolve(validated, start_date=MON, end_date=WED, manifest_id=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="spec"):
        resolve_policies(document())  # type: ignore[arg-type]
