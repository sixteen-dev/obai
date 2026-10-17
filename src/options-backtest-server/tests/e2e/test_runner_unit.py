"""The e2e runner's own parsing and comparison (ADR 0002 §11, §17 items 23 and 44).

These tests use no engine code beyond WP1 ingestion and the T0 dataclasses, so they pass
before the simulator exists; ``test_goldens`` is what exercises the engine.
"""

import json
from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from options_backtest.data.records import CoverageState, UnderlyingField
from options_backtest.ingest import load_strategy
from options_backtest.models.strategy import FixedContracts, ProfitRule
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.money import Price
from options_backtest.reference.calendars import Slot
from options_backtest.synthetic.market import (
    ActivityPin,
    CoverageStatus,
    EarlyClose,
    LateAvailability,
    MarketSpec,
    QuoteDrop,
    QuotePin,
    QuoteStale,
    RateDrop,
    SettlementDrop,
    SettlementPin,
    TermsRevision,
    UnderlyingDrop,
)

from .runner import (
    DEFAULTS_PATH,
    SCENARIOS_DIR,
    STRATEGIES_DIR,
    Expected,
    Json,
    Observed,
    StrategyRef,
    build_market_spec,
    build_override,
    compare,
    json_bytes,
    load_scenario,
    merge_patch,
    parse_defaults,
    parse_scenario,
    read_json_tree,
    strategy_document,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "strategy.schema.json"
STRATEGY_PATHS = sorted(STRATEGIES_DIR.glob("*.json"))
SCENARIO_PATHS = sorted(SCENARIOS_DIR.glob("*.toml"))
DAY = date(2024, 3, 4)
FIVE_NEAREST_OFFSETS = (-2, -1, 0, 1, 2)  # in strike steps around the grid centre
ROOT_DIVISORS = {"SPXW": Decimal(1), "XSP": Decimal(10)}  # ADR 0002 §17 item 15


def _five_nearest_strikes(market: MarketSpec, divisor: Decimal) -> set[Decimal]:
    """Return the grid centre (§17 item 20) and its two neighbours on each side, in root points."""
    steps = (market.index_start / divisor / market.strike_step).quantize(0, ROUND_HALF_UP)
    centre = market.strike_step * steps
    return {centre + k * market.strike_step for k in FIVE_NEAREST_OFFSETS}


def _defaults() -> dict[str, object]:
    return dict(parse_defaults(DEFAULTS_PATH.read_text(encoding="utf-8")))


def _scenario_text(
    market: str = "", expected: str = "", warnings: str = '"SYNTHETIC_FIXTURE_NOT_HISTORICAL"'
) -> str:
    return f"""
id = "G99"
proves = ["unit"]
derived_by = "C1"
checked_by = ""
window = {{ start = "2024-03-04", end = "2024-03-06" }}
strategy = {{ file = "spxw_put_credit_vertical.json" }}
[market]
{market}
[expected]
calculation_status = "valid"
warning_codes = [{warnings}]
derivation = "unit"
{expected}
"""


# --- RFC 7396 merge patch and exact JSON --------------------------------------------------

# RFC 7396 Appendix A, verbatim (JSON null is None).
RFC_7396_CASES: tuple[tuple[Json, Json, Json], ...] = (
    ({"a": "b"}, {"a": "c"}, {"a": "c"}),
    ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
    ({"a": "b"}, {"a": None}, {}),
    ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
    ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
    ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
    ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
    ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
    (["a", "b"], ["c", "d"], ["c", "d"]),
    ({"a": "b"}, ["c"], ["c"]),
    ({"a": "foo"}, None, None),
    ({"a": "foo"}, "bar", "bar"),
    ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
    ([1, 2], {"a": "b", "c": None}, {"a": "b"}),
    ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
)


@pytest.mark.parametrize(("target", "patch", "result"), RFC_7396_CASES)
def test_merge_patch_matches_rfc_7396_appendix_a(target: Json, patch: Json, result: Json) -> None:
    assert merge_patch(target, patch) == result


def test_merge_patch_leaves_the_target_unchanged() -> None:
    target: Json = {"exits": {"take_profit": None, "exit_dte": 0}}
    merge_patch(target, {"exits": {"exit_dte": 5}})
    assert target == {"exits": {"take_profit": None, "exit_dte": 0}}


def test_json_bytes_writes_decimals_as_exact_json_numbers() -> None:
    tree: Json = {"a": Decimal("0.05"), "b": [1, True, None, "x"], "c": Decimal("1E+2")}
    assert json_bytes(tree) == b'{"a":0.05,"b":[1,true,null,"x"],"c":1E+2}'


@pytest.mark.parametrize("bad", [0.05, Decimal("NaN"), Decimal("Infinity"), date(2024, 3, 4)])
def test_json_bytes_rejects_floats_non_finite_decimals_and_dates(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        json_bytes({"a": bad})  # type: ignore[dict-item]


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', '{"a": NaN}', '{"a": {"b": 1, "b": 1}}'])
def test_read_json_tree_rejects_duplicate_keys_and_non_finite_numbers(text: str) -> None:
    with pytest.raises(ValueError, match="strategy.json"):
        read_json_tree(text, "strategy.json")


def test_read_json_tree_parses_fractions_as_decimal() -> None:
    assert read_json_tree('{"f": 0.10, "n": 3}', "x.json") == {"f": Decimal("0.10"), "n": 3}


# --- strategy files -----------------------------------------------------------------------


@pytest.mark.parametrize("path", STRATEGY_PATHS, ids=[p.stem for p in STRATEGY_PATHS])
def test_strategy_file_is_schema_valid_and_passes_wp1_checks(path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = read_json_tree(path.read_text(encoding="utf-8"), path.name)
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(document)]
    assert errors == []
    strategy = load_strategy(path.read_bytes())
    assert strategy.spec.exits.take_profit is None
    assert strategy.spec.exits.stop_loss is None


def test_strategy_files_declare_the_intended_directions() -> None:
    directions = {
        path.stem: load_strategy(path.read_bytes()).premium_direction for path in STRATEGY_PATHS
    }
    assert directions == {
        "spxw_call_debit_vertical": PremiumDirection.DEBIT,
        "spxw_put_credit_vertical": PremiumDirection.CREDIT,
        "spxw_put_credit_vertical_delta": PremiumDirection.CREDIT,
        "spxw_put_credit_vertical_iv_gate": PremiumDirection.CREDIT,
        "spxw_put_credit_vertical_risk_budget": PremiumDirection.CREDIT,
        "xsp_put_credit_vertical": PremiumDirection.CREDIT,
    }


def test_strategy_document_applies_the_merge_patch() -> None:
    patch: Json = {
        "exits": {"take_profit": {"basis": "initial_credit", "fraction": Decimal("0.05")}},
        "sizing": {"contracts": 2},
    }
    ref = StrategyRef(file="spxw_put_credit_vertical.json", patch=patch)
    spec = load_strategy(strategy_document(ref)).spec
    assert spec.exits.take_profit == ProfitRule(basis="initial_credit", fraction=Decimal("0.05"))
    assert isinstance(spec.sizing, FixedContracts)
    assert spec.sizing.contracts == 2


# --- market spec and overrides --------------------------------------------------------------


def test_defaults_state_every_market_spec_field_with_a_fixed_index_and_zero_rates() -> None:
    defaults = _defaults()
    assert set(defaults) == {field.name for field in fields(MarketSpec)}
    spec = build_market_spec(defaults)
    assert (spec.daily_drift, spec.daily_vol) == (Decimal(0), Decimal(0))
    assert all(bey == 0 for _, bey in spec.rates)
    assert spec.overrides == ()
    assert (spec.premium_multiplier, spec.deliverable_units) == (Decimal(100), Decimal(100))


@pytest.mark.parametrize(
    ("edit", "message"),
    [({"sigma_typo": "0.2"}, "sigma_typo"), ({"seed": True}, "seed"), ({"tick": 0.05}, "tick")],
)
def test_market_rejects_unknown_keys_and_wrong_types(edit: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        build_market_spec({**_defaults(), **edit})


def test_market_rejects_a_missing_field() -> None:
    table = _defaults()
    del table["sigma"]
    with pytest.raises(ValueError, match="sigma"):
        build_market_spec(table)


OVERRIDE_CASES: tuple[tuple[dict[str, object], object], ...] = (
    (
        {"kind": "quote_pin", "contract": "C", "session": "2024-03-04", "slots": ["DEC", "F1"],
         "bid": "2.00", "ask": "2.20", "bid_size": 50, "ask_size": 0},
        QuotePin("C", DAY, (Slot.DEC, Slot.F1), Decimal("2.00"), Decimal("2.20"), 50, 0),
    ),
    (
        {"kind": "quote_drop", "contract": "C", "session": DAY, "slots": ["F1"]},
        QuoteDrop("C", DAY, (Slot.F1,)),
    ),
    (
        {"kind": "quote_stale", "contract": "C", "session": "2024-03-04", "slots": ["DEC"],
         "seconds": 121},
        QuoteStale("C", DAY, (Slot.DEC,), 121),
    ),
    (
        {"kind": "late_availability", "selector": "s:SPX_PM:2024-03-15:c0",
         "available_at": datetime(2024, 3, 16, 12, 0, tzinfo=UTC)},
        LateAvailability("s:SPX_PM:2024-03-15:c0", datetime(2024, 3, 16, 12, 0, tzinfo=UTC)),
    ),
    (
        {"kind": "underlying_drop", "underlying_id": "SPX", "field": "index_value",
         "session": "2024-03-04", "slots": ["DEC"]},
        UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, DAY, (Slot.DEC,)),
    ),
    (
        {"kind": "settlement_pin", "series": "SPX_PM", "session": "2024-03-04", "value": "4897"},
        SettlementPin("SPX_PM", DAY, Price(Decimal("4897"))),
    ),
    (
        {"kind": "settlement_drop", "series": "SPX_PM", "session": "2024-03-04"},
        SettlementDrop("SPX_PM", DAY),
    ),
    (
        {"kind": "rate_drop", "session": "2024-03-04", "tenor_days": 28},
        RateDrop(DAY, 28),
    ),
    ({"kind": "rate_drop", "session": "2024-03-04"}, RateDrop(DAY, None)),
    (
        {"kind": "activity_pin", "contract": "C", "session": "2024-03-04", "slot": "F1",
         "cumulative_volume": 7},
        ActivityPin("C", DAY, Slot.F1, 7),
    ),
    (
        {"kind": "terms_revision", "contract": "C", "session": "2024-03-04",
         "deliverable_units": "50"},
        TermsRevision("C", DAY, Decimal("50")),
    ),
    (
        {"kind": "coverage_status", "table": "quotes", "session": "2024-03-04",
         "status": "gap", "note": "feed outage"},
        CoverageStatus("quotes", DAY, CoverageState.GAP, "feed outage"),
    ),
    ({"kind": "early_close", "session": "2024-03-04"}, EarlyClose(DAY)),
)  # fmt: skip


@pytest.mark.parametrize(("table", "override"), OVERRIDE_CASES)
def test_build_override_constructs_the_snake_case_kind(
    table: dict[str, object], override: object
) -> None:
    assert build_override(table) == override


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"kind": "quote_pinned", "contract": "C"}, "quote_pinned"),
        ({"kind": "quote_drop", "contract": "C", "session": "2024-03-04"}, "slots"),
        ({"kind": "early_close", "session": "2024-03-04", "slot": "DEC"}, "slot"),
        ({"kind": "quote_drop", "contract": "C", "session": "2024-03-04", "slots": ["D"]}, "D"),
        ({"kind": "quote_stale", "contract": "C", "session": "2024-03-04", "slots": ["DEC"],
          "seconds": True}, "seconds"),
        ({"kind": "late_availability", "selector": "q",
          "available_at": datetime(2024, 3, 16, 12, 0)}, "available_at"),
    ],
)  # fmt: skip
def test_build_override_fails_loudly(table: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        build_override(table)


# --- scenario files -------------------------------------------------------------------------


def test_parse_scenario_merges_market_over_defaults_and_keeps_override_order() -> None:
    market = """seed = 7
[[market.overrides]]
kind = "early_close"
session = "2024-03-05"
[[market.overrides]]
kind = "settlement_drop"
series = "SPX_PM"
session = "2024-03-15"
"""
    scenario = parse_scenario(_scenario_text(market=market), defaults=_defaults())
    expected_market = build_market_spec(_defaults())
    assert scenario.market.seed == 7
    assert scenario.market.sigma == expected_market.sigma
    assert scenario.market.overrides == (
        EarlyClose(date(2024, 3, 5)),
        SettlementDrop("SPX_PM", date(2024, 3, 15)),
    )
    assert (scenario.start, scenario.end) == (DAY, date(2024, 3, 6))


def test_parse_scenario_defaults_exhaustive_lists_to_empty_and_leaves_campaigns_unstated() -> None:
    expected = parse_scenario(_scenario_text(), defaults=_defaults()).expected
    assert expected.rows == {"fills": (), "nonfills": (), "settlements": ()}
    assert expected.account == {}


@pytest.mark.parametrize(
    ("market", "expected", "message"),
    [
        ("", "surprise = 1", "surprise"),
        ("", '[[expected.fills]]\nslot = "F1"\nprice = "2.00"', "price"),
        ("", '[expected.account."2024-03-05"]\ncash = "1"\nnlv = "1"', "nlv"),
        ('sigmaa = "0.2"', "", "sigmaa"),
        ("", 'final_equity_usd = "10,006"', "final_equity_usd"),
        ("", "headline_eligible = 1", "headline_eligible"),
    ],
)
def test_parse_scenario_rejects_unknown_keys_and_malformed_values(
    market: str, expected: str, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        parse_scenario(_scenario_text(market=market, expected=expected), defaults=_defaults())


# --- comparison semantics (§17 item 44) ------------------------------------------------------


WARNINGS = '"SYNTHETIC_FIXTURE_NOT_HISTORICAL", "EXIT_DEFERRED"'


def _observed(**rows: tuple[dict[str, object], ...]) -> Observed:
    scalars: dict[str, object] = {
        "calculation_status": "valid",
        "warning_codes": ("SYNTHETIC_FIXTURE_NOT_HISTORICAL", "EXIT_DEFERRED"),
        "headline_eligible": True,
        "final_equity_usd": Decimal("10006.00"),
        "open_positions": (),
        "retired_contracts": (),
    }
    fill = {"session": DAY, "slot": "F1", "net_debit": Decimal("-90.00"), "fees": Decimal("2.00")}
    base: dict[str, tuple[dict[str, object], ...]] = {
        "fills": (fill,),
        "nonfills": (),
        "settlements": (),
        "campaigns": ({"campaign_id": "c1", "rolls": 0},),
    }
    account = {DAY: {"cash": Decimal("10000.00"), "headroom": Decimal("9496.00")}}
    return Observed(scalars=scalars, rows={**base, **rows}, account=account)


def _expected(text: str, warnings: str = WARNINGS) -> Expected:
    scenario_text = _scenario_text(expected=text, warnings=warnings)
    return parse_scenario(scenario_text, defaults=_defaults()).expected


FILL_ROW = '[[expected.fills]]\nsession = "2024-03-04"\nnet_debit = "-90"'


def test_compare_numbers_by_decimal_value_and_rows_by_stated_keys_only() -> None:
    expected = _expected(f'final_equity_usd = "10006"\n{FILL_ROW}')
    assert compare(expected, _observed()) == ()


def test_compare_requires_the_whole_warning_sequence_in_order() -> None:
    reversed_order = '"EXIT_DEFERRED", "SYNTHETIC_FIXTURE_NOT_HISTORICAL"'
    assert compare(_expected(FILL_ROW, warnings=reversed_order), _observed()) == (
        "warning_codes: expected ('EXIT_DEFERRED', 'SYNTHETIC_FIXTURE_NOT_HISTORICAL'), got "
        "('SYNTHETIC_FIXTURE_NOT_HISTORICAL', 'EXIT_DEFERRED')",
    )
    first_only = '"SYNTHETIC_FIXTURE_NOT_HISTORICAL"'
    assert len(compare(_expected(FILL_ROW, warnings=first_only), _observed())) == 1


def test_compare_reports_a_wrong_value_with_its_location() -> None:
    expected = _expected('[[expected.fills]]\nfees = "3.00"')
    mismatches = compare(expected, _observed(fills=({"fees": Decimal("2.00")},)))
    assert mismatches[-1] == "fills[0].fees: expected Decimal('3.00'), got Decimal('2.00')"


def test_compare_treats_fills_nonfills_and_settlements_as_exhaustive() -> None:
    two_fills = _observed(fills=({"slot": "F1"}, {"slot": "F2"}))
    assert "fills: expected 1 rows, got 2" in compare(_expected(FILL_ROW), two_fills)
    one_nonfill = _observed(nonfills=({"reason": "LIMIT"},))
    assert "nonfills: expected 0 rows, got 1" in compare(_expected(FILL_ROW), one_nonfill)


def test_compare_checks_account_rows_only_for_listed_dates() -> None:
    listed = _expected(f'{FILL_ROW}\n[expected.account."2024-03-04"]\ncash = "10000"')
    assert "account" not in " ".join(compare(listed, _observed()))
    missing = _expected(f'{FILL_ROW}\n[expected.account."2024-03-05"]\ncash = "10088"')
    assert "account[2024-03-05]: no account point" in compare(missing, _observed())


def test_compare_checks_campaigns_only_when_stated_and_then_exhaustively() -> None:
    assert not any("campaign" in m for m in compare(_expected(FILL_ROW), _observed()))
    stated = _expected(f"{FILL_ROW}\n[[expected.campaigns]]\nrolls = 1")
    assert "campaigns[0].rolls: expected 1, got 0" in compare(stated, _observed())


# --- the golden scenarios themselves -----------------------------------------------------------


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=[p.stem for p in SCENARIO_PATHS])
def test_scenario_file_loads_and_its_strategy_passes_wp1(path: Path) -> None:
    scenario = load_scenario(path)
    strategy = load_strategy(strategy_document(scenario.strategy))
    assert scenario.derived_by in {"C1", "C2"}
    assert scenario.expected.derivation.strip()
    sizing = strategy.spec.sizing
    cap = sizing.contracts if isinstance(sizing, FixedContracts) else sizing.max_contracts
    assert cap <= 3


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=[p.stem for p in SCENARIO_PATHS])
def test_scenario_follows_the_hand_derivation_rules(path: Path) -> None:
    market = load_scenario(path).market
    assert (market.daily_drift, market.daily_vol) == (Decimal(0), Decimal(0))
    assert all(bey == 0 for _, bey in market.rates)
    assert market.premium_multiplier == Decimal(100)
    for override in market.overrides:
        if isinstance(override, (QuotePin, QuoteDrop, QuoteStale)):
            root, _, _, strike = override.contract.split(":")
            nearest = _five_nearest_strikes(market, ROOT_DIVISORS[root])
            assert Decimal(strike) not in nearest, override
        if isinstance(override, QuotePin):
            assert override.bid % market.tick == 0, override
            assert override.ask % market.tick == 0, override
