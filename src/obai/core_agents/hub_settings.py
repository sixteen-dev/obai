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

from pydantic import BaseModel, ValidationError

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
