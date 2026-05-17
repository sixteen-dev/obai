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

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Supported sides — V1 ships only YES because both lifetime-volume
# contamination and entry-price filters are defined against YES prices in
# the current store. NO can join when entry math is mirrored.
SUPPORTED_SIDES: Final[frozenset[Literal["YES"]]] = frozenset({"YES"})

# Supported exit types. hold_to_resolution is the only one that ignores
# liquidity reconstruction; others wait on §10.4 V2 work.
SUPPORTED_EXIT_TYPES: Final[frozenset[Literal["hold_to_resolution"]]] = frozenset(
    {"hold_to_resolution"}
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


class ExitRule(BaseModel):
    """Exit rule. V1 supports hold_to_resolution only."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["hold_to_resolution"]


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
