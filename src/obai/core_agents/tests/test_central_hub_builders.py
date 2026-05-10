"""Unit tests for the Central Hub builder factory.

Verifies that the SandboxAgent path constructs a usable agent. Does not
run the agent; the goal is to prove the SandboxAgent capability chain is
wired correctly.
"""

from __future__ import annotations

from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills

from core_agents.central_hub_agent import (
    HUB_SKILLS_DIR,
    _build_hub_agent,
)


def test_hub_skills_dir_exists() -> None:
    """Lazy-skill source directory ships with the package."""
    assert HUB_SKILLS_DIR.is_dir()
    skill_files = list(HUB_SKILLS_DIR.rglob("SKILL.md"))
    # Five lifecycle skills: stock synthesis, strategy routing,
    # prediction-market routing, grounding/cache, and research routing.
    # Each routing skill carries its own output-contract rules.
    assert len(skill_files) == 5


def test_hub_builder_returns_sandbox_agent_with_skills() -> None:
    """Hub builder attaches the lazy hub_skills capability."""
    agent = _build_hub_agent(
        instructions="hub instructions",
        model="gpt-5.5",
        specialist_tools=[],
        guardrails=[],
        reasoning_effort="high",
        verbosity="low",
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
