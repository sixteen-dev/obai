"""The strategy's restricted as-of view of a frozen dataset (ADR 0002 §2, design §8.3).

An observation is visible at ``at_ns`` only if ``available_at_ns <= at_ns`` and its observed
instant ``<= at_ns``. Joins are backward only and never cross a session: the view belongs to
the one session whose ``[open_ns, cutoff_ns]`` holds ``at_ns``. Among visible observations the
latest is the maximum of ``(observed_at_ns, available_at_ns, observation_id)``, and it is
returned whatever its status: an invalid latest quote is never replaced by an older valid one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import (
    ActivityObservation,
    ContractVersion,
    CoveragePartition,
    FeatureObservation,
    QuoteObservation,
    SettlementObservation,
    TradingSession,
    UnderlyingObservation,
)
from options_backtest.reference.rates import DiscountCurve


@dataclass(frozen=True, slots=True)
class AsOfView:
    """What a decision at ``at_ns`` may see.

    Attributes:
        dataset: The frozen dataset.
        at_ns: The view's instant; must lie in a session's ``[open_ns, cutoff_ns]``.

    """

    dataset: FrozenDataset
    at_ns: int

    @property
    def session(self) -> TradingSession:
        """Return the table session with ``open_ns <= at_ns <= cutoff_ns``.

        Raises:
            ValueError: If ``at_ns`` lies in no session.

        """
        raise NotImplementedError

    def contract(self, contract_id: str) -> ContractVersion | None:
        """Return the contract's version known and effective at ``at_ns``.

        Known: ``known_from_ns <= at_ns``; effective: ``effective_from_ns <= at_ns`` and
        (``effective_to_ns`` is None or ``at_ns < effective_to_ns``).

        Args:
            contract_id: Contract id.

        Returns:
            The version; None if no version is known and effective.

        """
        raise NotImplementedError

    def listed(self, root: str) -> tuple[ContractVersion, ...]:
        """Return the root's listed contracts at ``at_ns``, sorted by contract id.

        A version is listed when it is known and effective (as ``contract``) and
        ``listed_at_ns <= at_ns <= terms.expires_at_ns``.

        Args:
            root: Option root.

        Returns:
            The listed versions; () when none.

        """
        raise NotImplementedError

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

        """
        raise NotImplementedError

    def index_value(self, underlying_id: str, *, max_age_ns: int) -> UnderlyingObservation | None:
        """Return the latest visible INDEX_VALUE of this session, if fresh enough.

        Args:
            underlying_id: Underlying index.
            max_age_ns: Largest allowed ``at_ns - observed_at_ns`` (inclusive), >= 0.

        Returns:
            The observation; None if there is none or it is older than ``max_age_ns``.

        """
        raise NotImplementedError

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
        raise NotImplementedError

    def settlement(self, series: str, session_date: date) -> SettlementObservation | None:
        """Return the final settlement of ``series`` for ``session_date``, if published.

        Args:
            series: Settlement series.
            session_date: Expiry session settled.

        Returns:
            The final observation with ``available_at_ns <= at_ns`` and the highest
            ``correction_version`` (then latest ``available_at_ns``); None if there is none.

        """
        raise NotImplementedError

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
        raise NotImplementedError

    def feature(self, feature_id: str, session_date: date) -> FeatureObservation | None:
        """Return a session's feature observation once all its inputs are available.

        Args:
            feature_id: ``{underlying_id}:{name}``.
            session_date: Session whose value is wanted (the entry gate asks for the prior
                table session).

        Returns:
            The observation (its ``value`` may be None) if ``max_input_available_at_ns <=
            at_ns``; None otherwise or when absent.

        """
        raise NotImplementedError

    def coverage(self, table: str, session_date: date) -> CoveragePartition | None:
        """Return the table's coverage partition for a session; dataset metadata, always visible.

        Args:
            table: Table name (the engine reads ``"quotes"``).
            session_date: Session.

        Returns:
            The partition; None when absent.

        """
        raise NotImplementedError
