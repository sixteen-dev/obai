"""Historical entry features, computed once at dataset creation (ADR 0002 §4, design §13.2).

A feature is a §8.2 record: a session's value is computed at that session's CLOSE snapshot
through ``AsOfView(dataset, close_ns)`` and carries the latest ``available_at_ns`` of its inputs,
so it is visible from that instant, never at the same session's DEC (C04). The entry gate reads
the prior table session's value. All arithmetic is float64; values are ``Decimal(float)``. A
missing input gives ``value=None`` with a reason, never zero.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import Final

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset, canonical_json
from options_backtest.data.records import (
    ContractVersion,
    FeatureObservation,
    QuoteObservation,
    QuoteStatus,
    TradingSession,
    UnderlyingObservation,
)
from options_backtest.models.market import OptionType, require_id, require_type
from options_backtest.money import EXACT
from options_backtest.pricing.iv import ParityPair, implied_vol, parity_forward
from options_backtest.reference.products import ProductRules
from options_backtest.reference.rates import CMT_CURVE_ID, DiscountCurve

ATM30_IV: Final = "options.atm30_iv"
IV_RANK_252: Final = "options.atm30_iv_rank_252s"
IV_PERCENTILE_252: Final = "options.atm30_iv_percentile_252s"
RETURN_20: Final = "underlying.return_20s"
CLOSE_TO_SMA_50: Final = "underlying.close_to_sma_50s"
FEATURE_VERSIONS: Final = MappingProxyType(
    {
        ATM30_IV: "1",
        IV_RANK_252: "1",
        IV_PERCENTILE_252: "1",
        RETURN_20: "1",
        CLOSE_TO_SMA_50: "1",
    }
)
"""Every feature name the generator computes, with its definition version."""

_SPOT_MAX_AGE_NS: Final = 60 * 10**9
"""Largest age of the CLOSE index print a feature reads, inclusive (design §8.4)."""
_QUOTE_MAX_AGE_NS: Final = 120 * 10**9
"""Largest age of an option quote a feature reads, inclusive (design §8.4)."""
_NS_PER_DAY: Final = 86_400 * 10**9
_NS_PER_YEAR: Final = 365 * _NS_PER_DAY
_T30_NS: Final = 30 * _NS_PER_DAY
_T30: Final = _T30_NS / _NS_PER_YEAR
"""30 calendar days in ACT/365F years."""
_IV_WINDOW: Final = 252
_RETURN_LAG: Final = 20
_SMA_WINDOW: Final = 50

type _Inputs = tuple[tuple[str, int], ...]
"""(input id, available_at_ns) of every input a computation read."""


@dataclass(frozen=True, slots=True)
class _Value:
    """One session's computed quantity, or why there is none, with the inputs it read.

    Attributes:
        value: The float64 value; None when unavailable.
        reason: The missing reason; None when ``value`` is present.
        inputs: (input id, available_at_ns) of every input read.
        warmup: Consecutive prior sessions with the needed input, capped at the window.

    """

    value: float | None
    reason: str | None
    inputs: _Inputs
    warmup: int = 0


@dataclass(frozen=True, slots=True)
class _Chain:
    """One listed expiry of the root as seen from a CLOSE snapshot.

    Attributes:
        t: ACT/365F years from the snapshot to expiry, > 0.
        contracts: The expiry's listed contract versions.

    """

    t: float
    contracts: tuple[ContractVersion, ...]


def feature_id(underlying_id: str, name: str) -> str:
    """Return the feature id ``{underlying_id}:{name}``, e.g. ``SPX:underlying.return_20s``.

    Args:
        underlying_id: Underlying index of the strategy (``product.underlying_symbol``).
        name: A ``FEATURE_VERSIONS`` name; the strategy condition names are these strings.

    Returns:
        The id.

    Raises:
        ValueError: If ``name`` is not a ``FEATURE_VERSIONS`` name.

    """
    require_id(underlying_id, "feature_id underlying_id")
    if name not in FEATURE_VERSIONS:
        raise ValueError(f"unknown feature {name!r}; known: {sorted(FEATURE_VERSIONS)}")
    return f"{underlying_id}:{name}"


def feature_series(
    dataset: FrozenDataset, rules: ProductRules, versions: Mapping[str, str]
) -> tuple[FeatureObservation, ...]:
    """Return every feature of the root's underlying for every session of the dataset.

    Per session ``s`` in table order, at ``AsOfView(dataset, s.close_ns)``:

    - close ``c_s``: the ``INDEX_VALUE`` of ``rules.underlying_id`` at most 60 s old (the CLOSE
      print; the 17:00 OFFICIAL_CLOSE is not visible at close).
    - ``options.atm30_iv``: among ``listed(rules.root)`` expiries with ``T > 0`` (ACT/365F from
      ``close_ns``), the largest ``T <= 30/365`` and the smallest ``T >= 30/365``. For each: DF
      from the ``UST_CMT`` curve at ``T·365`` days, F from ``parity_forward`` over the VALID
      call/put pairs (quotes at most 120 s old) with spot ``c_s``; the largest strike ``<= F``
      with a VALID put and the smallest ``>= F`` with a VALID call; their ``implied_vol`` from
      mids give total variances ``w = iv²·T``, interpolated linearly in ``ln(K/F)`` to 0 (at an
      exact-F strike, the mean of the call and put variances; both required). Then ``w`` is
      linear in ``T`` to ``T30 = 30/365`` and ``sqrt(w30/T30)`` is the value. No extrapolation.
    - ``options.atm30_iv_rank_252s``: ``100·(now - min)/(max - min)`` over the 252 table
      sessions before ``s``, all with a value; None if any is missing, fewer exist, or the
      range is zero. Not clamped.
    - ``options.atm30_iv_percentile_252s``: ``100·count(h <= now)/252`` over the same history.
    - ``underlying.return_20s``: ``c_s / c_{s-20} - 1``, 20 table sessions back.
    - ``underlying.close_to_sma_50s``: ``c_s / (math.fsum(c over the 50 sessions ending at s)/50)
      - 1``.

    ``warmup_count`` counts the consecutive prior sessions with the needed input, capped at 252,
    20 and 49 respectively (0 for ``atm30_iv``). ``missing_reason`` is one of ``"warmup"``,
    ``"input_missing"``, ``"no_bracket"``, ``"curve_unavailable"``, ``"forward_unavailable"``,
    ``"strike_unavailable"``, ``"iv_unavailable"``, ``"range_zero"``. Inputs for
    ``input_digest`` are the market observation ids read, or ``{feature_id}@{session_date}``
    for a feature input.

    Args:
        dataset: Dataset without features (its features table is ignored).
        rules: Rules of the root whose chain and underlying are used.
        versions: Version per feature name; must name every ``FEATURE_VERSIONS`` name.

    Returns:
        Five observations per session, ids ``feature_id(rules.underlying_id, name)``.

    Raises:
        ValueError: If ``versions`` lacks a feature name.

    """
    require_type(dataset, FrozenDataset, "feature_series dataset")
    require_type(rules, ProductRules, "feature_series rules")
    missing = [name for name in FEATURE_VERSIONS if name not in versions]
    if missing:
        raise ValueError(f"feature_series versions lack {missing}")
    sessions = dataset.sessions
    views = tuple(AsOfView(dataset, session.close_ns) for session in sessions)
    closes = tuple(_session_close(view, rules.underlying_id) for view in views)
    atm = tuple(
        _atm30_iv(view, rules.root, close) for view, close in zip(views, closes, strict=True)
    )
    atm_id = feature_id(rules.underlying_id, ATM30_IV)
    rows: list[FeatureObservation] = []
    for position, session in enumerate(sessions):
        rank, percentile = _rank_and_percentile(atm, position, sessions, atm_id)
        values = {
            ATM30_IV: atm[position],
            IV_RANK_252: rank,
            IV_PERCENTILE_252: percentile,
            RETURN_20: _return_20(closes, position),
            CLOSE_TO_SMA_50: _close_to_sma_50(closes, position),
        }
        rows.extend(
            _record(feature_id(rules.underlying_id, name), versions[name], session, value)
            for name, value in values.items()
        )
    return tuple(rows)


def _record(
    identifier: str, version: str, session: TradingSession, value: _Value
) -> FeatureObservation:
    """Return the feature record of one computed value."""
    ids = sorted({input_id for input_id, _ in value.inputs})
    return FeatureObservation(
        feature_id=identifier,
        feature_version=version,
        session_date=session.session_date,
        value=None if value.value is None else Decimal(value.value),
        max_input_available_at_ns=_visible_from(value, session),
        warmup_count=value.warmup,
        missing_reason=value.reason,
        input_digest=hashlib.sha256(canonical_json(ids)).hexdigest(),
    )


def _visible_from(value: _Value, session: TradingSession) -> int:
    """Return the latest input ``available_at_ns``; the session's close when none was read."""
    return max((available for _, available in value.inputs), default=session.close_ns)


