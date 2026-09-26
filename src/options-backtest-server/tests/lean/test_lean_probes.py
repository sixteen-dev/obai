"""LEAN probes, run before any differential scenario (ADR 0002 §12 step 3, §16 decision 3).

Each probe runs ``probes/ProbeAlgorithm.cs`` natively on the export of a synthetic SPXW market on
real 2024 non-holiday dates (zero drift and volatility: SPX is 5000 at every print) and asserts a
fact of §12 that the differential relies on; a mismatch fails the suite.

- echo: every fill and 16:00 mark LEAN reports equals the exported observation (sells at the
  bid, buys at the ask, marks at the bid/ask mid, SPX at its print); combo market orders fill in
  the submitting slice; option cash settles immediately (no unsettled cash).
- expiry: a cash-settled SPXW package held through expiry settles at the official value the
  export puts in the expiry day's 16:00 SPX bar (M3): each leg pays its intrinsic value there,
  an out-of-the-money leg nothing (M4), and exercise costs no fee.
- combo-limit: ``ComboLimitFill`` is strict (M6): a combo limit exactly at the natural package
  price never fills; one tick through it fills in the slice after the submission.
"""

import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from options_backtest.data.manifest import FrozenDataset
from options_backtest.money import Price
from options_backtest.reference.calendars import QUOTE_SLOTS, Slot, slot_instant
from options_backtest.synthetic.market import (
    MarketSpec,
    Override,
    QuotePin,
    SettlementPin,
    generate,
)

from .export import export
from .runner import JsonRecord, LeanRun, LeanToolchain, build_algorithm, preflight, run_algorithm

pytestmark = pytest.mark.lean

PROBES_DIR: Final = Path(__file__).parent / "probes"
TYPE_NAME: Final = "ProbeAlgorithm"
CASH: Final = Decimal(100_000)
MULTIPLIER: Final = Decimal(100)
FEE_PER_CONTRACT: Final = Decimal(1)
NEW_YORK: Final = ZoneInfo("America/New_York")
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
MONDAY: Final = date(2024, 3, 4)
TUESDAY: Final = date(2024, 3, 5)
FRIDAY: Final = date(2024, 3, 8)
NEXT_MONDAY: Final = date(2024, 3, 11)

type Legs = tuple[tuple[str, int], ...]


@pytest.fixture(scope="module")
def toolchain() -> LeanToolchain:
    return preflight(os.environ)


