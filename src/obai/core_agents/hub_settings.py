"""User-settable hub model and reasoning effort (~/.obai/settings.json).

The hub's model and reasoning effort are the two knobs worth changing without
editing code: they set the cost and the depth of every query. This module owns
the file both the web UI and ``obai config`` write, so the two clients — which
are separate processes each building their own hub — agree on one source of
truth.

Resolution order is env > this file > the shipped default, wired into
``AgentConfig`` in :mod:`core_agents.config`. Changes apply on the next hub
construction, i.e. after ``obai restart``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal, get_args

import openai
from openai.types.shared.reasoning_effort import ReasoningEffort as SdkReasoningEffort
from pydantic import BaseModel, ValidationError, field_validator

# The two choices offered in the UI and CLI. Sol is the shipped hub default;
# Terra is the heavier-analysis model already used by strategy, crypto, and
# prediction markets. Luna is deliberately absent — it is the specialist tier
# and is not a sensible hub.
HubModel = Literal["gpt-5.6-sol", "gpt-5.6-terra"]

# Verified against the live API: every gpt-5.6 model accepts none/low/medium/
# high/xhigh/max and rejects `minimal`. We offer only the top four; none and
# low are valid but too shallow for hub routing and synthesis.
HubReasoningEffort = Literal["medium", "high", "xhigh", "max"]

HUB_MODELS: tuple[str, ...] = get_args(HubModel)
HUB_REASONING_EFFORTS: tuple[str, ...] = get_args(HubReasoningEffort)

# openai release that added the `max` tier. Named here because the error
# below is the only place a user learns why their choice was refused.
_MAX_EFFORT_MIN_OPENAI = "2.45.0"


def _sdk_reasoning_efforts() -> frozenset[str]:
    """Return the effort tiers the installed openai SDK accepts.

    The SDK, not this module, builds the request: a tier it does not know
    raises inside ``Reasoning(...)`` during hub construction, which surfaces
    as a raw traceback at startup rather than as a settings error. Reading
    its literal lets us refuse the value where the user can act on it.

    Returns:
        Accepted effort strings, empty if the literal cannot be read.
    """
    # ReasoningEffort is Optional[Literal[...]], so unwrap to the Literal.
    for arg in get_args(SdkReasoningEffort):
        values = get_args(arg)
        if values:
            return frozenset(str(value) for value in values)
    return frozenset()


def default_hub_settings_path() -> Path:
    """Return the path of the hub settings file.

    Returns:
        ``~/.obai/settings.json``.
    """
    return Path.home() / ".obai" / "settings.json"


class HubSettings(BaseModel, extra="forbid"):
    """Hub model and reasoning effort as chosen by the user."""

    hub_model: HubModel = "gpt-5.6-sol"
    hub_reasoning_effort: HubReasoningEffort = "medium"

    @field_validator("hub_reasoning_effort")
    @classmethod
    def validate_sdk_supports_effort(cls, v: str) -> str:
        """Reject a tier the installed openai SDK cannot send.

        Args:
            v: Effort tier being validated.

        Returns:
            The tier, unchanged, when the SDK accepts it.

        Raises:
            ValueError: The installed SDK is too old for this tier.
        """
        supported = _sdk_reasoning_efforts()
        if not supported or v in supported:
            return v

        msg = (
            f"Reasoning effort {v!r} is not supported by the installed "
            f"openai {openai.__version__} SDK, which accepts "
            f"{', '.join(sorted(supported))}. The {v!r} tier needs "
            f"openai>={_MAX_EFFORT_MIN_OPENAI}. Upgrade the SDK, or pick a "
            f"lower tier — otherwise the hub cannot start."
        )
        raise ValueError(msg)


class HubSettingsStore:
    """File-backed hub settings store (~/.obai/settings.json).

    Args:
        path: Override file path (use ``tmp_path`` in tests).
    """

    def __init__(self, path: Path | None = None) -> None:
        """Initialize store with file path."""
        self._path = path if path is not None else default_hub_settings_path()

    @property
    def path(self) -> Path:
        """Path this store reads and writes."""
        return self._path

    def load(self) -> HubSettings:
        """Load settings from disk, returning defaults when the file is absent.

        An absent or empty file is the normal state for a fresh install and
        for anyone upgrading from a version that predates this file, so both
        mean "use the shipped defaults".

        A file that exists but does not parse or does not validate is a
        different matter and raises. Silently falling back to defaults would
        move the user off the model they chose and bill them at another tier
        with nothing but a log line to show for it.

        Returns:
            The stored settings, or defaults when no file is present.

        Raises:
            ValueError: The file exists but is not valid hub settings.
        """
        if not self._path.exists():
            return HubSettings()

        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            return HubSettings()

        try:
            return HubSettings.model_validate_json(raw)
        except (json.JSONDecodeError, ValidationError) as e:
            msg = (
                f"Invalid hub settings at {self._path}: {e}. "
                f"Fix the file or delete it to fall back to defaults. "
                f"Valid hub_model: {', '.join(HUB_MODELS)}. "
                f"Valid hub_reasoning_effort: {', '.join(HUB_REASONING_EFFORTS)}."
            )
            raise ValueError(msg) from e

    def save(self, settings: HubSettings) -> None:
        """Atomically write settings to disk.

        Args:
            settings: Settings to persist.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same dir, then atomic rename, so a
        # crash mid-write cannot leave a half-written settings file behind.
        # mkstemp hands back an *open* descriptor, so write through it rather
        # than reopening by path: os.fdopen owns it and closes it on every
        # exit, including the error path below.
        handle, name = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        tmp = Path(name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(settings.model_dump_json(indent=2) + "\n")
            tmp.replace(self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