def _session_close(view: AsOfView, underlying_id: str) -> UnderlyingObservation | None:
    """Return the CLOSE index print visible at the view, at most 60 s old."""
    close = view.index_value(underlying_id, max_age_ns=_SPOT_MAX_AGE_NS)
    if close is not None and close.value.value <= 0:
        raise ValueError(f"{close.observation_id}: an index value must be > 0 to price from it")
    return close


def _close_inputs(closes: Sequence[UnderlyingObservation]) -> _Inputs:
    return tuple((close.observation_id, close.available_at_ns) for close in closes)


def _run_length(values: Sequence[object | None], position: int, cap: int) -> int:
    """Return how many entries right before ``position`` are present, counting back to ``cap``."""
    count = 0
    for value in reversed(values[max(0, position - cap) : position]):
        if value is None:
            break
        count += 1
    return count


# --- underlying features -------------------------------------------------------------------------


def _return_20(closes: Sequence[UnderlyingObservation | None], position: int) -> _Value:
    """Return ``c_s / c_{s-20} - 1``; only the two closes are needed."""
    warmup = _run_length(closes, position, _RETURN_LAG)
    now = closes[position]
    if now is None:
        return _Value(None, "input_missing", (), warmup)
    if position < _RETURN_LAG:
        return _Value(None, "warmup", _close_inputs([now]), warmup)
    base = closes[position - _RETURN_LAG]
    if base is None:
        return _Value(None, "input_missing", _close_inputs([now]), warmup)
    value = float(now.value.value) / float(base.value.value) - 1
    return _Value(value, None, _close_inputs([now, base]), warmup)


