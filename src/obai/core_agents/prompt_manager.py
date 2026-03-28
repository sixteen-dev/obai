"""Opik prompt management for versioned prompt storage.

Syncs markdown prompt files to Opik for automatic versioning.
Every content change creates a new version in Opik, enabling
rollback to any previous version via commit hash.

Falls back gracefully when Opik is unavailable — the application
works fine with just the markdown files on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Agent names with prompt files (excludes CLAUDE.md)
PROMPT_NAMES = [
    "central_hub",
    "events_news",
    "fundamentals",
    "guardrail",
    "market_data",
    "options",
    "portfolio",
    "research",
    "screener",
    "strategy",
]


def _get_opik_client() -> Any | None:
    """Get an Opik client instance.

    Returns:
        Opik client, or None if opik is not installed or unreachable.
    """
    try:
        import opik

        return opik.Opik()
    except Exception:
        logger.debug("Opik client unavailable for prompt management")
        return None


def sync_prompts_to_opik() -> dict[str, bool]:
    """Sync all markdown prompt files to Opik for versioning.

    Reads each prompt .md file and creates/updates it in Opik.
    Idempotent: if content hasn't changed, no new version is created.

    Returns:
        Dict mapping agent_name to True if synced, False if skipped/failed.
    """
    client = _get_opik_client()
    if client is None:
        logger.info("Opik unavailable — skipping prompt sync")
        return {name: False for name in PROMPT_NAMES}

    results: dict[str, bool] = {}
    for name in PROMPT_NAMES:
        prompt_path = PROMPTS_DIR / f"{name}.md"
        if not prompt_path.exists():
            logger.warning("Prompt file missing: %s", prompt_path)
            results[name] = False
            continue

        template_text = prompt_path.read_text()
        try:
            client.create_prompt(
                name=name,
                prompt=template_text,
                description=f"OBaI {name} agent instructions",
            )
            results[name] = True
            logger.debug("Synced prompt '%s' to Opik", name)
        except Exception:
            logger.exception("Failed to sync prompt '%s' to Opik", name)
            results[name] = False

    synced = sum(1 for v in results.values() if v)
    logger.info("Prompt sync complete: %d/%d synced", synced, len(PROMPT_NAMES))
    return results


def get_prompt_from_opik(agent_name: str, *, commit: str | None = None) -> str | None:
    """Retrieve a prompt template from Opik.

    Args:
        agent_name: Agent name (matches prompt name in Opik).
        commit: Version commit hash. None returns latest.

    Returns:
        Prompt template text, or None if unavailable.
    """
    client = _get_opik_client()
    if client is None:
        return None

    try:
        prompt = client.get_prompt(name=agent_name, commit=commit)
        if prompt is None:
            return None
        result: str = prompt.prompt
        return result
    except Exception:
        logger.debug("Failed to get prompt '%s' from Opik", agent_name)
        return None


def get_prompt_versions(agent_name: str) -> list[dict[str, Any]]:
    """Get version history for a prompt.

    Args:
        agent_name: Agent name.

    Returns:
        List of version info dicts with commit hash and metadata.
    """
    client = _get_opik_client()
    if client is None:
        return []

    try:
        versions = client.get_prompt_history(name=agent_name)
        return [
            {
                "commit": v.commit,
                "created_at": getattr(v, "created_at", None),
                "change_description": getattr(v, "change_description", None),
            }
            for v in versions
        ]
    except Exception:
        logger.debug("Failed to get versions for prompt '%s'", agent_name)
        return []
