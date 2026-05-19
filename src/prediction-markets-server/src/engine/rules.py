"""V1 prediction-market rule schema (§10.4 + §11.1).

Rule validation is strict: extra fields raise rather than silently being
ignored, and unsupported enum values are rejected. The agent is expected to
translate the user's free text into a structured rule, but the server
executes only validated structures (§11.1).

Add new V2 fields by widening the frozensets at the top of the file plus
the corresponding Pydantic model — `extra="forbid"` then enforces parity at
the boundary.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Supported sides — V1 ships only YES because both lifetime-volume
# contamination and entry-price filters are defined against YES prices in
# the current store. NO can join when entry math is mirrored.
SUPPORTED_SIDES: Final[frozenset[Literal["YES"]]] = frozenset({"YES"})

# Supported exit types. hold_to_resolution is the baseline; stop_take_profit
# adds stop, take-profit, and max-hold triggers on top of sampled price
# history. Both share the §10.4 response contract.
SupportedExitType = Literal["hold_to_resolution", "stop_take_profit"]
SUPPORTED_EXIT_TYPES: Final[frozenset[SupportedExitType]] = frozenset(
    {"hold_to_resolution", "stop_take_profit"}
)

# Supported volume_filter_mode values. The "lifetime_static" label names
# the §11.4 contamination so the response can echo it verbatim.
SUPPORTED_VOLUME_FILTER_MODES: Final[frozenset[Literal["lifetime_static", "none"]]] = frozenset(
    {"lifetime_static", "none"}
)


class EntryRule(BaseModel):
    """Inclusive price band the YES outcome must fall inside at entry."""

    model_config = ConfigDict(extra="forbid")

    price_min: float = Field(ge=0.0, le=1.0)
    price_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_band_order(self) -> EntryRule:
        if self.price_min > self.price_max:
            msg = f"price_min ({self.price_min}) must be ≤ price_max ({self.price_max})"
            raise ValueError(msg)
        return self


class HoldToResolutionExit(BaseModel):
    """Exit at the terminal payoff of the resolved market.

    Original V1 behavior. Exit price is 1.0 (YES win) or 0.0 (YES loss) and
    exit timestamp is ``market.end_date``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["hold_to_resolution"]


class StopTakeProfitExit(BaseModel):
    """Exit on the first sampled price that crosses a stop, take-profit, or max-hold trigger.

    All three triggers are optional; at least one must be provided
    (otherwise the rule is equivalent to ``hold_to_resolution`` and should
    declare itself that way). Trigger evaluation uses observed sampled
    prices — intra-bucket paths between samples are unobserved and may
    miss trigger crossings; see ``fidelity_${N}min_undercounts_triggers``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["stop_take_profit"]
    stop_price: float | None = Field(default=None, gt=0.0, lt=1.0)
    take_profit_price: float | None = Field(default=None, gt=0.0, lt=1.0)
    max_hold_days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_at_least_one_trigger(self) -> StopTakeProfitExit:
        if (
            self.stop_price is None
            and self.take_profit_price is None
            and self.max_hold_days is None
        ):
            msg = (
                "stop_take_profit needs at least one of stop_price / "
                "take_profit_price / max_hold_days; use hold_to_resolution instead."
            )
            raise ValueError(msg)
        if (
            self.stop_price is not None
            and self.take_profit_price is not None
            and self.stop_price >= self.take_profit_price
        ):
            msg = (
                f"stop_price ({self.stop_price}) must be strictly below "
                f"take_profit_price ({self.take_profit_price})."
            )
            raise ValueError(msg)
        return self


# Discriminated union — ``type`` selects the variant. ``hold_to_resolution``
# stays the implicit default for legacy callers; ``stop_take_profit`` adds
# intermediate exits without changing the response contract shape.
ExitRule = Annotated[
    HoldToResolutionExit | StopTakeProfitExit,
    Field(discriminator="type"),
]


class RuleFilters(BaseModel):
    """Universe filters applied before observation generation."""

    model_config = ConfigDict(extra="forbid")

    min_lifetime_volume: float | None = Field(default=None, ge=0.0)
    volume_filter_mode: Literal["lifetime_static", "none"] = "none"
    category: str | None = None
    min_days_to_resolution: int | None = Field(default=None, ge=0)
    max_days_to_resolution: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_volume_mode_alignment(self) -> RuleFilters:
        if self.min_lifetime_volume is not None and self.volume_filter_mode == "none":
            # The contamination warning is keyed off volume_filter_mode; if a
            # caller sets a filter value without naming the mode, the
            # response would lose the limitation. Reject early instead of
            # quietly producing under-flagged output.
            msg = (
                "min_lifetime_volume set but volume_filter_mode is 'none'; "
                "set volume_filter_mode='lifetime_static' to acknowledge "
                "contamination"
            )
            raise ValueError(msg)
        if (
            self.min_days_to_resolution is not None
            and self.max_days_to_resolution is not None
            and self.min_days_to_resolution > self.max_days_to_resolution
        ):
            msg = (
                f"min_days_to_resolution ({self.min_days_to_resolution}) must be ≤ "
                f"max_days_to_resolution ({self.max_days_to_resolution})"
            )
            raise ValueError(msg)
        return self


class PredictionRule(BaseModel):
    """Top-level prediction-market backtest rule."""

    model_config = ConfigDict(extra="forbid")

    side: Literal["YES"]
    entry: EntryRule
    exit: ExitRule
    filters: RuleFilters = Field(default_factory=RuleFilters)

    @model_validator(mode="after")
    def _check_entry_exit_disjoint(self) -> PredictionRule:
        """Reject stop_take_profit rules whose triggers overlap the entry band.

        If ``stop_price >= entry.price_min`` the earliest eligible entry
        sample can itself satisfy the stop trigger, making exit semantics
        ambiguous (do we exit at entry? exit on the next row?). The cleanest
        fix is to reject at validation time so the engine walk never has to
        special-case it. Same shape for ``take_profit_price``.
        """
        if not isinstance(self.exit, StopTakeProfitExit):
            return self
        stop = self.exit.stop_price
        if stop is not None and stop >= self.entry.price_min:
            msg = (
                f"stop_price ({stop}) must be strictly below entry.price_min "
                f"({self.entry.price_min}); otherwise the entry sample itself "
                "satisfies the stop and exit semantics are ambiguous."
            )
            raise ValueError(msg)
        take_profit = self.exit.take_profit_price
        if take_profit is not None and take_profit <= self.entry.price_max:
            msg = (
                f"take_profit_price ({take_profit}) must be strictly above "
                f"entry.price_max ({self.entry.price_max}); otherwise the "
                "entry sample itself satisfies the take-profit and exit "
                "semantics are ambiguous."
            )
            raise ValueError(msg)
        return self


def validate_rule(payload: dict[str, object]) -> PredictionRule:
    """Parse + validate a rule payload (dict, typically from agent input).

    Args:
        payload: Dict matching the V1 rule schema in §10.4.

    Returns:
        Validated PredictionRule instance.

    Raises:
        pydantic.ValidationError: If the payload contains unsupported
            fields, unsupported enum values, or violates the cross-field
            constraints in the model validators.

    """
    return PredictionRule.model_validate(payload)
