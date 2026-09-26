"""Historical entry features, computed once at dataset creation (ADR 0002 §4, design §13.2).

A feature is a §8.2 record: a session's value is computed at that session's CLOSE snapshot
through ``AsOfView(dataset, close_ns)`` and carries the latest ``available_at_ns`` of its inputs,
so it is visible from that instant, never at the same session's DEC (C04). The entry gate reads
the prior table session's value. All arithmetic is float64; values are ``Decimal(float)``. A
missing input gives ``value=None`` with a reason, never zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import FeatureObservation
from options_backtest.reference.products import ProductRules

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
    raise NotImplementedError


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
    raise NotImplementedError