@pytest.fixture(scope="module")
def probe_dll(toolchain: LeanToolchain, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_algorithm(toolchain, PROBES_DIR, tmp_path_factory.mktemp("probe_build"))


def _market(last: date, weekly_dte: int, overrides: tuple[Override, ...]) -> FrozenDataset:
    """Return SPXW from 2024-03-04 to ``last``: SPX 5000, strikes 4990-5010, zero rates."""
    return generate(
        MarketSpec(
            seed=1,
            first_session=MONDAY,
            last_session=last,
            holidays=(),
            early_closes=(),
            index_start=Decimal(5000),
            daily_drift=Decimal(0),
            daily_vol=Decimal(0),
            sigma=Decimal("0.18"),
            rates=((28, Decimal(0)),),
            roots=("SPXW",),
            weekly_dtes=(weekly_dte,),
            strike_step=Decimal(5),
            strikes_each_side=2,
            tick=Decimal("0.05"),
            half_spread_abs=Decimal("0.05"),
            half_spread_rel=Decimal("0.02"),
            bid_size=50,
            ask_size=50,
            premium_multiplier=MULTIPLIER,
            deliverable_units=Decimal(100),
            overrides=overrides,
        )
    )


def _at(dataset: FrozenDataset, day: date, slot: Slot) -> str:
    """Return a slot's New York wall-clock stamp, the probe's time format."""
    session = next(s for s in dataset.sessions if s.session_date == day)
    instant = EPOCH + timedelta(microseconds=slot_instant(session, slot) // 1000)
    return instant.astimezone(NEW_YORK).strftime("%Y-%m-%dT%H:%M:%S")


def _combo(at: str, tag: str, legs: Legs, quantity: int, limit: str | None = None) -> JsonRecord:
    action: JsonRecord = {
        "at": at,
        "kind": "combo_market" if limit is None else "combo_limit",
        "tag": tag,
        "legs": [list(leg) for leg in legs],
        "quantity": quantity,
    }
    if limit is not None:
        action["limit"] = limit
    return action


def _probe(
    toolchain: LeanToolchain,
    dll: Path,
    dataset: FrozenDataset,
    actions: Sequence[JsonRecord],
    workspace: Path,
) -> LeanRun:
    """Run the probe over the dataset's window on its export, subscribing every traded leg."""
    contracts = sorted({leg[0] for action in actions for leg in action.get("legs", [])})
    algorithm_input = {
        "start_date": dataset.sessions[0].session_date.isoformat(),
        "end_date": dataset.sessions[-1].session_date.isoformat(),
        "cash": str(CASH),
        "contracts": contracts,
        "actions": list(actions),
    }
    run = run_algorithm(
        toolchain,
        dll,
        type_name=TYPE_NAME,
        archives=export(dataset),
        algorithm_input=algorithm_input,
        workspace=workspace,
    )
    assert run.records[-1]["unexecuted"] == []
    return run


def _events(run: LeanRun, status: str) -> list[JsonRecord]:
    return [r for r in run.records if r["kind"] == "order_event" and r["status"] == status]


def _quote_sides(dataset: FrozenDataset) -> Mapping[str, tuple[Decimal, Decimal]]:
    return {quote.observation_id: (quote.bid, quote.ask) for quote in dataset.quotes}


def _assert_close_marks(dataset: FrozenDataset, snapshot: JsonRecord) -> None:
    """Assert a 16:00 snapshot: SPX at its CLOSE print, each contract at its CLOSE quote's mid."""
    day = snapshot["tag"]
    prints = {row.observation_id: row.value.value for row in dataset.underlying}
    sides = _quote_sides(dataset)
    assert snapshot["time"] == _at(dataset, date.fromisoformat(day), Slot.CLOSE)
    assert Decimal(snapshot["index_price"]) == prints[f"u:SPX:index_value:{day}:CLOSE"]
    assert Decimal(snapshot["unsettled_cash"]) == 0
    for holding in snapshot["holdings"]:
        bid, ask = sides[f"q:{holding['contract']}:{day}:CLOSE"]
        assert (Decimal(holding["bid"]), Decimal(holding["ask"])) == (bid, ask)
        assert Decimal(holding["price"]) == (bid + ask) / 2


def _trade_cash(fills: Sequence[JsonRecord]) -> Decimal:
    """Return the cash the fills moved: −Σ quantity × price × multiplier − Σ fees."""
    premium = sum(
        (Decimal(f["fill_quantity"]) * Decimal(f["fill_price"]) * MULTIPLIER for f in fills),
        Decimal(0),
    )
    fees = sum((Decimal(f["fee"]) for f in fills), Decimal(0))
    return -premium - fees


def test_echo_probe_every_fill_and_close_mark_equals_the_export(
    toolchain: LeanToolchain, probe_dll: Path, tmp_path: Path
) -> None:
    dataset = _market(date(2024, 3, 6), 11, ())
    expiry = "SPXW:2024-03-15"
    legs: Legs = ((f"{expiry}:P:4995", -1), (f"{expiry}:P:4990", 1), (f"{expiry}:C:5005", -1))
    closing: Legs = tuple((contract, -ratio) for contract, ratio in legs)
    orders = {"open": (MONDAY, Slot.F1, legs), "close": (TUESDAY, Slot.F2, closing)}
    actions = [
        _combo(_at(dataset, MONDAY, Slot.F1), "open", legs, 2),
        {"at": _at(dataset, MONDAY, Slot.CLOSE), "kind": "snapshot", "tag": "2024-03-04"},
        _combo(_at(dataset, TUESDAY, Slot.F2), "close", closing, 2),
        {"at": _at(dataset, TUESDAY, Slot.CLOSE), "kind": "snapshot", "tag": "2024-03-05"},
    ]

    run = _probe(toolchain, probe_dll, dataset, actions, tmp_path)

    assert re.fullmatch(r"[0-9a-f]{40}", run.lean_commit)
    assert run.sdk_version
    assert [path for path, _ in run.export_digests] == [a.path for a in export(dataset)]
    sides = _quote_sides(dataset)
    fills = _events(run, "Filled")
    assert len(fills) == 6
    for fill in fills:
        day, slot, order_legs = orders[fill["tag"]]
        ratio = dict(order_legs)[fill["contract"]]
        bid, ask = sides[f"q:{fill['contract']}:{day.isoformat()}:{slot.value}"]
        assert fill["time"] == _at(dataset, day, slot)
        assert Decimal(fill["fill_quantity"]) == 2 * ratio
        assert Decimal(fill["fill_price"]) == (bid if ratio < 0 else ask)
        assert Decimal(fill["fee"]) == 2 * abs(ratio) * FEE_PER_CONTRACT
    snapshots = [r for r in run.records if r["kind"] == "snapshot"]
    assert [s["tag"] for s in snapshots] == ["2024-03-04", "2024-03-05"]
    for snapshot in snapshots:
        _assert_close_marks(dataset, snapshot)
    held = {h["contract"]: Decimal(h["quantity"]) for h in snapshots[0]["holdings"]}
    assert held == {contract: Decimal(2 * ratio) for contract, ratio in legs}
    assert Decimal(run.records[-1]["cash"]) == CASH + _trade_cash(fills)


def test_expiry_probe_a_cash_settled_package_settles_at_the_official_value(
    toolchain: LeanToolchain, probe_dll: Path, tmp_path: Path
) -> None:
    settlement = Decimal("4997.50")
    dataset = _market(NEXT_MONDAY, 4, (SettlementPin("SPX_PM", FRIDAY, Price(settlement)),))
    expiry = "SPXW:2024-03-08"
    short_put, long_put, long_call = f"{expiry}:P:5000", f"{expiry}:P:4995", f"{expiry}:C:4990"
    legs: Legs = ((short_put, -1), (long_put, 1), (long_call, 1))
    actions = [
        _combo(_at(dataset, MONDAY, Slot.F1), "open", legs, 1),
        {"at": _at(dataset, FRIDAY, Slot.CLOSE), "kind": "snapshot", "tag": "expiry close"},
    ]

    run = _probe(toolchain, probe_dll, dataset, actions, tmp_path)

    fills = _events(run, "Filled")
    trades = [f for f in fills if f["order_type"] != "OptionExercise"]
    exercises = [f for f in fills if f["order_type"] == "OptionExercise"]
    assert [f["tag"] for f in trades] == ["open"] * 3
    closing = next(r for r in run.records if r["kind"] == "snapshot")
    assert Decimal(closing["index_price"]) == settlement
    assert {Decimal(f["index_price"]) for f in exercises} == {settlement}
    intrinsic = -(Decimal(5000) - settlement) + (settlement - Decimal(4990))  # 4995 P expires OTM
    end = run.records[-1]
    assert all(Decimal(h["quantity"]) == 0 for h in end["holdings"])
    assert all(Decimal(f["fee"]) == 0 for f in exercises)
    assert Decimal(end["unsettled_cash"]) == 0
    assert Decimal(end["cash"]) == CASH + _trade_cash(trades) + intrinsic * MULTIPLIER


def test_combo_limit_probe_is_strict_at_an_exact_limit(
    toolchain: LeanToolchain, probe_dll: Path, tmp_path: Path
) -> None:
    short_put, long_put = "SPXW:2024-03-15:P:5000", "SPXW:2024-03-15:P:4995"
    pins: tuple[Override, ...] = tuple(
        QuotePin(contract, day, QUOTE_SLOTS, bid, ask, 50, 50)
        for day in (MONDAY, TUESDAY)
        for contract, bid, ask in (
            (short_put, Decimal("2.00"), Decimal("2.20")),
            (long_put, Decimal("1.00"), Decimal("1.10")),
        )
    )
    dataset = _market(date(2024, 3, 6), 11, pins)
    legs: Legs = ((short_put, -1), (long_put, 1))  # natural: −2.00 + 1.10 = −0.90 per package
    actions = [
        _combo(_at(dataset, MONDAY, Slot.DEC), "exact", legs, 1, limit="-0.90"),
        {"at": _at(dataset, MONDAY, Slot.F3), "kind": "cancel", "tag": "exact"},
        _combo(_at(dataset, TUESDAY, Slot.DEC), "through", legs, 1, limit="-0.85"),
        {"at": _at(dataset, TUESDAY, Slot.F3), "kind": "cancel", "tag": "through"},
    ]

    run = _probe(toolchain, probe_dll, dataset, actions, tmp_path)

    fills = _events(run, "Filled")
    assert [f["tag"] for f in fills] == ["through", "through"]
    assert {f["time"] for f in fills} == {_at(dataset, TUESDAY, Slot.F1)}
    prices = {f["contract"]: Decimal(f["fill_price"]) for f in fills}
    assert prices == {short_put: Decimal("2.00"), long_put: Decimal("1.10")}
    cancelled = {f["contract"] for f in _events(run, "Canceled") if f["tag"] == "exact"}
    assert cancelled == {short_put, long_put}
