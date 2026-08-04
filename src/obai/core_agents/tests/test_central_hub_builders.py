"""Unit tests for the Central Hub builder factory.

Verifies that the SandboxAgent path constructs a usable agent. Does not
run the agent; the goal is to prove the SandboxAgent capability chain is
wired correctly.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills

from core_agents.central_hub_agent import (
    HUB_SKILLS_DIR,
    CentralHubAgent,
    _build_hub_agent,
    _hub_context_management,
)


def test_hub_skills_dir_exists() -> None:
    """Lazy-skill source directory ships with the package."""
    assert HUB_SKILLS_DIR.is_dir()
    skill_files = list(HUB_SKILLS_DIR.rglob("SKILL.md"))
    # Six lifecycle skills: stock synthesis, strategy routing,
    # prediction-market routing, crypto routing, grounding/cache, and research routing.
    # Each routing skill carries its own output-contract rules.
    assert len(skill_files) == 6


def test_hub_builder_returns_sandbox_agent_with_skills() -> None:
    """Hub builder attaches the lazy hub_skills capability."""
    agent = _build_hub_agent(
        instructions="hub instructions",
        model="gpt-5.5",
        specialist_tools=[],
        guardrails=[],
        reasoning_effort="high",
        verbosity="low",
        compact_ratio=0.9,
    )
    assert isinstance(agent, SandboxAgent)
    assert agent.name == "central_hub"
    assert agent.model_settings.parallel_tool_calls is True
    assert agent.model_settings.tool_choice == "auto"

    skills_caps = [c for c in agent.capabilities if isinstance(c, Skills)]
    assert len(skills_caps) == 1
    lazy_source = skills_caps[0].lazy_from
    assert isinstance(lazy_source, LocalDirLazySkillSource)
    assert lazy_source.source.src == HUB_SKILLS_DIR


@pytest.mark.parametrize(
    ("builder", "attribute"),
    [
        ("_build_prediction_tool", "prediction_markets_agent"),
        ("_build_crypto_tool", "crypto_agent"),
        ("_build_strategy_tool", "strategy_agent"),
    ],
)
def test_specialist_wrappers_use_strict_json_schema(builder: str, attribute: str) -> None:
    """Lax schemas let the model send `Input` and get rejected by the SDK.

    The model then retries with correct casing, so one routing decision
    bills two model requests and burns two specialist calls. Strict mode
    removes the guess; the wrappers take a single required string.
    """
    hub = object.__new__(CentralHubAgent)
    setattr(hub, attribute, SimpleNamespace(agent=SimpleNamespace(name="stub")))

    assert getattr(hub, builder)().strict_json_schema is True


def _hub_base_prompt() -> str:
    return (Path(__file__).resolve().parents[1] / "prompts" / "central_hub_base.md").read_text()


def test_load_bearing_hub_rules_live_in_the_in_context_prompt() -> None:
    """Skill bodies never reach the model, so rules there are dead.

    ``load_skill`` stages a directory and returns a status envelope, and
    with ``Skills`` as the only capability the hub has no file-read tool.
    A captured run confirms it: the rendered hub instructions contain the
    one-line skill index but none of the SKILL.md section headings. Rules
    the hub must obey therefore belong in central_hub_base.md, which is
    rendered on every turn.
    """
    prompt = _hub_base_prompt()

    # Relay is scoped to the marker, so a pre-flight control signal is not
    # mistaken for an answer and echoed back to the user verbatim.
    assert "__TERMINAL_TOOL_OUTPUT__:<tool>:" in prompt
    assert "MISSING_CRYPTO_INPUTS:" in prompt
    assert "never an answer" in prompt

    # The SDK sandbox preamble asks for brevity upstream of this file; these
    # four items are the explicit exception to it.
    assert "Never shorten past these four" in prompt
    for rule in ("Name the subject", "Restate every input", "State the filters"):
        assert rule in prompt, f"missing brevity carve-out: {rule}"

    # A capability question ("can OBaI place a real-money Polymarket order?")
    # routed nowhere and was answered from the hub's own instructions. The
    # supported list has to come from the specialist's contract, which can
    # drift from the hub's belief about it without either side noticing.
    assert "supports, refuses, or can execute is a routing trigger" in prompt


def test_compaction_threshold_scales_with_model_window() -> None:
    """The threshold is a fraction of the real window, not a fixed count.

    gpt-5.6-sol's window is ~2.6x gpt-5.1's, so the same ratio must yield
    a proportionally larger threshold. This is the regression that a
    hardcoded token count would silently reintroduce.
    """
    sol = _hub_context_management(model="gpt-5.6-sol", compact_ratio=0.9)
    mini = _hub_context_management(model="gpt-5.1", compact_ratio=0.9)
    assert sol == [{"type": "compaction", "compact_threshold": 942818}]
    assert mini == [{"type": "compaction", "compact_threshold": 360000}]


def test_compaction_omitted_when_ratio_is_none() -> None:
    """No ratio means the field stays unset, not a null entry.

    A ``[{"compact_threshold": None}]`` entry would still ask the API to
    compact, so the off switch has to drop the list entirely.
    """
    assert _hub_context_management(model="gpt-5.6-sol", compact_ratio=None) is None


def test_compaction_omitted_for_unknown_model() -> None:
    """An unknown window yields no threshold rather than a guessed one."""
    assert _hub_context_management(model="some-future-model", compact_ratio=0.9) is None


def test_hub_builder_wires_compaction_and_retained_reasoning() -> None:
    """Hub pins reasoning retention and carries the derived compaction entry."""
    agent = _build_hub_agent(
        instructions="hub instructions",
        model="gpt-5.6-sol",
        specialist_tools=[],
        guardrails=[],
        reasoning_effort="medium",
        verbosity="low",
        compact_ratio=0.9,
    )
    assert agent.model_settings.context_management == [
        {"type": "compaction", "compact_threshold": 942818}
    ]
    reasoning = agent.model_settings.reasoning
    assert reasoning is not None
    assert reasoning.effort == "medium"
    assert reasoning.context == "all_turns"
