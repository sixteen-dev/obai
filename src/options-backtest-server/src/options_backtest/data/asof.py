"""The strategy's restricted as-of view of a frozen dataset (ADR 0002 §2, design §8.3).

An observation is visible at ``at_ns`` only if ``available_at_ns <= at_ns`` and its observed
instant ``<= at_ns``. Joins are backward only and never cross a session: the view belongs to
the one session whose ``[open_ns, cutoff_ns]`` holds ``at_ns``. Among visible observations the
latest is the maximum of ``(observed_at_ns, available_at_ns, observation_id)``, and it is
returned whatever its status: an invalid latest quote is never replaced by an older valid one.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from options_backtest.data.manifest import FrozenDataset
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
from options_backtest.models.market import require_id, require_type
from options_backtest.reference.rates import DiscountCurve, bill_df


@dataclass(frozen=True, slots=True)
class AsOfView:
    """What a decision at ``at_ns`` may see.

    Attributes:
        dataset: The frozen dataset.
        at_ns: The view's instant; must lie in a session's ``[open_ns, cutoff_ns]``.

    """

    dataset: FrozenDataset
    at_ns: int

    def __post_init__(self) -> None:
        """Require a frozen dataset and an int instant that lies in one of its sessions."""
        require_type(self.dataset, FrozenDataset, "AsOfView.dataset")
        if type(self.at_ns) is not int:
            raise TypeError(f"AsOfView.at_ns must be int, got {type(self.at_ns).__name__}")
        _session_at(self.dataset.sessions, self.at_ns)

    @property
    def session(self) -> TradingSession:
        """Return the table session with ``open_ns <= at_ns <= cutoff_ns``.

        Raises:
            ValueError: If ``at_ns`` lies in no session.

        """
        return _session_at(self.dataset.sessions, self.at_ns)

    def contract(self, contract_id: str) -> ContractVersion | None:
        """Return the contract's version known and effective at ``at_ns``.

        Known: ``known_from_ns <= at_ns``; effective: ``effective_from_ns <= at_ns`` and
        (``effective_to_ns`` is None or ``at_ns < effective_to_ns``).

        Args:
            contract_id: Contract id.

        Returns:
            The version; None if no version is known and effective.

        """
        require_id(contract_id, "AsOfView.contract contract_id")
        versions = _prefixed(self.dataset.contracts, f"{contract_id}@v", _version_key)
        return next(
            (
                version
                for version in versions
                if version.terms.contract_id == contract_id and _in_force(version, self.at_ns)
            ),
            None,
        )

    def listed(self, root: str) -> tuple[ContractVersion, ...]:
        """Return the root's listed contracts at ``at_ns``, sorted by contract id.

        A version is listed when it is known and effective (as ``contract``) and
        ``listed_at_ns <= at_ns <= terms.expires_at_ns``.

        Args:
            root: Option root.

        Returns:
            The listed versions; () when none.

        """
        require_id(root, "AsOfView.listed root")
        at_ns = self.at_ns
        listed = (
            version
            for version in _prefixed(self.dataset.contracts, f"{root}:", _version_key)
            if version.root == root
            and _in_force(version, at_ns)
            and version.listed_at_ns <= at_ns <= version.terms.expires_at_ns
        )
        return tuple(sorted(listed, key=lambda version: version.terms.contract_id))

    def quote(
        self, contract_id: str, *, max_age_ns: int, observed_after_ns: int | None = None
    ) -> QuoteObservation | None:
        """Return the contract's latest visible quote of this session, if fresh enough.

        Args:
            contract_id: Quoted contract.
            max_age_ns: Largest allowed ``at_ns - observed_at_ns`` (inclusive), >= 0.
            observed_after_ns: If given, the quote must satisfy ``observed_at_ns >
                observed_after_ns`` (strictly; a fill never uses its order's decision
                observation, C05).

        Returns:
            The latest visible observation with ``session_date`` = the view's session, any
            status; None if there is none, it is older than ``max_age_ns`` or it fails
            ``observed_after_ns``.

        Raises:
            TypeError: If an argument has the wrong type.
            ValueError: If ``contract_id`` is empty or ``max_age_ns < 0``.

        """
        require_id(contract_id, "AsOfView.quote contract_id")
        _require_max_age(max_age_ns)
        if observed_after_ns is not None and type(observed_after_ns) is not int:
            raise TypeError(
                f"observed_after_ns must be int, got {type(observed_after_ns).__name__}"
            )
        day = self.session.session_date
        rows = _prefixed(self.dataset.quotes, f"q:{contract_id}:{day.isoformat()}:", _row_id)
        latest = _latest_observed(
            (row for row in rows if row.contract_id == contract_id and row.session_date == day),
            self.at_ns,
        )
        if latest is None or self.at_ns - latest.observed_at_ns > max_age_ns:
            return None
        if observed_after_ns is not None and latest.observed_at_ns <= observed_after_ns:
            return None
        return latest

    def index_value(self, underlying_id: str, *, max_age_ns: int) -> UnderlyingObservation | None:
        """Return the latest visible INDEX_VALUE of this session, if fresh enough.

        Args:
            underlying_id: Underlying index.
            max_age_ns: Largest allowed ``at_ns - observed_at_ns`` (inclusive), >= 0.

        Returns:
            The observation; None if there is none or it is older than ``max_age_ns``.

        Raises:
            TypeError: If an argument has the wrong type.
            ValueError: If ``underlying_id`` is empty or ``max_age_ns < 0``.

        """
        require_id(underlying_id, "AsOfView.index_value underlying_id")
        _require_max_age(max_age_ns)
        day = self.session.session_date
        field = UnderlyingField.INDEX_VALUE
        prefix = f"u:{underlying_id}:{field.value}:{day.isoformat()}:"
        latest = _latest_observed(
            (
                row
                for row in _prefixed(self.dataset.underlying, prefix, _row_id)
                if row.underlying_id == underlying_id
                and row.field is field
                and row.session_date == day
            ),
            self.at_ns,
        )
        if latest is None or self.at_ns - latest.observed_at_ns > max_age_ns:
            return None
        return latest

    def activity(self, contract_id: str) -> ActivityObservation | None:
        """Return the contract's latest visible cumulative volume of this session.

        Visible: ``available_at_ns <= at_ns`` and ``measured_through_ns <= at_ns``; this session:
        ``measured_through_ns`` within the view's ``[open_ns, cutoff_ns]``. Latest: maximum
        ``(measured_through_ns, available_at_ns, observation_id)``.

        Args:
            contract_id: Contract measured.

        Returns:
            The observation; None when there is none.

        """
        require_id(contract_id, "AsOfView.activity contract_id")
        session, at_ns = self.session, self.at_ns
        visible = (
            row
            for row in _prefixed(self.dataset.activity, f"a:{contract_id}:", _row_id)
            if row.contract_id == contract_id
            and session.open_ns <= row.measured_through_ns <= min(at_ns, session.cutoff_ns)
            and row.available_at_ns <= at_ns
        )
        return max(
            visible,
            key=lambda row: (row.measured_through_ns, row.available_at_ns, row.observation_id),
            default=None,
        )

    def settlement(self, series: str, session_date: date) -> SettlementObservation | None:
        """Return the final settlement of ``series`` for ``session_date``, if published.

        Args:
            series: Settlement series.
            session_date: Expiry session settled.

        Returns:
            The final observation with ``available_at_ns <= at_ns`` and the highest
            ``correction_version`` (then latest ``available_at_ns``); None if there is none.

        """
        require_id(series, "AsOfView.settlement series")
        _require_day(session_date, "AsOfView.settlement session_date")
        prefix = f"s:{series}:{session_date.isoformat()}:c"
        published = (
            row
            for row in _prefixed(self.dataset.settlements, prefix, _row_id)
            if row.settlement_series == series
            and row.session_date == session_date
            and row.final
            and row.available_at_ns <= self.at_ns
        )
        return max(
            published,
            key=lambda row: (row.correction_version, row.available_at_ns, row.observation_id),
            default=None,
        )

    def curve(self, curve_id: str) -> DiscountCurve | None:
        """Return this session's discount curve.

        Uses rate observations with ``open_ns <= available_at_ns <= at_ns`` (a curve published
        in an earlier session is never carried); per tenor the latest by
        ``(available_at_ns, observation_date, observation_id)``, converted with ``bill_df``.

        Args:
            curve_id: Curve id, such as ``UST_CMT``.

        Returns:
            The curve over the tenors present, ascending; None when no tenor is present.

        """
        require_id(curve_id, "AsOfView.curve curve_id")
        open_ns, at_ns = self.session.open_ns, self.at_ns
        published = [
            row
            for row in _prefixed(self.dataset.rates, f"r:{curve_id}:", _row_id)
            if row.curve_id == curve_id and open_ns <= row.available_at_ns <= at_ns
        ]
        latest = {row.tenor_days: row for row in sorted(published, key=_rate_order)}
        if not latest:
            return None
        return DiscountCurve(
            tuple((tenor, bill_df(row.bey, tenor)) for tenor, row in sorted(latest.items()))
        )

    def feature(self, feature_id: str, session_date: date) -> FeatureObservation | None:
        """Return a session's feature observation once all its inputs are available.

        Args:
            feature_id: ``{underlying_id}:{name}``.
            session_date: Session whose value is wanted (the entry gate asks for the prior
                table session).

        Returns:
            The observation (its ``value`` may be None) if ``max_input_available_at_ns <=
            at_ns``; None otherwise or when absent.

        Raises:
            ValueError: If the dataset holds more than one version of the feature for the date.

        """
        require_id(feature_id, "AsOfView.feature feature_id")
        _require_day(session_date, "AsOfView.feature session_date")
        matches = [
            row
            for row in _prefixed(self.dataset.features, feature_id, _feature_id)
            if row.feature_id == feature_id and row.session_date == session_date
        ]
        if len(matches) > 1:
            versions = [row.feature_version for row in matches]
            raise ValueError(f"{feature_id} has versions {versions} for {session_date}")
        if not matches or matches[0].max_input_available_at_ns > self.at_ns:
            return None
        return matches[0]

    def coverage(self, table: str, session_date: date) -> CoveragePartition | None:
        """Return the table's coverage partition for a session; dataset metadata, always visible.

        Args:
            table: Table name (the engine reads ``"quotes"``).
            session_date: Session.

        Returns:
            The partition; None when absent.

        """
        require_id(table, "AsOfView.coverage table")
        _require_day(session_date, "AsOfView.coverage session_date")
        rows = self.dataset.coverage
        index = bisect_left(rows, (table, session_date), key=_coverage_key)
        if index < len(rows) and _coverage_key(rows[index]) == (table, session_date):
            return rows[index]
        return None


def _session_at(sessions: Sequence[TradingSession], at_ns: int) -> TradingSession:
    """Return the session holding ``at_ns``; ``freeze`` keeps sessions disjoint and ordered."""
    index = bisect_right(sessions, at_ns, key=lambda session: session.open_ns) - 1
    if index < 0 or at_ns > sessions[index].cutoff_ns:
        raise ValueError(f"AsOfView.at_ns {at_ns} lies in no session's [open_ns, cutoff_ns]")
    return sessions[index]


def _prefixed[R](rows: Sequence[R], prefix: str, key: Callable[[R], str]) -> Iterator[R]:
    """Yield the rows whose key starts with ``prefix``; ``rows`` are sorted by ``key``."""
    for index in range(bisect_left(rows, prefix, key=key), len(rows)):
        row = rows[index]
        if not key(row).startswith(prefix):
            return
        yield row


def _latest_observed[O: (QuoteObservation, UnderlyingObservation)](
    rows: Iterable[O], at_ns: int
) -> O | None:
    """Return the maximum ``(observed_at, available_at, id)`` of the rows visible at ``at_ns``."""
    visible = (row for row in rows if row.available_at_ns <= at_ns and row.observed_at_ns <= at_ns)
    return max(
        visible,
        key=lambda row: (row.observed_at_ns, row.available_at_ns, row.observation_id),
        default=None,
    )


def _in_force(version: ContractVersion, at_ns: int) -> bool:
    """Return whether the version is known and effective at ``at_ns``."""
    ended = version.effective_to_ns is not None and at_ns >= version.effective_to_ns
    return version.known_from_ns <= at_ns and version.effective_from_ns <= at_ns and not ended


def _require_max_age(max_age_ns: object) -> None:
    if type(max_age_ns) is not int:
        raise TypeError(f"max_age_ns must be int, got {type(max_age_ns).__name__}")
    if max_age_ns < 0:
        raise ValueError(f"max_age_ns must be >= 0, got {max_age_ns}")


def _require_day(value: object, field: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{field} must be exactly date, got {type(value).__name__}")


def _version_key(version: ContractVersion) -> str:
    return version.version_id


def _row_id(
    row: QuoteObservation
    | UnderlyingObservation
    | ActivityObservation
    | SettlementObservation
    | RateObservation,
) -> str:
    return row.observation_id


def _feature_id(row: FeatureObservation) -> str:
    return row.feature_id


def _coverage_key(row: CoveragePartition) -> tuple[str, date]:
    return (row.table, row.session_date)


def _rate_order(row: RateObservation) -> tuple[int, date, str]:
    return (row.available_at_ns, row.observation_date, row.observation_id)
