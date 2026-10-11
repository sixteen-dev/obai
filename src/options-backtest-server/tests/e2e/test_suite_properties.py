"""Suite-level properties of the R1 simulation (ADR 0002 §7, §11, §17 item 41; C03, C04, C34).

(b) Every golden scenario, run exactly as ``test_goldens`` runs it, is also a behaviour of
``R1Campaign``: the trace checker (``tests/conformance/r1_trace.py``) finds no violation in any of
them, and together they reach every canary of the R1Campaign TLC configs.

C03, point in time: for an instant t, every record observed or available after t is changed
(values moved, availability one second later, so ``available_at >= observed_at`` still holds);
the events and candidate decisions at or before t stay byte-identical. The instants cover every
table a decision reads: quotes, index values and settlements (G02), features (G22) and volume
(G01 behind a volume minimum).

C04, same-session data: a volume observation measured through the session's CLOSE does not
satisfy a volume minimum at its DEC (the same observation at DEC does), and dropping every
17:00 official close changes neither the features nor the events.

C34, replay: the same rows in shuffled order freeze to the same ``manifest_id`` and run to the
same artifact digests, and replaying the journal reproduces the last event's account and holdings.

Determinism: a rerun in-process and runs in subprocesses under two other ``PYTHONHASHSEED``
values give identical artifact digests.
"""

import json
import os
import random
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
from conformance.r1_trace import CANARIES, Report, check, model_of, trace_of

from options_backtest.data.manifest import TABLE_NAMES, FrozenDataset, canonical_json
from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    FeatureObservation,
    QuoteObservation,
    RateObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingField,
    UnderlyingObservation,
)
from options_backtest.engine.journal import replay
from options_backtest.engine.simulator import run
from options_backtest.ingest import load_strategy
from options_backtest.models.artifacts import ArtifactBundle, SimEvent, SimEventKind
from options_backtest.models.ledger import AccountKind, LedgerState
from options_backtest.models.run import resolve
from options_backtest.money import Price
from options_backtest.reference.calendars import Slot
from options_backtest.synthetic.market import ActivityPin, MarketSpec, UnderlyingDrop, generate

from .runner import (
    SCENARIOS_DIR,
    Json,
    Scenario,
    StrategyRef,
    load_scenario,
    run_scenario,
    stored_dataset,
    strategy_document,
)

SCENARIO_PATHS: Final = sorted(SCENARIOS_DIR.glob("*.toml"))
TESTS_DIR: Final = Path(__file__).resolve().parents[1]
SERVICE_DIR: Final = TESTS_DIR.parent
SECOND_NS: Final = 10**9
MINUTE_NS: Final = 60 * SECOND_NS
BEFORE_CLOSE_MIN: Final = {Slot.DEC: 15, Slot.F1: 14, Slot.F2: 13, Slot.F3: 12, Slot.CLOSE: 0}
"""Minutes before the session's close of each intraday slot (ADR 0002 §7)."""
SUBPROCESS_TIMEOUT_S: Final = 600
DIGESTS_SCRIPT: Final = """
import json
import sys
import tempfile
from pathlib import Path

from e2e.runner import SCENARIOS_DIR, load_scenario, run_scenario

with tempfile.TemporaryDirectory() as directory:
    bundle = run_scenario(load_scenario(SCENARIOS_DIR / sys.argv[1]), Path(directory))
print(json.dumps([list(pair) for pair in bundle.digests]))
"""


@dataclass(frozen=True, slots=True)
class Tables:
    """A dataset's nine tables, in ``TABLE_NAMES`` order, to refreeze after an edit."""

    sessions: tuple[TradingSession, ...]
    contracts: tuple[ContractVersion, ...]
    quotes: tuple[QuoteObservation, ...]
    underlying: tuple[UnderlyingObservation, ...]
    activity: tuple[ActivityObservation, ...]
    settlements: tuple[SettlementObservation, ...]
    rates: tuple[RateObservation, ...]
    features: tuple[FeatureObservation, ...]
    coverage: tuple[CoveragePartition, ...]


def tables_of(dataset: FrozenDataset) -> Tables:
    """Return the dataset's tables."""
    return Tables(*(getattr(dataset, name) for name in TABLE_NAMES))


def refreeze(dataset: FrozenDataset, tables: Tables) -> FrozenDataset:
    """Freeze ``tables`` under ``dataset``'s manifest versions, fidelity and license."""
    manifest = dataset.manifest
    return FrozenDataset.freeze(
        sessions=tables.sessions,
        contracts=tables.contracts,
        quotes=tables.quotes,
        underlying=tables.underlying,
        activity=tables.activity,
        settlements=tables.settlements,
        rates=tables.rates,
        features=tables.features,
        coverage=tables.coverage,
        fidelity=manifest.fidelity,
        limitations=manifest.limitations,
        calendar_version=manifest.calendar_version,
        product_rules_version=manifest.product_rules_version,
        feature_versions=manifest.feature_versions,
        license_policy_id=manifest.license_policy_id,
    )


