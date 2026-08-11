"""Unit tests for the Central Hub builder factory.

Verifies that the SandboxAgent path constructs a usable agent. Does not
run the agent; the goal is to prove the SandboxAgent capability chain is
wired correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import Agent
from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills

from core_agents.central_hub_agent import (
    HUB_SKILLS_DIR,
    CentralHubAgent,
    _apply_hub_agent_settings,
    _build_hub_agent,
    _hub_context_management,
)
from core_agents.config import get_config, reset_config


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


class TestApplyHubSettings:
    """Live-applying a hub model/effort change without rebuilding the agent.

    The SDK resolves ``agent.model`` and ``agent.model_settings`` per turn
    (``run_internal/turn_preparation.get_model``), so mutating the built
    agent takes effect on the next query — no process restart, no MCP
    re-init, no dropped WebSocket.
    """

    @staticmethod
    def _agent() -> Agent[None]:
        return _build_hub_agent(
            instructions="hub instructions",
            model="gpt-5.6-sol",
            specialist_tools=[],
            guardrails=[],
            reasoning_effort="medium",
            verbosity="low",
            compact_ratio=0.9,
        )

    def test_applies_model_and_effort(self) -> None:
        agent = self._agent()

        _apply_hub_agent_settings(
            agent,
            model="gpt-5.6-terra",
            reasoning_effort="xhigh",
            compact_ratio=0.9,
        )

        assert agent.model == "gpt-5.6-terra"
        assert agent.model_settings.reasoning is not None
        assert agent.model_settings.reasoning.effort == "xhigh"

    def test_keeps_reasoning_context_pinned(self) -> None:
        """context="all_turns" is what retains reasoning across hub turns.

        Rebuilding Reasoning() without it would silently drop that.
        """
        agent = self._agent()

        _apply_hub_agent_settings(
            agent, model="gpt-5.6-terra", reasoning_effort="high", compact_ratio=0.9
        )

        assert agent.model_settings.reasoning is not None
        assert agent.model_settings.reasoning.context == "all_turns"

    def test_recomputes_the_compaction_threshold_for_the_new_model(self) -> None:
        """The threshold is a fraction of the *model's* window."""
        agent = self._agent()

        _apply_hub_agent_settings(
            agent, model="gpt-5.6-terra", reasoning_effort="medium", compact_ratio=0.5
        )

        expected = _hub_context_management(model="gpt-5.6-terra", compact_ratio=0.5)
        assert agent.model_settings.context_management == expected

    def test_leaves_everything_else_untouched(self) -> None:
        """Only model and effort change; tools and prompt must survive."""
        agent = self._agent()
        assert isinstance(agent, SandboxAgent)
        instructions, caps, verbosity = (
            agent.instructions,
            agent.capabilities,
            agent.model_settings.verbosity,
        )

        _apply_hub_agent_settings(
            agent, model="gpt-5.6-terra", reasoning_effort="max", compact_ratio=0.9
        )

        assert agent.instructions == instructions
        assert agent.capabilities is caps
        assert agent.model_settings.verbosity == verbosity
        assert agent.model_settings.parallel_tool_calls is True


class TestCentralHubApplyHubSettings:
    """The public entry point: agent retune plus the config sync.

    ``self.config`` is the ``get_config()`` singleton that ``/api/status`` and
    ``/api/settings`` read. Retuning the agent without updating it would leave
    every surface reporting the old model forever — indistinguishable, from
    the user's side, from the save having failed.
    """

    @pytest.fixture
    def hub(self) -> Iterator[CentralHubAgent]:
        """A hub with a built agent and an isolated config singleton."""
        reset_config()
        hub = CentralHubAgent()
        hub.agent = _build_hub_agent(
            instructions="hub instructions",
            model="gpt-5.6-sol",
            specialist_tools=[],
            guardrails=[],
            reasoning_effort="medium",
            verbosity="low",
            compact_ratio=0.9,
        )
        yield hub
        reset_config()

    def test_retunes_the_agent(self, hub: CentralHubAgent) -> None:
        hub.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh")

        assert hub.agent is not None
        assert hub.agent.model == "gpt-5.6-terra"
        assert hub.agent.model_settings.reasoning is not None
        assert hub.agent.model_settings.reasoning.effort == "xhigh"

    def test_syncs_the_config_the_status_endpoints_read(self, hub: CentralHubAgent) -> None:
        hub.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="xhigh")

        assert hub.config.orchestrator_model == "gpt-5.6-terra"
        assert hub.config.orchestrator_reasoning_effort == "xhigh"
        # The same object /api/status reports from, not a copy of it.
        assert get_config().orchestrator_model == "gpt-5.6-terra"

    def test_refuses_before_initialize(self) -> None:
        """Fail loud rather than pretend a change was applied to nothing."""
        reset_config()
        hub = CentralHubAgent()

        with pytest.raises(RuntimeError, match="before initialize"):
            hub.apply_hub_settings(model="gpt-5.6-terra", reasoning_effort="high")

        reset_config()
