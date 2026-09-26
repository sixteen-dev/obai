"""The LEAN export, byte for byte against hand-written files (ADR 0002 §12 step 2).

The dataset spans the 2024 US daylight-saving change: 2024-03-08 is EST/CST and 2024-03-11 is
EDT/CDT, so equal local bar times on both days prove that the export converts through the zone
rules, not a fixed offset. Every exported value is pinned (zero drift and volatility fix the
index at 5000.25), so each expected row is written by hand:

- index bars (America/Chicago): DEC 15:45 ET is the bar [14:44, 14:45) CT, ``53040000`` ms after
  local midnight; F1-F3 follow one minute apart; CLOSE 16:00 ET is ``53940000``. Prices are
  unscaled, O=H=L=C, volume 0. The F3 print of 2024-03-08 is dropped, so it has no bar. On the
  expiry day 2024-03-11 the CLOSE bar carries the pinned settlement 4997.50, not the print.
- option bars (America/New_York): DEC is the bar [15:44, 15:45) ET, ``56640000`` ms; CLOSE is
  ``57540000``. Prices ×10000; the strike 5000 is ``50000000`` in the file name. The put's F2
  quote of 2024-03-08 is dropped, so it has no bar; the expiry session has no CLOSE quote.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Final
from zipfile import ZipFile

import pytest

from options_backtest.data.records import UnderlyingField
from options_backtest.money import Price
from options_backtest.reference.calendars import QUOTE_SLOTS, Slot
from options_backtest.synthetic.market import (
    LateAvailability,
    MarketSpec,
    Override,
    QuoteDrop,
    QuotePin,
    QuoteStale,
    SettlementPin,
    UnderlyingDrop,
    generate,
)

from .export import archive_bytes, export

EXPECTED_DIR: Final = Path(__file__).parent / "expected_export"
CALL: Final = "SPXW:2024-03-11:C:5000"
PUT: Final = "SPXW:2024-03-11:P:5000"
FRIDAY: Final = date(2024, 3, 8)
MONDAY: Final = date(2024, 3, 11)
BEFORE_CLOSE: Final = (Slot.DEC, Slot.F1, Slot.F2, Slot.F3)


def _spec(overrides: tuple[Override, ...]) -> MarketSpec:
    return MarketSpec(
        seed=1,
        first_session=FRIDAY,
        last_session=MONDAY,
        holidays=(),
        early_closes=(),
        index_start=Decimal("5000.25"),
        daily_drift=Decimal(0),
        daily_vol=Decimal(0),
        sigma=Decimal("0.18"),
        rates=((28, Decimal(0)),),
        roots=("SPXW",),
        weekly_dtes=(3,),
        strike_step=Decimal(5),
        strikes_each_side=0,
        tick=Decimal("0.05"),
        half_spread_abs=Decimal("0.05"),
        half_spread_rel=Decimal("0.02"),
        bid_size=50,
        ask_size=50,
        premium_multiplier=Decimal(100),
        deliverable_units=Decimal(100),
        overrides=overrides,
    )


PINNED: Final[tuple[Override, ...]] = (
    QuotePin(CALL, FRIDAY, QUOTE_SLOTS, Decimal("60.00"), Decimal("61.50"), 10, 12),
    QuotePin(CALL, FRIDAY, (Slot.F2,), Decimal("60.25"), Decimal("61.75"), 11, 13),
    QuotePin(
        PUT,
        FRIDAY,
        (Slot.DEC, Slot.F1, Slot.F3, Slot.CLOSE),
        Decimal("55.05"),
        Decimal("56.10"),
        7,
        9,
    ),
    QuoteDrop(PUT, FRIDAY, (Slot.F2,)),
    QuotePin(CALL, MONDAY, BEFORE_CLOSE, Decimal("12.35"), Decimal("12.85"), 20, 20),
    QuotePin(PUT, MONDAY, BEFORE_CLOSE, Decimal(0), Decimal("0.05"), 0, 30),
    UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, FRIDAY, (Slot.F3,)),
    SettlementPin("SPX_PM", MONDAY, Price(Decimal("4997.50"))),
)


def _expected_files() -> dict[tuple[str, str], bytes]:
    """Return ``{(archive path, entry name): bytes}`` from ``expected_export/<archive>/<entry>``."""
    files = sorted(path for path in EXPECTED_DIR.rglob("*.csv") if path.is_file())
    return {
        (path.parent.relative_to(EXPECTED_DIR).as_posix(), path.name): path.read_bytes()
        for path in files
    }


def test_export_matches_the_hand_written_files_byte_for_byte() -> None:
    archives = export(generate(_spec(PINNED)))

    exported = {
        (archive.path, name): text.encode("ascii")
        for archive in archives
        for name, text in archive.entries
    }

    expected = _expected_files()
    assert len(expected) == 6
    assert sorted(exported) == sorted(expected)
    for key, content in expected.items():
        assert exported[key] == content, key


def test_export_orders_archives_by_path_and_entries_by_name() -> None:
    archives = export(generate(_spec(PINNED)))

    assert [archive.path for archive in archives] == [
        "index/usa/minute/spx/20240308_trade.zip",
        "index/usa/minute/spx/20240311_trade.zip",
        "indexoption/usa/minute/spxw/20240308_quote_european.zip",
        "indexoption/usa/minute/spxw/20240311_quote_european.zip",
    ]
    for archive in archives:
        names = [name for name, _ in archive.entries]
        assert names == sorted(names)


def test_archive_bytes_is_a_deterministic_zip_of_the_entries() -> None:
    archive = export(generate(_spec(PINNED)))[2]

    first = archive_bytes(archive)

    assert first == archive_bytes(archive)
    with ZipFile(BytesIO(first)) as unzipped:
        assert [info.filename for info in unzipped.infolist()] == [n for n, _ in archive.entries]
        assert {info.date_time for info in unzipped.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        for name, text in archive.entries:
            assert unzipped.read(name).decode("ascii") == text


def test_export_of_generated_quotes_scales_every_price_exactly() -> None:
    dataset = generate(_spec(()))
    quotes = {quote.observation_id: quote for quote in dataset.quotes}
    call_dec = quotes[f"q:{CALL}:2024-03-08:DEC"]

    archive = export(dataset)[2]
    text = dict(archive.entries)["20240308_spxw_minute_quote_european_call_50000000_20240311.csv"]

    fields = text.splitlines()[0].split(",")
    assert fields[0] == "56640000"
    assert Decimal(fields[4]) == call_dec.bid * 10_000
    assert Decimal(fields[9]) == call_dec.ask * 10_000
    assert fields[5] == str(call_dec.bid_size)


def test_export_refuses_a_root_lean_does_not_list() -> None:
    dataset = generate(replace(_spec(()), roots=("SPXW", "XSP")))

    with pytest.raises(ValueError, match="XSP"):
        export(dataset)


def test_export_refuses_an_observation_off_the_minute_grid() -> None:
    dataset = generate(_spec((QuoteStale(CALL, FRIDAY, (Slot.F1,), 30),)))

    with pytest.raises(ValueError, match="whole minute"):
        export(dataset)


def test_export_refuses_an_observation_available_after_it_was_observed() -> None:
    late = datetime(2024, 3, 8, 21, 0, tzinfo=UTC)
    dataset = generate(_spec((LateAvailability(f"q:{CALL}:2024-03-08:F1", late),)))

    with pytest.raises(ValueError, match="available"):
        export(dataset)