def run_on(document: bytes, dataset: FrozenDataset, start: date, end: date) -> ArtifactBundle:
    """Run a strategy document over ``[start, end]`` of an in-memory dataset."""
    strategy = load_strategy(document)
    resolved = resolve(
        strategy, start_date=start, end_date=end, manifest_id=dataset.manifest.manifest_id
    )
    return run(resolved, dataset)


def run_scenario_on(scenario: Scenario, dataset: FrozenDataset) -> ArtifactBundle:
    """Run a scenario's strategy and window on ``dataset`` instead of its own market."""
    return run_on(strategy_document(scenario.strategy), dataset, scenario.start, scenario.end)


def patched(ref: StrategyRef, extra: dict[str, Json]) -> bytes:
    """Return ``ref``'s strategy document with ``extra``'s top-level keys added to its patch."""
    base = ref.patch
    assert isinstance(base, dict), base
    return strategy_document(StrategyRef(file=ref.file, patch={**base, **extra}))


def scenario_named(stem_prefix: str) -> Scenario:
    """Return the one scenario whose file stem starts with ``stem_prefix``."""
    paths = [path for path in SCENARIO_PATHS if path.stem.startswith(stem_prefix)]
    assert len(paths) == 1, (stem_prefix, paths)
    return load_scenario(paths[0])


def slot_instant(dataset: FrozenDataset, session: date, slot: Slot) -> int:
    """Return a slot's instant from the session table (ADR 0002 §7: DEC = close - 15 min ...)."""
    row = next(row for row in dataset.sessions if row.session_date == session)
    if slot is Slot.CUT:
        return row.cutoff_ns
    return row.close_ns - BEFORE_CLOSE_MIN[slot] * MINUTE_NS


def events_through(events: Sequence[SimEvent], instant: int) -> bytes:
    """Return the canonical JSON of the events at or before ``instant``."""
    return canonical_json(tuple(event for event in events if event.at_ns <= instant))


# --- (b) the golden suite through the R1Campaign trace checker -------------------------------


def check_golden(path: Path, directory: Path) -> Report:
    """Run one golden as ``test_goldens`` does and check its trace against R1Campaign.

    The model reads the dataset the run read, so the stored files are decoded once.
    """
    scenario = load_scenario(path)
    dataset = stored_dataset(scenario, directory)
    bundle = run_scenario_on(scenario, dataset)
    strategy = load_strategy(strategy_document(scenario.strategy))
    model = model_of(strategy, dataset, scenario.start, scenario.end)
    return check(model, trace_of(bundle))


@pytest.fixture(scope="module")
def golden_report(tmp_path_factory: pytest.TempPathFactory) -> Callable[[Path], Report]:
    """Return a per-module memo of ``check_golden``, so the canary test reruns nothing."""
    reports: dict[Path, Report] = {}

    def report(path: Path) -> Report:
        if path not in reports:
            reports[path] = check_golden(path, tmp_path_factory.mktemp(path.stem))
        return reports[path]

    return report


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=[path.stem for path in SCENARIO_PATHS])
def test_golden_refines_r1_campaign(path: Path, golden_report: Callable[[Path], Report]) -> None:
    assert golden_report(path).violations == ()


def test_the_golden_suite_reaches_every_canary(golden_report: Callable[[Path], Report]) -> None:
    reached: set[str] = set()
    for path in SCENARIO_PATHS:
        reached |= golden_report(path).canaries
    assert sorted(set(CANARIES) - reached) == []


# --- C03: no decision reads the future ---------------------------------------------------------


def later(instant: int, *stamps: int) -> bool:
    """Return whether a record with these observed/available instants lies after ``instant``."""
    return max(stamps) > instant


def future_moved(tables: Tables, instant: int) -> Tables:
    """Move every value observed or available after ``instant``; availability +1 s."""
    cent, second = Decimal("0.01"), SECOND_NS
    return replace(
        tables,
        quotes=tuple(
            replace(
                q,
                bid=q.bid + cent,
                ask=q.ask + 2 * cent,
                available_at_ns=q.available_at_ns + second,
            )
            if later(instant, q.observed_at_ns, q.available_at_ns)
            else q
            for q in tables.quotes
        ),
        underlying=tuple(
            replace(u, value=Price(u.value.value + 1), available_at_ns=u.available_at_ns + second)
            if later(instant, u.observed_at_ns, u.available_at_ns)
            else u
            for u in tables.underlying
        ),
        activity=tuple(
            replace(
                a,
                cumulative_volume=a.cumulative_volume + 7,
                available_at_ns=a.available_at_ns + second,
            )
            if later(instant, a.measured_through_ns, a.available_at_ns)
            else a
            for a in tables.activity
        ),
        settlements=tuple(
            replace(s, value=Price(s.value.value + 1), available_at_ns=s.available_at_ns + second)
            if later(instant, s.available_at_ns)
            else s
            for s in tables.settlements
        ),
        rates=tuple(
            replace(r, bey=r.bey + cent, available_at_ns=r.available_at_ns + second)
            if later(instant, r.available_at_ns)
            else r
            for r in tables.rates
        ),
        features=tuple(
            replace(f, value=f.value + 1)
            if f.value is not None and later(instant, f.max_input_available_at_ns)
            else f
            for f in tables.features
        ),
    )