def _close_to_sma_50(closes: Sequence[UnderlyingObservation | None], position: int) -> _Value:
    """Return ``c_s / SMA_50 - 1``; needs the 49 closes before ``s`` and ``c_s``."""
    warmup = _run_length(closes, position, _SMA_WINDOW - 1)
    now = closes[position]
    if now is None:
        return _Value(None, "input_missing", (), warmup)
    if warmup < _SMA_WINDOW - 1:
        return _Value(None, "warmup", _close_inputs([now]), warmup)
    window = [c for c in closes[position - _SMA_WINDOW + 1 : position + 1] if c is not None]
    mean = math.fsum(float(close.value.value) for close in window) / _SMA_WINDOW
    return _Value(float(now.value.value) / mean - 1, None, _close_inputs(window), warmup)


# --- IV rank and percentile -----------------------------------------------------------------------


def _rank_and_percentile(
    atm: Sequence[_Value], position: int, sessions: Sequence[TradingSession], atm_id: str
) -> tuple[_Value, _Value]:
    """Return (rank, percentile) of the session's ``atm30_iv`` over the 252 sessions before it."""
    start = max(0, position - _IV_WINDOW)
    inputs = tuple(
        (f"{atm_id}@{sessions[j].session_date.isoformat()}", _visible_from(atm[j], sessions[j]))
        for j in range(start, position + 1)
    )
    history = [value.value for value in atm[start:position]]
    warmup = _run_length(history, len(history), _IV_WINDOW)
    now = atm[position].value
    if now is None or warmup < _IV_WINDOW:
        missing = _Value(None, "input_missing" if now is None else "warmup", inputs, warmup)
        return missing, missing
    valid = [past for past in history if past is not None]
    low, high = min(valid), max(valid)
    percentile = 100 * sum(1 for past in valid if past <= now) / _IV_WINDOW
    rank = (
        _Value(None, "range_zero", inputs, warmup)
        if high == low
        else _Value(100 * (now - low) / (high - low), None, inputs, warmup)
    )
    return rank, _Value(percentile, None, inputs, warmup)


# --- atm30_iv -------------------------------------------------------------------------------------


def _atm30_iv(view: AsOfView, root: str, close: UnderlyingObservation | None) -> _Value:
    """Return the 30-day ATM implied volatility at the view's CLOSE snapshot."""
    if close is None:
        return _Value(None, "input_missing", ())
    inputs = _close_inputs([close])
    chains = _brackets(view.listed(root), view.at_ns)
    if chains is None:
        return _Value(None, "no_bracket", inputs)
    curve = view.curve(CMT_CURVE_ID)
    if curve is None:
        return _Value(None, "curve_unavailable", inputs)
    variances: list[float] = []
    for chain in chains:
        variance = _expiry_variance(view, chain, curve, float(close.value.value))
        inputs += variance.inputs
        if variance.value is None:
            return _Value(None, variance.reason, inputs)
        variances.append(variance.value)
    if len(chains) == 1:
        return _Value(math.sqrt(variances[0] / _T30), None, inputs)
    (first, second), (w1, w2) = chains, variances
    w30 = w1 + (w2 - w1) * (_T30 - first.t) / (second.t - first.t)
    return _Value(math.sqrt(w30 / _T30), None, inputs)


