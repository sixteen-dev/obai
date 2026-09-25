"""Static semantic checks of design §9.1 (ADR 0001 §4 step 7 and its table).

Only what the specification alone decides is checked here; the table's "Deferred" column
(product rules, strike existence, session counting, capital against package risk) needs
reference or market data and belongs to WP2/WP3.

Strike relations: a leg selected by ``strike_offset`` sits at an exact distance from its
anchor, so legs chained to the same independently selected root have a fixed strike order.
Only those fixed relations are judged; independently selected strikes are left to selection,
even with inverted targets: a target and its tolerance fix an interval, not a strike (ADR 0001
§11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from itertools import combinations
from types import MappingProxyType
from typing import Final

from options_backtest.errors import ErrorCode, Issue, SpecRejected, sorted_issues
from options_backtest.models.strategy import (
    DeltaStrike,
    Exits,
    FixedContracts,
    Leg,
    MoneynessStrike,
    SameAsExpiry,
    SequentialRoll,
    StrategySpec,
    StrikeOffset,
    StrikeSelection,
    TargetDteExpiry,
)
from options_backtest.money import EXACT

R1_OPTION_ROOTS: Final = frozenset({"SPXW", "XSP"})

type _Positions = dict[int, tuple[int, Decimal]]  # leg index -> (root leg index, offset)


class PremiumDirection(StrEnum):
    """Sign of the opening package premium: §10.3's declared direction, ``R1Campaign.kind``."""

    CREDIT = "credit"
    DEBIT = "debit"


