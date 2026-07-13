"""Unit tests for the research specialist prompt contract."""

from __future__ import annotations

from pathlib import Path


def _read_prompt_file(name: str) -> str:
    prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
    return (prompts_dir / f"{name}.md").read_text()


def test_prompt_covers_future_freshness_and_url_citations() -> None:
    """Prompt handles the 'future' freshness class and requires URL citations.

    Guards accuracy.md §32: the research server emits a ``freshness: "future"``
    class for post-today dates (a fabrication/misdate red flag), and every
    result already carries the full ``url``. The prompt must therefore (a)
    enumerate ``future`` and give it a suspicious-source rule, and (b) cite the
    full source URL so material claims are verifiable, not domain-only.
    """
    prompt = _read_prompt_file("research")
    lowered = prompt.lower()

    # (a) 'future' is enumerated as a freshness class and has a handling rule.
    assert '"future"' in prompt
    assert "suspicious" in lowered

    # (b) Citations require the full source URL, not domain-only.
    assert "url" in lowered
    assert "source domain" not in lowered