def _brackets(listed: Sequence[ContractVersion], at_ns: int) -> tuple[_Chain, ...] | None:
    """Return the expiries bracketing 30 days (one if an expiry is exactly at 30 days)."""
    by_expiry: dict[int, list[ContractVersion]] = {}
    for version in listed:
        by_expiry.setdefault(version.terms.expires_at_ns, []).append(version)
    live = sorted(expires for expires in by_expiry if expires > at_ns)
    below = [expires for expires in live if expires - at_ns <= _T30_NS]
    above = [expires for expires in live if expires - at_ns >= _T30_NS]
    if not below or not above:
        return None
    return tuple(
        _Chain((expires - at_ns) / _NS_PER_YEAR, tuple(by_expiry[expires]))
        for expires in sorted({below[-1], above[0]})
    )


def _expiry_variance(view: AsOfView, chain: _Chain, curve: DiscountCurve, spot: float) -> _Value:
    """Return the ATM total variance ``w = iv²·T`` of one expiry at its parity forward."""
    df = curve.df(chain.t * 365)
    if df is None:
        return _Value(None, "curve_unavailable", ())
    puts, calls = _valid_quotes(view, chain.contracts)
    inputs = tuple(
        (row.observation_id, row.available_at_ns) for row in (*puts.values(), *calls.values())
    )
    pairs = [
        ParityPair(strike, _mid(calls[strike]), _mid(puts[strike]))
        for strike in sorted(puts.keys() & calls.keys())
    ]
    forward = parity_forward(pairs, df, spot).value
    if forward is None:
        return _Value(None, "forward_unavailable", inputs)
    variance, reason = _atm_variance(chain, puts, calls, forward, df)
    return _Value(variance, reason, inputs)


def _atm_variance(
    chain: _Chain,
    puts: Mapping[float, QuoteObservation],
    calls: Mapping[float, QuoteObservation],
    forward: float,
    df: float,
) -> tuple[float | None, str | None]:
    """Return (total variance at ``ln(K/F) = 0``, None) or (None, reason).

    The OTM put at the largest strike below F and the OTM call at the smallest above it; at a
    listed exact-F strike, the mean of its put and call variances, both required.
    """
    strikes = {float(version.terms.strike.value) for version in chain.contracts}
    if forward in strikes:
        both = forward in puts and forward in calls
        low = high = forward if both else None
    else:
        low = max((strike for strike in puts if strike < forward), default=None)
        high = min((strike for strike in calls if strike > forward), default=None)
    if low is None or high is None:
        return None, "strike_unavailable"
    iv_low = implied_vol(_mid(puts[low]), forward, low, chain.t, df, OptionType.PUT).value
    iv_high = implied_vol(_mid(calls[high]), forward, high, chain.t, df, OptionType.CALL).value
    if iv_low is None or iv_high is None:
        return None, "iv_unavailable"
    w_low, w_high = iv_low**2 * chain.t, iv_high**2 * chain.t
    if low == high:
        return (w_low + w_high) / 2, None
    x_low, x_high = math.log(low / forward), math.log(high / forward)
    return w_low + (w_high - w_low) * (0.0 - x_low) / (x_high - x_low), None


def _valid_quotes(
    view: AsOfView, contracts: Sequence[ContractVersion]
) -> tuple[dict[float, QuoteObservation], dict[float, QuoteObservation]]:
    """Return (puts, calls) by strike: each contract's VALID quote at most 120 s old."""
    puts: dict[float, QuoteObservation] = {}
    calls: dict[float, QuoteObservation] = {}
    for version in contracts:
        row = view.quote(version.terms.contract_id, max_age_ns=_QUOTE_MAX_AGE_NS)
        if row is None or row.status() is not QuoteStatus.VALID:
            continue
        side = calls if version.terms.option_type is OptionType.CALL else puts
        side[float(version.terms.strike.value)] = row
    return puts, calls


def _mid(row: QuoteObservation) -> float:
    """Return the quote's mid in float64 (the exact sum, halved)."""
    with localcontext(EXACT):
        total = row.bid + row.ask
    return float(total) / 2