_FIXED_DIRECTIONS: Final = MappingProxyType(
    {
        "single_long": PremiumDirection.DEBIT,
        "iron_condor": PremiumDirection.CREDIT,
        "long_straddle": PremiumDirection.DEBIT,
        "long_strangle": PremiumDirection.DEBIT,
    }
)
_BASIS_DIRECTIONS: Final = MappingProxyType(
    {"initial_credit": PremiumDirection.CREDIT, "initial_debit": PremiumDirection.DEBIT}
)
_LEG_COUNTS: Final = MappingProxyType(
    {"single_long": 1, "vertical": 2, "iron_condor": 4, "long_straddle": 2, "long_strangle": 2}
)
# Roles in ascending strike order.
_CONDOR_ROLES: Final = (("buy", "put"), ("sell", "put"), ("sell", "call"), ("buy", "call"))
_LONG_PUT_CALL_ROLES: Final = (("buy", "put"), ("buy", "call"))


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of ``check_strategy``.

    Attributes:
        issues: Every issue found, sorted by (pointer, code); empty when the strategy passes.
        leg_order: Leg ids with every anchor before its dependents; empty when the anchor
            graph is invalid.
        premium_direction: The opening premium's direction; None when it is undetermined.

    """

    issues: tuple[Issue, ...]
    leg_order: tuple[str, ...]
    premium_direction: PremiumDirection | None


@dataclass(frozen=True, slots=True)
class ValidatedStrategy:
    """A strategy that passed every static check, carrying the facts the checks derived.

    Construction re-runs ``check_strategy`` (stage 7), so a spec that fails it cannot exist as
    this type. Stages 1-6 are the spec's own: ``StrategySpec`` values come only from
    ``model_validate`` (``load_strategy``), since spec models refuse ``model_copy`` updates;
    ``model_construct`` is pydantic's trusted-data escape hatch and is never used on specs.

    Attributes:
        spec: The strategy.
        leg_order: Resolution order: every anchor before its dependents (§9.2 step 4).
        premium_direction: The direction an opening fill must have (§10.3); always derived,
            since an undetermined direction is a rejection (ADR 0001 §4 row 7).

    """

    spec: StrategySpec
    leg_order: tuple[str, ...]
    premium_direction: PremiumDirection

    def __post_init__(self) -> None:
        """Re-run the checks and require the stated facts to be theirs."""
        if not isinstance(self.spec, StrategySpec):
            raise TypeError(
                f"ValidatedStrategy needs a StrategySpec, got {type(self.spec).__name__}"
            )
        result = check_strategy(self.spec)
        if result.issues:
            raise SpecRejected(result.issues)
        if (self.leg_order, self.premium_direction) != (result.leg_order, result.premium_direction):
            raise ValueError(
                "leg_order and premium_direction must equal check_strategy's "
                f"{result.leg_order} and {result.premium_direction}"
            )


def check_strategy(spec: StrategySpec) -> CheckResult:
    """Run every static §9.1 check on a syntactically valid strategy.

    Args:
        spec: The strategy.

    Returns:
        Every issue sorted by (pointer, code), the leg resolution order and the premium
        direction.

    Raises:
        TypeError: If ``spec`` is not a ``StrategySpec``.

    """
    if not isinstance(spec, StrategySpec):
        raise TypeError(f"check_strategy needs a StrategySpec, got {type(spec).__name__}")
    graph_issues, leg_order = _check_leg_graph(spec.legs)
    positions = _strike_positions(spec.legs, leg_order)
    direction_issues, direction = _premium_direction(spec, positions)
    issues = [
        *_check_roots(spec),
        *_check_delta_signs(spec.legs),
        *graph_issues,
        *_check_offsetting_legs(spec.legs, positions),
        *_check_structure(spec, positions),
        *_check_entry_windows(spec),
        *direction_issues,
        *_check_exit_bases(spec.exits, direction),
        *_check_capital(spec),
        *_check_open_interest_and_roll(spec),
    ]
    return CheckResult(sorted_issues(issues), leg_order, direction)


# Row 1: product roots --------------------------------------------------------------------


def _check_roots(spec: StrategySpec) -> list[Issue]:
    return [
        Issue(
            ErrorCode.UNSUPPORTED_PRODUCT, _root_message(root), f"/product/allowed_option_roots/{i}"
        )
        for i, root in enumerate(spec.product.allowed_option_roots)
        if root not in R1_OPTION_ROOTS
    ]


def _root_message(root: str) -> str:
    if root == "SPX":
        return "SPX options are AM-settled; R1 supports only the PM-settled roots SPXW and XSP"
    return f"option root {root!r} is not supported; R1 supports only SPXW and XSP"


# Row 2: signed delta ---------------------------------------------------------------------


def _check_delta_signs(legs: Sequence[Leg]) -> list[Issue]:
    return [
        Issue(
            ErrorCode.INVALID_SELECTOR,
            f"a {leg.option_type} target_delta must be {_delta_sign(leg)}: it is the signed "
            f"long-option spot delta, got {leg.strike_selection.target_delta}",
            f"/legs/{index}/strike_selection/target_delta",
        )
        for index, leg in enumerate(legs)
        if isinstance(leg.strike_selection, DeltaStrike)
        and (leg.strike_selection.target_delta > 0) != (leg.option_type == "call")
    ]


def _delta_sign(leg: Leg) -> str:
    return "positive" if leg.option_type == "call" else "negative"


# Rows 1 and 3: leg ids, anchors, the single expiry target, leg-level acyclicity -------------


def _anchors(leg: Leg, index: int) -> list[tuple[str, str]]:
    """Return (pointer, anchor leg id) for each anchor the leg names."""
    anchors = []
    if isinstance(leg.expiry_selection, SameAsExpiry):
        pointer = f"/legs/{index}/expiry_selection/anchor_leg_id"
        anchors.append((pointer, leg.expiry_selection.anchor_leg_id))
    if isinstance(leg.strike_selection, StrikeOffset):
        pointer = f"/legs/{index}/strike_selection/anchor_leg_id"
        anchors.append((pointer, leg.strike_selection.anchor_leg_id))
    return anchors


def _check_leg_graph(legs: Sequence[Leg]) -> tuple[list[Issue], tuple[str, ...]]:
    reference_issues = [*_check_leg_ids(legs), *_check_anchor_targets(legs)]
    target_issues = _check_expiry_target(legs)
    if reference_issues:
        return [*reference_issues, *target_issues], ()
    order, unresolved = _resolution_order(legs)
    cycle_issues = [
        Issue(
            ErrorCode.INVALID_SELECTOR,
            f"leg {legs[index].leg_id!r} is on or behind an anchor cycle",
            f"/legs/{index}",
        )
        for index in unresolved
    ]
    return [*target_issues, *cycle_issues], () if unresolved else order


def _check_leg_ids(legs: Sequence[Leg]) -> list[Issue]:
    return [
        Issue(
            ErrorCode.INVALID_SELECTOR, f"leg_id {leg.leg_id!r} is not unique", f"/legs/{i}/leg_id"
        )
        for i, leg in enumerate(legs)
        if leg.leg_id in {earlier.leg_id for earlier in legs[:i]}
    ]


def _check_anchor_targets(legs: Sequence[Leg]) -> list[Issue]:
    leg_ids = {leg.leg_id for leg in legs}
    return [
        Issue(ErrorCode.INVALID_SELECTOR, _anchor_message(leg.leg_id, anchor), pointer)
        for index, leg in enumerate(legs)
        for pointer, anchor in _anchors(leg, index)
        if anchor == leg.leg_id or anchor not in leg_ids
    ]


def _anchor_message(leg_id: str, anchor: str) -> str:
    if anchor == leg_id:
        return f"leg {leg_id!r} cannot anchor to itself"
    return f"anchor {anchor!r} names no leg"


def _check_expiry_target(legs: Sequence[Leg]) -> list[Issue]:
    targets = [i for i, leg in enumerate(legs) if isinstance(leg.expiry_selection, TargetDteExpiry)]
    if not targets:
        message = "exactly one leg must select its expiry by target_dte; the others use same_as"
        return [Issue(ErrorCode.INVALID_SELECTOR, message, "/legs")]
    message = "only one leg may select its expiry by target_dte; chain this one with same_as"
    return [
        Issue(ErrorCode.INVALID_SELECTOR, message, f"/legs/{index}/expiry_selection")
        for index in targets[1:]
    ]


def _resolution_order(legs: Sequence[Leg]) -> tuple[tuple[str, ...], list[int]]:
    """Resolve whole legs anchors-first, lowest index first; return the order and leftovers."""
    dependencies = [{anchor for _, anchor in _anchors(leg, i)} for i, leg in enumerate(legs)]
    order: list[str] = []
    pending = list(range(len(legs)))
    for _ in range(len(legs)):  # one leg per round; a round with nothing ready means a cycle
        ready = [index for index in pending if dependencies[index] <= set(order)]
        if not ready:
            break
        order.append(legs[ready[0]].leg_id)
        pending.remove(ready[0])
    return tuple(order), pending


def _strike_positions(legs: Sequence[Leg], leg_order: tuple[str, ...]) -> _Positions:
    """Place each leg's strike relative to its root leg; empty when the graph is invalid."""
    index_of = {leg.leg_id: index for index, leg in enumerate(legs)}
    positions: _Positions = {}
    for leg_id in leg_order:
        index = index_of[leg_id]
        selection = legs[index].strike_selection
        if not isinstance(selection, StrikeOffset):
            positions[index] = (index, Decimal(0))
            continue
        root, offset = positions[index_of[selection.anchor_leg_id]]
        with localcontext(EXACT):
            positions[index] = (root, offset + selection.offset_price_units)
    return positions


