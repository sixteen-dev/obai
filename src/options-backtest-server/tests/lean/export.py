"""LEAN minute files of a ``FrozenDataset`` (ADR 0002 §12 step 2, §16 decision 3).

``export`` is pure: it turns observations into LEAN's on-disk minute format and returns the zip
archives as data; ``archive_bytes`` renders one archive as deterministic zip bytes. Writing them
under a data folder is the runner's job.

Rules:

- An observation at ``t`` is the bar ``[t − 1 min, t)`` with O=H=L=C. LEAN keys a minute bar by
  its start and emits it at its end, so the algorithm sees it in the slice at ``t``.
- Only observed slots produce bars: a dropped quote or print leaves no row. Nothing is filled in.
- SPX INDEX_VALUE prints are the index bars; OFFICIAL_CLOSE is not exported. On an expiry
  session (one with a final ``SPX_PM`` settlement) the bar ending at the session's close carries
  the settlement value of the highest correction instead of the print, and exists even when the
  print was dropped (M3), so LEAN's last underlying price at expiry is the official value.
- SPXW quotes are the option bars, both sides raw (a zero bid stays 0; LEAN then drops that side,
  see ``LEAN_FORMAT``).
- Refused with ``ValueError``: a root or underlying LEAN does not list (M9: no XSP), an
  observation off the whole-minute grid (a minute bar cannot hold it), an observation available
  after it was observed (LEAN has no availability lag), two observations in one bar.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from io import BytesIO
from types import MappingProxyType
from typing import Final
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import UnderlyingField
from options_backtest.models.market import require_type
from options_backtest.money import EXACT, Price
from options_backtest.reference.products import SPXW_RULES


@dataclass(frozen=True, slots=True)
class LeanSeries:
    """Where and how LEAN stores one minute series.

    Attributes:
        directory: Folder relative to LEAN's data folder.
        archive: Zip file name; ``{date}`` is the bar's local date, ``yyyyMMdd``.
        entry: CSV entry name; options add ``{right}`` (call/put), ``{strike}`` (scaled) and
            ``{expiry}`` (``yyyyMMdd``).
        price_scale: Factor from price to file units.
        time_zone: LEAN's data time zone of the series: bar times are local wall-clock
            milliseconds after local midnight.

    """

    directory: str
    archive: str
    entry: str
    price_scale: Decimal
    time_zone: str


# Verified 2026-09-25 against the LEAN checkout at b1337938 (paths relative to $LEAN_ROOT):
# - directory: Common/Util/LeanData.cs:549-569 GenerateRelativeZipFileDirectory puts Index under
#   {type}/{market}/{resolution}/{ticker} and IndexOption under the canonical option ticker (spxw,
#   not spx), all lower case.
# - archive: LeanData.cs:800-809 an Index minute zip is "{date}_{ticktype}.zip" (trade);
#   LeanData.cs:827-836 an IndexOption one "{date}_{ticktype}_{style}.zip" (quote, european).
# - entry: LeanData.cs:676-692 "{date}_{ticker}_{resolution}_{ticktype}.csv" for Index;
#   LeanData.cs:720-746 "{date}_{option ticker}_{resolution}_{ticktype}_{style}_{right}_
#   {Scale(strike)}_{expiry}.csv" for IndexOption; Common/Extensions.cs:2661-2682 spell the
#   right "call"/"put" and the style "european".
# - columns and scale: LeanData.cs:238-251 an Index minute row is "ms,open,high,low,close,volume"
#   unscaled; LeanData.cs:281-289 an IndexOption minute quote row is "ms, bid OHLC, bid size,
#   ask OHLC, ask size" with each price Scale()d; LeanData.cs:951-954 Scale is value × 10000
#   normalized and LeanData.cs:959-968 ToCsv normalizes every decimal (no trailing zeros).
# - readers: Common/Data/Market/TradeBar.cs:288-289 Index reads through ParseIndex
#   (TradeBar.cs:732-735, no scale) and TradeBar.cs:676 sets Time = date + ms, converted from the
#   data to the exchange time zone; QuoteBar.cs:368-371 and 432-435 IndexOption reads through
#   ParseOption, scaled by 1/10000 (QuoteBar.cs:36, LeanData.cs:1534-1537); QuoteBar.cs:548-575
#   drop a side whose four prices are 0, and QuoteBar.cs:163-190 make Close the bid/ask mid only
#   when both sides are nonzero, otherwise the nonzero side (a NO_BID quote marks at its ask).
# - time zones: Data/market-hours/market-hours-database.json:103815-103817 "Index-usa-SPX" has
#   dataTimeZone America/Chicago; :108879-108881 "IndexOption-usa-SPXW" America/New_York.
# - shipped samples agree: Data/index/usa/minute/spx/20210104_trade.zip holds
#   20210104_spx_minute_trade.csv whose first row is "30600000,3764.61,3769.99,3764.61,3766.63,59"
#   (08:30 CT); Data/indexoption/usa/minute/spxw/20210105_quote_european.zip holds
#   20210105_spxw_minute_quote_european_put_37000000_20210106.csv whose first row starts
#   "34200000,302000," (09:30 ET, bid 30.20).
LEAN_FORMAT: Final[Mapping[str, LeanSeries]] = MappingProxyType(
    {
        SPXW_RULES.underlying_id: LeanSeries(
            directory="index/usa/minute/spx",
            archive="{date}_trade.zip",
            entry="{date}_spx_minute_trade.csv",
            price_scale=Decimal(1),
            time_zone="America/Chicago",
        ),
        SPXW_RULES.root: LeanSeries(
            directory="indexoption/usa/minute/spxw",
            archive="{date}_quote_european.zip",
            entry="{date}_spxw_minute_quote_european_{right}_{strike}_{expiry}.csv",
            price_scale=Decimal(10_000),
            time_zone="America/New_York",
        ),
    }
)
"""The exported series by our id: the SPX index and the SPXW option root."""

_MINUTE_NS: Final = 60 * 10**9
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
"""Every zip entry's timestamp, so equal archives give equal bytes."""
_RIGHTS: Final = MappingProxyType({"C": "call", "P": "put"})


