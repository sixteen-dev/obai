"""Unit tests for the Central Hub builder factories.

Verifies that the legacy plain-Agent path and the SandboxAgent path
each construct a usable agent. Does not run the agents; the goal is to
prove the SandboxAgent capability chain is wired correctly so the
``ENABLE_SANDBOX_HUB`` flag can be flipped on without runtime surprises.
"""

from __future__ import annotations

from agents import Agent
from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills

from core_agents.central_hub_agent import (
    HUB_SKILLS_DIR,
    _build_plain_hub_agent,
    _build_sandbox_hub_agent,
)


def test_hub_skills_dir_exists() -> None:
    """Lazy-skill source directory ships with the package."""
    assert HUB_SKILLS_DIR.is_dir()
    skill_files = list(HUB_SKILLS_DIR.rglob("SKILL.md"))
    # Five lifecycle skills: stock synthesis, strategy routing,
    # prediction-market routing, grounding/cache, and research routing.
    # Each routing skill carries its own output-contract rules.
    assert len(skill_files) == 5


def test_plain_hub_builder_returns_plain_agent() -> None:
    """Plain builder produces an ``Agent`` (not a ``SandboxAgent``)."""
    agent = _build_plain_hub_agent(
        instructions="hub instructions",
        model="gpt-5.5",
        specialist_tools=[],
        guardrails=[],
    )
    assert isinstance(agent, Agent)
    assert not isinstance(agent, SandboxAgent)
    assert agent.name == "central_hub"
    assert agent.model_settings.parallel_tool_calls is True
    assert agent.model_settings.tool_choice == "auto"


def test_sandbox_hub_builder_returns_sandbox_agent_with_skills() -> None:
    """Sandbox builder attaches the lazy hub_skills capability."""
    agent = _build_sandbox_hub_agent(
        instructions="hub instructions",
        model="gpt-5.5",
        specialist_tools=[],
        guardrails=[],
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