def _fixed_offsets(
    positions: _Positions, first: int, second: int
) -> tuple[Decimal, Decimal] | None:
    """Both legs' offsets from a shared root, or None when their strikes are independent."""
    if first not in positions or second not in positions:
        return None
    (first_root, first_offset), (second_root, second_offset) = positions[first], positions[second]
    return (first_offset, second_offset) if first_root == second_root else None


def _check_offsetting_legs(legs: Sequence[Leg], positions: _Positions) -> list[Issue]:
    return [
        Issue(
            ErrorCode.INVALID_SELECTOR,
            f"legs {first.leg_id!r} and {second.leg_id!r} buy and sell the same "
            f"{first.option_type}: their strikes are at net offset 0",
            f"/legs/{_offset_leg(legs, i, j)}/strike_selection",
        )
        for (i, first), (j, second) in combinations(enumerate(legs), 2)
        if first.option_type == second.option_type
        and first.side != second.side
        and _equal_strikes(_fixed_offsets(positions, i, j))
    ]


def _offset_leg(legs: Sequence[Leg], first: int, second: int) -> int:
    """Return the leg of an offset-fixed pair carrying the strike_offset (second if both do)."""
    return second if isinstance(legs[second].strike_selection, StrikeOffset) else first


def _equal_strikes(offsets: tuple[Decimal, Decimal] | None) -> bool:
    return offsets is not None and offsets[0] == offsets[1]