@dataclass(frozen=True, slots=True)
class LeanArchive:
    """One zip archive of the export.

    Attributes:
        path: POSIX path relative to LEAN's data folder.
        entries: (CSV entry name, CSV text) pairs sorted by name; each row ends in a newline.

    """

    path: str
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Bar:
    """One CSV row and where it goes."""

    archive: str
    entry: str
    ms: int
    row: str


def export(dataset: FrozenDataset) -> tuple[LeanArchive, ...]:
    """Return the LEAN minute archives of a dataset's SPX prints and SPXW quotes.

    Args:
        dataset: The frozen dataset.

    Returns:
        The archives sorted by path, their entries sorted by name, rows by time.

    Raises:
        TypeError: If ``dataset`` is not a ``FrozenDataset``.
        ValueError: On a root or underlying LEAN does not list, an observation off the minute
            grid or available after it was observed, or two observations in one bar.

    """
    require_type(dataset, FrozenDataset, "export dataset")
    rows: defaultdict[tuple[str, str], list[_Bar]] = defaultdict(list)
    for bar in (*_index_bars(dataset), *_option_bars(dataset)):
        rows[(bar.archive, bar.entry)].append(bar)
    entries: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for (archive, entry), bars in rows.items():
        entries[archive].append((entry, _csv(archive, entry, bars)))
    return tuple(LeanArchive(path, tuple(sorted(entries[path]))) for path in sorted(entries))


def archive_bytes(archive: LeanArchive) -> bytes:
    """Return the archive as zip bytes: deflated entries in order, each stamped 1980-01-01.

    Args:
        archive: The archive.

    Returns:
        The zip bytes; equal archives give equal bytes.

    Raises:
        TypeError: If ``archive`` is not a ``LeanArchive``.
        UnicodeEncodeError: If an entry is not ASCII.

    """
    require_type(archive, LeanArchive, "archive_bytes archive")
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        for name, text in archive.entries:
            info = ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = ZIP_DEFLATED
            zipped.writestr(info, text.encode("ascii"))
    return buffer.getvalue()


def _csv(archive: str, entry: str, bars: list[_Bar]) -> str:
    """Return one entry's rows by time; two bars at one time are refused."""
    ordered = sorted(bars, key=lambda bar: bar.ms)
    times = [bar.ms for bar in ordered]
    if len(set(times)) != len(times):
        raise ValueError(f"{archive}/{entry}: two observations fall in one minute bar")
    return "".join(f"{bar.row}\n" for bar in ordered)