C03_INSTANTS: Final = (
    ("G02", date(2024, 3, 4), Slot.DEC),
    ("G02", date(2024, 3, 4), Slot.F1),
    ("G02", date(2024, 3, 8), Slot.CLOSE),
    ("G02", date(2024, 3, 15), Slot.CUT),
    ("G22", date(2024, 2, 21), Slot.DEC),
    ("G01 volume through CLOSE", date(2024, 3, 4), Slot.DEC),
)
"""G02's entry decision and fill, a held session's CLOSE mark and the expiry's settlement CUT
(quotes, index values, settlements); G22's first gate, whose same-session feature is defined
while the prior session's is not (features); and G01 behind a volume minimum of 1 whose only
volume, 0, is measured through CLOSE, so a DEC that read it would enter once it moved to 7
(activity)."""


def c03_subject(name: str) -> tuple[bytes, MarketSpec, Scenario]:
    """Return the strategy document, market and scenario C03 runs as ``name``."""
    if name == "G01 volume through CLOSE":
        return g01_with_volume_at(Slot.CLOSE, volume=0)
    scenario = scenario_named(f"{name}_")
    return strategy_document(scenario.strategy), scenario.market, scenario


def through(bundle: ArtifactBundle, instant: int) -> bytes:
    """Return the canonical JSON of the events and candidate decisions at or before ``instant``.

    The decisions carry every condition value and candidate the engine read, so a future value
    read and not acted on still shows.
    """
    decisions = tuple(d for d in bundle.candidate_decisions if d.at_ns <= instant)
    return events_through(bundle.events, instant) + canonical_json(decisions)


@pytest.mark.parametrize(("name", "session", "slot"), C03_INSTANTS, ids=str)
def test_c03_events_through_t_ignore_every_record_after_t(
    name: str, session: date, slot: Slot
) -> None:
    document, market, scenario = c03_subject(name)
    dataset = generate(market)
    instant = slot_instant(dataset, session, slot)
    contracts_known_later = [c for c in dataset.contracts if c.known_from_ns > instant]
    assert contracts_known_later == []  # no terms are revised; nothing else would need moving
    moved = refreeze(dataset, future_moved(tables_of(dataset), instant))
    assert moved.manifest.manifest_id != dataset.manifest.manifest_id  # the mutation is real
    before = run_on(document, dataset, scenario.start, scenario.end)
    after = run_on(document, moved, scenario.start, scenario.end)
    assert any(event.at_ns > instant for event in before.events)
    assert through(after, instant) == through(before, instant)


# --- C04: same-session volume and the official close are invisible at DEC ---------------------

VOLUME_LEGS: Final = ("SPXW:2024-03-15:P:4900", "SPXW:2024-03-15:P:4895")
"""G01's two legs, pinned at its 2024-03-04 DEC entry."""


def g01_with_volume_at(slot: Slot, volume: int = 100) -> tuple[bytes, MarketSpec, Scenario]:
    """Return G01 with a volume minimum of 1 and each leg's session volume pinned at ``slot``."""
    scenario = scenario_named("G01_")
    document = patched(scenario.strategy, {"liquidity": {"min_cumulative_volume": 1}})
    pins = tuple(ActivityPin(leg, date(2024, 3, 4), slot, volume) for leg in VOLUME_LEGS)
    market = replace(scenario.market, overrides=(*scenario.market.overrides, *pins))
    return document, market, scenario


def entry_fills(bundle: ArtifactBundle) -> list[SimEvent]:
    """Return the run's fills."""
    return [event for event in bundle.events if event.kind is SimEventKind.FILLED]


def test_c04_volume_measured_through_close_does_not_count_at_dec() -> None:
    document, market, scenario = g01_with_volume_at(Slot.DEC)
    control = run_on(document, generate(market), scenario.start, scenario.end)
    assert [event.session_date for event in entry_fills(control)][:1] == [date(2024, 3, 4)]
    document, market, scenario = g01_with_volume_at(Slot.CLOSE)
    late = run_on(document, generate(market), scenario.start, scenario.end)
    assert entry_fills(late) == []