# Row 4: leg composition and fixed strike order per structure ----------------------------------


def _check_structure(spec: StrategySpec, positions: _Positions) -> list[Issue]:
    legs, structure = spec.legs, spec.structure
    expected = _LEG_COUNTS[structure]
    if len(legs) != expected:
        message = f"{structure} needs {expected} legs, got {len(legs)}"
        return [Issue(ErrorCode.UNSUPPORTED_STRUCTURE, message, "/legs")]
    if structure == "single_long":
        return _check_single_long(legs)
    if structure == "vertical":
        return _check_vertical(legs)
    if structure == "long_straddle":
        return _check_straddle(legs, positions)
    roles = _CONDOR_ROLES if structure == "iron_condor" else _LONG_PUT_CALL_ROLES
    return _check_ordered_roles(structure, legs, roles, positions)


def _check_single_long(legs: Sequence[Leg]) -> list[Issue]:
    if legs[0].side == "buy":
        return []
    return [Issue(ErrorCode.UNSUPPORTED_STRUCTURE, "single_long needs a buy leg", "/legs/0/side")]


def _check_vertical(legs: Sequence[Leg]) -> list[Issue]:
    first, second = legs
    issues = []
    if first.side == second.side:
        message = "vertical legs must have opposite sides"
        issues.append(Issue(ErrorCode.UNSUPPORTED_STRUCTURE, message, "/legs/1/side"))
    if first.option_type != second.option_type:
        message = "vertical legs must have the same option type"
        issues.append(Issue(ErrorCode.UNSUPPORTED_STRUCTURE, message, "/legs/1/option_type"))
    return issues


def _role_indices(legs: Sequence[Leg], roles: tuple[tuple[str, str], ...]) -> list[int] | None:
    """Leg index for each (side, option_type) role, or None if the legs do not fill them."""
    keys: list[tuple[str, str]] = [(leg.side, leg.option_type) for leg in legs]
    if sorted(keys) != sorted(roles):
        return None
    return [keys.index(role) for role in roles]


def _composition_issue(structure: str, roles: tuple[tuple[str, str], ...]) -> Issue:
    wanted = ", ".join(f"{side} {option_type}" for side, option_type in roles)
    return Issue(ErrorCode.UNSUPPORTED_STRUCTURE, f"{structure} needs legs: {wanted}", "/legs")


def _check_ordered_roles(
    structure: str, legs: Sequence[Leg], roles: tuple[tuple[str, str], ...], positions: _Positions
) -> list[Issue]:
    """Require the roles present and every offset-fixed pair in ascending strike order."""
    indices = _role_indices(legs, roles)
    if indices is None:
        return [_composition_issue(structure, roles)]
    return [
        Issue(
            ErrorCode.UNSUPPORTED_STRUCTURE,
            f"{structure} needs {legs[low].leg_id!r} strike below {legs[high].leg_id!r} strike",
            f"/legs/{_offset_leg(legs, low, high)}/strike_selection",
        )
        for low, high in combinations(indices, 2)
        if (offsets := _fixed_offsets(positions, low, high)) is not None
        and offsets[1] <= offsets[0]
    ]


