"""The resolved run: a validated strategy bound to a window, a dataset and policies (ADR 0002 §6).

Design §9.1 separates the strategy from the run request. ``resolve`` binds the strategy's policy
ids through the registries below; an id WP3 cannot execute is a request error at its pointer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.models.strategy import StrategySpec
from options_backtest.models.strategy_checks import ValidatedStrategy
from options_backtest.money import Usd

ENGINE_VERSION: Final = "0.1.0"
"""Engine version recorded in results; equals the service ``VERSION`` file."""
FLAT_FEE_SCHEDULE_ID: Final = "illustrative_flat_1usd_per_contract_side_v1"
ZERO_INTEREST_FUNDING_ID: Final = "illustrative_zero_interest_no_borrow_v1"
FEE_SCHEDULES: Final[Mapping[str, AssumedFlatFeeSchedule]] = MappingProxyType(
    {
        FLAT_FEE_SCHEDULE_ID: AssumedFlatFeeSchedule(
            schedule_id=FLAT_FEE_SCHEDULE_ID,
            trade_per_contract=Usd(Decimal("1.00")),
            exercise_assignment_per_contract=Usd(Decimal("0.00")),
            cash_settlement_per_contract=Usd(Decimal("0.00")),
        )
    }
)
"""Fee schedule registry: $1.00 per contract per traded side, $0 exercise and settlement."""
FUNDING_POLICIES: Final = frozenset({ZERO_INTEREST_FUNDING_ID})
"""Funding policy registry: zero interest on cash, no borrowing (design §11.4)."""


@dataclass(frozen=True, slots=True)
class Policies:
    """The policies a strategy's ids bind to.

    Attributes:
        fee_schedule: Bound fee schedule.
        funding_policy_id: Bound funding policy (zero interest: no financing postings).

    """

    fee_schedule: AssumedFlatFeeSchedule
    funding_policy_id: str


def resolve_policies(spec: StrategySpec) -> Policies:
    """Bind ``fee_schedule_id`` and ``funding_policy_id`` through the registries.

    ``comparison_policy_id`` is WP4's and is not bound here.

    Args:
        spec: The strategy.

    Returns:
        The bound policies.

    Raises:
        SpecRejected: With every INVALID_STRATEGY_RULE issue, at ``/fee_schedule_id`` and
            ``/funding_policy_id``, for an id absent from its registry.

    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ResolvedRun:
    """Everything that determines a run besides the dataset's bytes.

    Attributes:
        strategy: The validated strategy.
        start_date: First window session.
        end_date: Final window session.
        manifest_id: Manifest id of the dataset the run must use.
        fee_schedule: Bound fee schedule.
        policy_versions: (``"fee_schedule"``, id) and (``"funding_policy"``, id), in that order.
        engine_version: ``ENGINE_VERSION``.

    """

    strategy: ValidatedStrategy
    start_date: date
    end_date: date
    manifest_id: str
    fee_schedule: AssumedFlatFeeSchedule
    policy_versions: tuple[tuple[str, str], ...]
    engine_version: str


def resolve(
    strategy: ValidatedStrategy, *, start_date: date, end_date: date, manifest_id: str
) -> ResolvedRun:
    """Bind a validated strategy to a window, a dataset and its policies.

    Session membership of the dates is checked by the run against the dataset's table.

    Args:
        strategy: The validated strategy.
        start_date: First window date.
        end_date: Final window date, >= ``start_date``.
        manifest_id: 64 lower-case hex characters.

    Returns:
        The resolved run.

    Raises:
        ValueError: If ``start_date > end_date`` or ``manifest_id`` is malformed.
        SpecRejected: With every issue of ``resolve_policies``, plus UNSUPPORTED_PRODUCT at
            ``/product/allowed_option_roots/{i}`` for a root whose ``ProductRules.underlying_id``
            is not ``product.underlying_symbol`` (design §9.1 item 1's deferred root check).

    """
    raise NotImplementedError
