"""The resolved run: a validated strategy bound to a window, a dataset and policies (ADR 0002 §6).

Design §9.1 separates the strategy from the run request. ``resolve`` binds the strategy's policy
ids through the registries below; an id WP3 cannot execute is a request error at its pointer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.errors import ErrorCode, Issue, SpecRejected, sorted_issues
from options_backtest.models.strategy import Product, StrategySpec
from options_backtest.models.strategy_checks import ValidatedStrategy
from options_backtest.money import Usd
from options_backtest.reference.products import product_rules

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
_MANIFEST_ID: Final = re.compile(r"[0-9a-f]{64}")


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
        TypeError: If ``spec`` is not a ``StrategySpec``.

    """
    if not isinstance(spec, StrategySpec):
        raise TypeError(f"resolve_policies spec must be a StrategySpec, got {type(spec).__name__}")
    issues = _policy_issues(spec)
    if issues:
        raise SpecRejected(sorted_issues(issues))
    return Policies(FEE_SCHEDULES[spec.fee_schedule_id], spec.funding_policy_id)


def _policy_issues(spec: StrategySpec) -> list[Issue]:
    """Return an INVALID_STRATEGY_RULE issue per policy id absent from its registry."""
    issues: list[Issue] = []
    if spec.fee_schedule_id not in FEE_SCHEDULES:
        issues.append(
            Issue(
                ErrorCode.INVALID_STRATEGY_RULE,
                f"unknown fee schedule {spec.fee_schedule_id!r}; WP3 executes only "
                f"{FLAT_FEE_SCHEDULE_ID!r}",
                "/fee_schedule_id",
            )
        )
    if spec.funding_policy_id not in FUNDING_POLICIES:
        issues.append(
            Issue(
                ErrorCode.INVALID_STRATEGY_RULE,
                f"unknown funding policy {spec.funding_policy_id!r}; WP3 executes only "
                f"{ZERO_INTEREST_FUNDING_ID!r}",
                "/funding_policy_id",
            )
        )
    return issues


def root_issues(product: Product) -> list[Issue]:
    """Return an UNSUPPORTED_PRODUCT issue per root whose underlying is not the product's.

    Design §9.1 item 1's deferred root check; public so the MCP validator can run it without a
    manifest (ADR 0003 §1.1), on the product block alone (§8).

    Args:
        product: A product whose every root is an R1 root (``root_membership_issues`` is empty).

    Returns:
        The issues, at ``/product/allowed_option_roots/{i}``, in root order.

    Raises:
        TypeError: If ``product`` is not a ``Product``.
        ValueError: If a root has no R1 product rules (``root_membership_issues`` names those).

    """
    if not isinstance(product, Product):
        raise TypeError(f"root_issues product must be a Product, got {type(product).__name__}")
    underlying = product.underlying_symbol
    issues: list[Issue] = []
    for index, root in enumerate(product.allowed_option_roots):
        rules_underlying = product_rules(root).underlying_id
        if rules_underlying == underlying:
            continue
        issues.append(
            Issue(
                ErrorCode.UNSUPPORTED_PRODUCT,
                f"root {root!r} trades underlying {rules_underlying!r}, not the product's "
                f"{underlying!r}",
                f"/product/allowed_option_roots/{index}",
            )
        )
    return issues


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
        TypeError: If an argument has the wrong type (dates must be exactly ``date``).

    """
    _check_request(strategy, start_date, end_date, manifest_id)
    issues = sorted_issues([*_policy_issues(strategy.spec), *root_issues(strategy.spec.product)])
    if issues:
        raise SpecRejected(issues)
    policies = resolve_policies(strategy.spec)
    return ResolvedRun(
        strategy=strategy,
        start_date=start_date,
        end_date=end_date,
        manifest_id=manifest_id,
        fee_schedule=policies.fee_schedule,
        policy_versions=(
            ("fee_schedule", policies.fee_schedule.schedule_id),
            ("funding_policy", policies.funding_policy_id),
        ),
        engine_version=ENGINE_VERSION,
    )


def _check_request(
    strategy: ValidatedStrategy, start_date: date, end_date: date, manifest_id: str
) -> None:
    """Refuse a request argument of the wrong type or value."""
    if not isinstance(strategy, ValidatedStrategy):
        raise TypeError(f"resolve strategy must be a ValidatedStrategy, got {strategy!r}")
    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if type(value) is not date:
            raise TypeError(f"resolve {name} must be exactly date, got {value!r}")
    if not isinstance(manifest_id, str):
        raise TypeError(f"resolve manifest_id must be a str, got {manifest_id!r}")
    if start_date > end_date:
        raise ValueError(f"resolve start_date {start_date} is after end_date {end_date}")
    if _MANIFEST_ID.fullmatch(manifest_id) is None:
        raise ValueError(f"resolve manifest_id must be 64 lower-case hex, got {manifest_id!r}")