SMA_GATE: Final[dict[str, Json]] = {
    "entry": {
        "all_conditions": [
            {"feature": "underlying.close_to_sma_50s", "operator": "gte", "value": Decimal(-1)}
        ]
    }
}
"""A condition true whenever the 50-session close feature is defined (ratio or difference)."""


def g01_with_history() -> tuple[bytes, MarketSpec, Scenario]:
    """Return G01 behind the SMA gate on a market starting 2023-12-01 (66 sessions of history).

    ``weekly_dtes`` 105 keeps G01's only expiry, 2024-03-15, so its pins apply unchanged.
    """
    scenario = scenario_named("G01_")
    document = patched(scenario.strategy, SMA_GATE)
    market = replace(scenario.market, first_session=date(2023, 12, 1), weekly_dtes=(105,))
    return document, market, scenario


def feature_facts(dataset: FrozenDataset) -> bytes:
    """Return the canonical JSON of each feature's id, session, value and visibility."""
    facts = tuple(
        (f.feature_id, f.session_date, f.value, f.max_input_available_at_ns)
        for f in dataset.features
    )
    return canonical_json(facts)


def test_c04_dropping_every_official_close_changes_no_feature_and_no_event() -> None:
    document, market, scenario = g01_with_history()
    dataset = generate(market)
    drops = tuple(
        UnderlyingDrop("SPX", UnderlyingField.OFFICIAL_CLOSE, row.session_date, (Slot.CLOSE,))
        for row in dataset.sessions
    )
    dropped = generate(replace(market, overrides=(*market.overrides, *drops)))
    assert feature_facts(dropped) == feature_facts(dataset)
    before = run_on(document, dataset, scenario.start, scenario.end)
    after = run_on(document, dropped, scenario.start, scenario.end)
    assert entry_fills(before)  # the gate read a defined feature and opened
    assert canonical_json(after.events) == canonical_json(before.events)


# --- C34: shuffled rows, same identity; the journal replays to the final state --------------


def shuffled(tables: Tables, rng: random.Random) -> Tables:
    """Return every table's rows in a random order."""
    rows = [list(getattr(tables, name)) for name in TABLE_NAMES]
    for table in rows:
        rng.shuffle(table)
    return Tables(*(tuple(table) for table in rows))


def balance(state: LedgerState, kind: AccountKind) -> Decimal:
    """Return the sum of every balance of ``kind``."""
    return sum((usd.amount for key, usd in state.balances.items() if key.kind is kind), Decimal(0))


def test_c34_shuffled_rows_freeze_and_run_identically() -> None:
    scenario = scenario_named("G09_")
    dataset = generate(scenario.market)
    rng = random.Random(20240304)  # noqa: S311 — a fixed row order, not a secret
    again = refreeze(dataset, shuffled(tables_of(dataset), rng))
    assert again.manifest.manifest_id == dataset.manifest.manifest_id
    assert tables_of(again) == tables_of(dataset)
    assert run_scenario_on(scenario, again).digests == run_scenario_on(scenario, dataset).digests


def test_c34_replaying_the_journal_reproduces_the_final_state() -> None:
    scenario = scenario_named("G09_")
    bundle = run_scenario_on(scenario, generate(scenario.market))
    state = replay(bundle.journal)
    last = bundle.events[-1].summary
    assert balance(state, AccountKind.CASH) == last.cash.amount
    assert balance(state, AccountKind.RECEIVABLE) == last.receivable.amount
    assert -balance(state, AccountKind.PAYABLE) == last.payable.amount
    held = tuple(sorted((c, sum(lot.quantity for lot in lots)) for c, lots in state.lots.items()))
    assert held == last.held == bundle.result.open_positions


# --- determinism -------------------------------------------------------------------------------

DETERMINISM_GOLDEN: Final = "G09_sequential_roll_links_realized.toml"


def digests_in_subprocess(hash_seed: str) -> list[list[str]]:
    """Run ``DETERMINISM_GOLDEN`` in a fresh interpreter under ``PYTHONHASHSEED=hash_seed``."""
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(TESTS_DIR)}
    done = subprocess.run(  # noqa: S603 — this interpreter, a fixed script, no shell
        [sys.executable, "-c", DIGESTS_SCRIPT, DETERMINISM_GOLDEN],
        cwd=SERVICE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    digests: list[list[str]] = json.loads(done.stdout)
    return digests


def test_reruns_and_other_hash_seeds_give_identical_digests(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS_DIR / DETERMINISM_GOLDEN)
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = run_scenario(scenario, tmp_path / "first")
    second = run_scenario(scenario, tmp_path / "second")
    assert second.digests == first.digests
    expected = [list(pair) for pair in first.digests]
    assert digests_in_subprocess("0") == expected
    assert digests_in_subprocess("1") == expected