def _index_bars(dataset: FrozenDataset) -> Iterator[_Bar]:
    """Yield the SPX bars: each INDEX_VALUE print, the expiry sessions' close at settlement."""
    series = LEAN_FORMAT[SPXW_RULES.underlying_id]
    prints: dict[int, Price] = {}
    for print_ in dataset.underlying:
        if print_.underlying_id != SPXW_RULES.underlying_id:
            raise ValueError(
                f"LEAN lists no index {print_.underlying_id} ({print_.observation_id})"
            )
        if print_.field is not UnderlyingField.INDEX_VALUE:
            continue
        _require_bar_instant(print_.observation_id, print_.observed_at_ns, print_.available_at_ns)
        if print_.observed_at_ns in prints:
            raise ValueError(f"{print_.observation_id}: two index prints in one minute bar")
        prints[print_.observed_at_ns] = print_.value
    prints.update(_expiry_closes(dataset))
    for end_ns, value in prints.items():
        local = _bar_start(end_ns, series.time_zone)
        day, ms, price = _yyyymmdd(local.date()), _ms_of_day(local), _text(value.value)
        yield _Bar(
            archive=f"{series.directory}/{series.archive.format(date=day)}",
            entry=series.entry.format(date=day),
            ms=ms,
            row=f"{ms},{price},{price},{price},{price},0",
        )


def _expiry_closes(dataset: FrozenDataset) -> dict[int, Price]:
    """Return ``{close_ns: settlement}`` per session with a final ``SPX_PM`` settlement (M3)."""
    closes = {session.session_date: session.close_ns for session in dataset.sessions}
    finals = sorted(
        (row for row in dataset.settlements if row.final),
        key=lambda row: row.correction_version,
    )
    values: dict[int, Price] = {}
    for row in finals:
        if row.settlement_series != SPXW_RULES.settlement_series:
            continue
        if row.session_date not in closes:
            raise ValueError(f"{row.observation_id} settles {row.session_date}, not a session")
        values[closes[row.session_date]] = row.value  # the highest correction is last
    return values


def _option_bars(dataset: FrozenDataset) -> Iterator[_Bar]:
    """Yield one SPXW quote bar per quote observation, both sides raw and scaled."""
    series = LEAN_FORMAT[SPXW_RULES.root]
    for quote in dataset.quotes:
        root, expiry, right, strike = quote.contract_id.split(":")
        if root != SPXW_RULES.root:
            raise ValueError(f"LEAN lists no option root {root} ({quote.observation_id})")
        _require_bar_instant(quote.observation_id, quote.observed_at_ns, quote.available_at_ns)
        local = _bar_start(quote.observed_at_ns, series.time_zone)
        day, ms = _yyyymmdd(local.date()), _ms_of_day(local)
        bid = _text(_scaled(quote.bid, series.price_scale))
        ask = _text(_scaled(quote.ask, series.price_scale))
        name = series.entry.format(
            date=day,
            right=_RIGHTS[right],
            strike=_text(_scaled(Decimal(strike), series.price_scale)),
            expiry=_yyyymmdd(date.fromisoformat(expiry)),
        )
        yield _Bar(
            archive=f"{series.directory}/{series.archive.format(date=day)}",
            entry=name,
            ms=ms,
            row=(
                f"{ms},{bid},{bid},{bid},{bid},{quote.bid_size},"
                f"{ask},{ask},{ask},{ask},{quote.ask_size}"
            ),
        )


def _require_bar_instant(observation_id: str, observed_at_ns: int, available_at_ns: int) -> None:
    if observed_at_ns % _MINUTE_NS:
        raise ValueError(f"{observation_id} is not observed on a whole minute")
    if available_at_ns != observed_at_ns:
        raise ValueError(f"{observation_id} is available after it was observed")


def _bar_start(end_ns: int, time_zone: str) -> datetime:
    """Return the local wall-clock start of the minute bar ending at ``end_ns``."""
    start = _EPOCH + timedelta(microseconds=(end_ns - _MINUTE_NS) // 1000)
    return start.astimezone(ZoneInfo(time_zone))


def _ms_of_day(local: datetime) -> int:
    """Return LEAN's bar time: wall-clock milliseconds after local midnight."""
    return ((local.hour * 60 + local.minute) * 60 + local.second) * 1000


def _yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


def _scaled(value: Decimal, scale: Decimal) -> Decimal:
    with localcontext(EXACT):
        return value * scale


def _text(value: Decimal) -> str:
    """Return LEAN's normalized decimal text: no exponent, no trailing zeros (5000.00 → 5000)."""
    with localcontext(EXACT):
        return format(value.normalize(), "f")