def _check_straddle(legs: Sequence[Leg], positions: _Positions) -> list[Issue]:
    indices = _role_indices(legs, _LONG_PUT_CALL_ROLES)
    if indices is None:
        return [_composition_issue("long_straddle", _LONG_PUT_CALL_ROLES)]
    put, call = indices
    if put not in positions or call not in positions:
        return []  # unresolved legs: the anchor-graph issue already rejects the spec
    if _equal_strikes(_fixed_offsets(positions, put, call)):
        return []
    message = "long_straddle needs one leg anchored to the other by a strike_offset of 0"
    return [Issue(ErrorCode.UNSUPPORTED_STRUCTURE, message, "/legs")]


# Rows 5 and 9: entry DTE window ----------------------------------------------------------


def _check_entry_windows(spec: StrategySpec) -> list[Issue]:
    return [
        issue
        for index, leg in enumerate(spec.legs)
        if isinstance(leg.expiry_selection, TargetDteExpiry)
        for issue in _window_issues(leg.expiry_selection, f"/legs/{index}/expiry_selection", spec)
    ]


def _window_issues(window: TargetDteExpiry, pointer: str, spec: StrategySpec) -> list[Issue]:
    """Report a target outside its window, or a window no candidate can ever enter."""
    issues = []
    if not window.min_dte <= window.target_dte <= window.max_dte:
        message = (
            f"target_dte {window.target_dte} must lie within "
            f"[min_dte {window.min_dte}, max_dte {window.max_dte}]"
        )
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, f"{pointer}/target_dte"))
    exit_dte = spec.exits.exit_dte
    if window.max_dte <= exit_dte:
        message = f"no entry can qualify: max_dte {window.max_dte} <= exits.exit_dte {exit_dte}"
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, f"{pointer}/max_dte"))
    roll = spec.roll
    if isinstance(roll, SequentialRoll) and window.max_dte <= roll.trigger_dte:
        message = (
            f"no entry can qualify: max_dte {window.max_dte} <= roll.trigger_dte {roll.trigger_dte}"
        )
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, f"{pointer}/max_dte"))
    return issues


# Row 7: premium direction and exit bases -------------------------------------------------


def _premium_direction(
    spec: StrategySpec, positions: _Positions
) -> tuple[list[Issue], PremiumDirection | None]:
    """Derive the direction: fixed per structure; a vertical's from selectors or exit bases."""
    fixed = _FIXED_DIRECTIONS.get(spec.structure)
    if fixed is not None:
        return [], fixed
    legs = spec.legs
    if len(legs) != _LEG_COUNTS["vertical"] or _check_vertical(legs):
        return [], None  # not a vertical pair: the structure issue already rejects the spec
    pricier = _pricier_vertical_leg(legs, positions)
    if pricier is not None:
        bought = legs[pricier].side == "buy"
        return [], PremiumDirection.DEBIT if bought else PremiumDirection.CREDIT
    issues, direction = _direction_from_exit_bases(spec.exits)
    if len(positions) < len(legs):
        return [], direction  # unresolved legs: the anchor-graph issue already rejects the spec
    return issues, direction


def _pricier_vertical_leg(legs: Sequence[Leg], positions: _Positions) -> int | None:
    """Index of the leg with the higher premium, when the selectors fix it.

    Same type and expiry: a higher strike is pricier for a put and a lower one for a call; a
    larger |delta| is pricier for both.
    """
    first, second = legs
    higher_strike_is_pricier = first.option_type == "put"
    offsets = _fixed_offsets(positions, 0, 1)
    if offsets is not None:
        return _pricier(*offsets, higher_is_pricier=higher_strike_is_pricier)
    return _pricier_by_selector(
        first.strike_selection, second.strike_selection, higher_strike_is_pricier
    )


def _pricier_by_selector(
    first: StrikeSelection, second: StrikeSelection, higher_strike_is_pricier: bool
) -> int | None:
    if isinstance(first, DeltaStrike) and isinstance(second, DeltaStrike):
        first_size, second_size = first.target_delta.copy_abs(), second.target_delta.copy_abs()
        return _pricier(first_size, second_size, higher_is_pricier=True)
    if isinstance(first, MoneynessStrike) and isinstance(second, MoneynessStrike):
        return _pricier(
            first.target_strike_to_spot,
            second.target_strike_to_spot,
            higher_is_pricier=higher_strike_is_pricier,
        )
    return None


def _pricier(first: Decimal, second: Decimal, *, higher_is_pricier: bool) -> int | None:
    """Index (0 or 1) of the pricier leg from each leg's ordering key; None on a tie."""
    if first == second:
        return None
    return 0 if (first > second) == higher_is_pricier else 1


def _direction_from_exit_bases(exits: Exits) -> tuple[list[Issue], PremiumDirection | None]:
    bases = {rule.basis for rule in (exits.take_profit, exits.stop_loss) if rule is not None}
    if len(bases) == 1:
        return [], _BASIS_DIRECTIONS[bases.pop()]
    message = (
        "vertical premium direction is undetermined: the strike selectors do not order the "
        "legs' premiums and the exit rules do not declare one basis"
    )
    return [Issue(ErrorCode.INVALID_STRATEGY_RULE, message, "/legs")], None


def _check_exit_bases(exits: Exits, direction: PremiumDirection | None) -> list[Issue]:
    if direction is None:
        return []
    rules = (("take_profit", exits.take_profit), ("stop_loss", exits.stop_loss))
    return [
        Issue(
            ErrorCode.INVALID_STRATEGY_RULE,
            f"{name}.basis {rule.basis!r} contradicts the package's {direction} premium",
            f"/exits/{name}/basis",
        )
        for name, rule in rules
        if rule is not None and _BASIS_DIRECTIONS[rule.basis] is not direction
    ]


# Rows 8, 9 and 10: capital, sizing, liquidity, open interest, roll ----------------------------


def _check_capital(spec: StrategySpec) -> list[Issue]:
    account, sizing = spec.account, spec.sizing
    order_cap = spec.execution.max_contracts_per_order
    issues = []
    if account.initial_cash_usd.amount <= 0:
        message = "initial_cash_usd must be > 0"
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, "/account/initial_cash_usd"))
    if not account.initial_cash_usd.is_cents():
        message = "initial_cash_usd must be whole cents: the opening deposit posts to CASH"
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, "/account/initial_cash_usd"))
    if account.max_campaign_risk_fraction > account.max_total_risk_fraction:
        message = "max_campaign_risk_fraction must not exceed max_total_risk_fraction"
        pointer = "/account/max_campaign_risk_fraction"
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, pointer))
    if isinstance(sizing, FixedContracts) and sizing.contracts > order_cap:
        message = (
            f"contracts {sizing.contracts} exceeds execution.max_contracts_per_order {order_cap}"
        )
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, "/sizing/contracts"))
    if spec.liquidity.max_absolute_spread_price_units.value == 0:
        message = "max_absolute_spread_price_units must be > 0"
        pointer = "/liquidity/max_absolute_spread_price_units"
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, pointer))
    return issues


def _check_open_interest_and_roll(spec: StrategySpec) -> list[Issue]:
    issues = []
    if spec.liquidity.min_open_interest != 0:
        issues.append(
            Issue(
                ErrorCode.DATA_ENTITLEMENT_MISSING,
                "R1 has no historical open interest source, so min_open_interest must be 0",
                "/liquidity/min_open_interest",
                missing_capability="historical_open_interest",
                remediation="set liquidity.min_open_interest to 0",
            )
        )
    roll, exit_dte = spec.roll, spec.exits.exit_dte
    if isinstance(roll, SequentialRoll) and roll.trigger_dte <= exit_dte:
        message = (
            f"roll.trigger_dte {roll.trigger_dte} must exceed exits.exit_dte {exit_dte}: "
            "exit takes precedence, so the roll could never fire"
        )
        issues.append(Issue(ErrorCode.INVALID_STRATEGY_RULE, message, "/roll/trigger_dte"))
    return issues
