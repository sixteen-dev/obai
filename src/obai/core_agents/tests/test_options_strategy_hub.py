"""Hub wiring for the options-strategy terminal route (ADR 0003 §2.2, §2.4-§2.7, §3).

Covers the §4.3 Phase B obligations: the ``options_strategy_analysis`` wrapper
(handoff check, rendering, terminal relay, empty output), the terminal failure
hook, the invocation-scoped state across copied contexts, concurrent and
cancelled ``run()`` calls, degradation and the enable flag, and the routing
facts the hub reads. Prompt and skill pins read the ``.md`` files directly,
never through ``load_prompt``, so a prompt synced to a local Opik server cannot
change what they check.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from agents import Agent, FunctionTool, MaxTurnsExceeded, Runner
from agents.stream_events import RawResponsesStreamEvent
from agents.tool_context import ToolContext
from openai.types.responses import ResponseTextDeltaEvent

from core_agents import central_hub_agent
from core_agents.base_agent import BaseAgent
from core_agents.central_hub_agent import (
    _OPTIONS_STRATEGY_TOOL_DESCRIPTION,
    CentralHubAgent,
    OptionsStrategyPassthroughEvent,
    StrategyPassthroughEvent,
    _clear_options_strategy_passthrough,
    _get_options_strategy_passthrough,
    _has_strategy_objective,
    _set_options_strategy_passthrough,
)
from core_agents.config import AgentConfig, reset_config
from core_agents.crypto_agent import CryptoAgent
from core_agents.events_news_agent import EventsNewsAgent
from core_agents.fundamentals_agent import FundamentalsAgent
from core_agents.market_data_agent import MarketDataAgent
from core_agents.mcp import MCPClientError
from core_agents.options_agent import OptionsAgent
from core_agents.options_strategy_agent import OptionsStrategyAgent
from core_agents.portfolio_agent import PortfolioAgent
from core_agents.prediction_markets_agent import PredictionMarketsAgent
from core_agents.research_agent import ResearchAgent
from core_agents.screener_agent import ScreenerAgent
from core_agents.strategy_agent import StrategyAgent

_CORE_AGENTS_DIR = Path(__file__).resolve().parents[1]
_MARKER = "__TERMINAL_TOOL_OUTPUT__:options_strategy_analysis:render=verbatim_relay\n\n"
_QUERY = "Backtest an XSP put credit vertical over the last three years and give me the return."
_ANSWER = "**Status**: `unavailable`\n\n**Explanation**: DATA_ENTITLEMENT_MISSING"
_ARGUMENT_NAMES = {"user_request", "underlyings", "context", "prior_run_ids", "requested_action"}
_AGENT_CLASSES: tuple[type[BaseAgent], ...] = (
    FundamentalsAgent,
    MarketDataAgent,
    EventsNewsAgent,
    OptionsAgent,
    ScreenerAgent,
    PortfolioAgent,
    StrategyAgent,
    ResearchAgent,
    PredictionMarketsAgent,
    CryptoAgent,
    OptionsStrategyAgent,
)


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    """Start and end every test with an empty holder and scoring list."""
    _clear_options_strategy_passthrough()
    central_hub_agent._inner_tool_outputs.clear()
    yield
    _clear_options_strategy_passthrough()
    central_hub_agent._inner_tool_outputs.clear()


def _config(**overrides: Any) -> AgentConfig:
    """Build a config independent of the developer's environment.

    Args:
        overrides: Field values; init kwargs outrank the environment.

    Returns:
        The config.
    """
    values: dict[str, Any] = {"openai_api_key": "test-key", "options_strategy_max_turns": 7}
    values.update(overrides)
    return AgentConfig(**values)


def _arguments(**overrides: object) -> dict[str, object]:
    """Tool arguments for a faithful backtest handoff.

    Args:
        overrides: Argument values replacing the defaults.

    Returns:
        The five strict-schema arguments.
    """
    arguments: dict[str, object] = {
        "user_request": _QUERY,
        "underlyings": ["XSP"],
        "context": "",
        "prior_run_ids": [],
        "requested_action": "backtest",
    }
    arguments.update(overrides)
    return arguments


def _hub(name: str, query: str | None = _QUERY) -> CentralHubAgent:
    """Build a hub with a stub specialist and no MCP connections.

    Args:
        name: Distinguishes the stub hub and specialist agents.
        query: The query ``run()`` would have recorded.

    Returns:
        A hub ready for ``_build_options_strategy_tool`` and ``run()``.
    """
    hub = object.__new__(CentralHubAgent)
    hub.config = _config()
    hub._current_user_query = query
    hub.options_strategy_agent = SimpleNamespace(agent=SimpleNamespace(name=f"{name}_specialist"))  # type: ignore[assignment]
    hub.agent = SimpleNamespace(name=f"{name}_hub")  # type: ignore[assignment]
    hub._initialized = True
    hub._cache = None
    hub._run_config = None
    return hub


def _tool(hub: CentralHubAgent) -> FunctionTool:
    """Build the options strategy tool and check its type.

    Args:
        hub: Hub carrying the stub specialist.

    Returns:
        The function tool.
    """
    tool = hub._build_options_strategy_tool()
    assert isinstance(tool, FunctionTool)
    return tool


async def _invoke(tool: FunctionTool, arguments: dict[str, object]) -> str:
    """Call the tool the way the SDK does, with a real tool context.

    Args:
        tool: The function tool.
        arguments: JSON-serializable arguments.

    Returns:
        The tool output.
    """
    return await _invoke_raw(tool, json.dumps(arguments))


async def _invoke_raw(tool: FunctionTool, payload: str) -> str:
    """Call the tool with argument text exactly as a model emitted it.

    Args:
        tool: The function tool.
        payload: The raw arguments text, possibly malformed.

    Returns:
        The tool output.
    """
    context: ToolContext[None] = ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id="call_test",
        tool_arguments=payload,
    )
    return str(await tool.on_invoke_tool(context, payload))


class _FakeSpecialistRun:
    """Stand-in for a streamed specialist run with a fixed final output."""

    def __init__(self, final_output: object) -> None:
        self.final_output = final_output

    async def stream_events(self) -> AsyncIterator[object]:
        """Yield nothing; the wrapper only reads ``final_output`` afterwards."""
        return
        yield  # pragma: no cover - makes this an async generator


def _text_event(item_id: str, delta: str) -> RawResponsesStreamEvent:
    """Build one hub text delta event.

    Args:
        item_id: Message item id.
        delta: Text delta.

    Returns:
        The stream event.
    """
    return RawResponsesStreamEvent(
        data=ResponseTextDeltaEvent(
            content_index=0,
            delta=delta,
            item_id=item_id,
            logprobs=[],
            output_index=0,
            sequence_number=0,
            type="response.output_text.delta",
        )
    )


class _FakeHubRun:
    """Streamed hub run: text, one options-strategy tool call, then hub text."""

    def __init__(
        self,
        tool: FunctionTool,
        arguments: dict[str, object],
        after_tool: Callable[[], Awaitable[object]],
    ) -> None:
        self._tool = tool
        self._arguments = arguments
        self._after_tool = after_tool

    async def stream_events(self) -> AsyncIterator[object]:
        """Yield a preamble, run the tool in its own task, then a hub memo."""
        yield _text_event("pre", "Routing to the options specialist.")
        # The SDK runs each tool call in a task, which copies the run's context.
        await asyncio.create_task(_invoke(self._tool, self._arguments))
        await self._after_tool()
        yield _text_event("post", "Hub-authored memo with an invented return.")


def _install_runs(monkeypatch: pytest.MonkeyPatch, runs: dict[int, object]) -> None:
    """Route ``Runner.run_streamed`` to a fake run by starting agent.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        runs: Fake run per ``id(starting_agent)``.
    """

    def _fake_run_streamed(*, starting_agent: object, **_kwargs: object) -> object:
        return runs[id(starting_agent)]

    monkeypatch.setattr(Runner, "run_streamed", _fake_run_streamed)


def _record_specialist_calls(
    monkeypatch: pytest.MonkeyPatch,
    result: _FakeSpecialistRun | Exception,
) -> list[dict[str, Any]]:
    """Patch ``Runner.run_streamed`` to record its arguments.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        result: The fake run to return, or an exception to raise.

    Returns:
        The list each call's keyword arguments are appended to.
    """
    calls: list[dict[str, Any]] = []

    def _fake_run_streamed(**kwargs: Any) -> _FakeSpecialistRun:
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(Runner, "run_streamed", _fake_run_streamed)
    return calls


async def _collect(hub: CentralHubAgent, query: str) -> list[object]:
    """Drain one ``run()`` call.

    Args:
        hub: Hub under test.
        query: User query.

    Returns:
        Every event the run yielded.
    """
    return [event async for event in hub.run(query)]


async def _noop() -> None:
    """Do nothing after the tool call."""


class TestOptionsStrategyToolWrapper:
    """``options_strategy_analysis`` (ADR 0003 §2.5)."""

    def test_tool_is_strict_and_takes_the_five_handoff_arguments(self) -> None:
        tool = _tool(_hub("solo"))

        schema = tool.params_json_schema
        assert tool.name == "options_strategy_analysis"
        assert tool.strict_json_schema is True
        assert tool.description == _OPTIONS_STRATEGY_TOOL_DESCRIPTION
        assert set(schema["properties"]) == _ARGUMENT_NAMES
        assert set(schema["required"]) == _ARGUMENT_NAMES
        assert schema["properties"]["requested_action"]["enum"] == [
            "build",
            "backtest",
            "compare",
            "explain",
            "status",
        ]

    def test_rewritten_request_returns_the_handoff_error_unwrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hub must retry with the user's words; nothing is relayed or run."""
        calls = _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        tool = _tool(_hub("solo"))

        output = asyncio.run(_invoke(tool, _arguments(user_request="XSP put spread backtest")))

        assert output.startswith("OPTIONS_STRATEGY_HANDOFF_ERROR:")
        assert "__TERMINAL_TOOL_OUTPUT__" not in output
        assert _get_options_strategy_passthrough() is None
        assert calls == []

    def test_verbatim_request_passes_despite_the_gate_correlation_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        tagged = f"{_QUERY} [OBaI regression correlation: case-1]"
        tool = _tool(_hub("solo", query=tagged))

        output = asyncio.run(_invoke(tool, _arguments(user_request=f"  {_QUERY.upper()}  ")))

        assert output == _MARKER + _ANSWER

    def test_non_empty_output_is_marker_wrapped_and_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        hub = _hub("solo")
        tool = _tool(hub)

        output = asyncio.run(_invoke(tool, _arguments()))

        assert output == _MARKER + _ANSWER
        assert _get_options_strategy_passthrough() == _ANSWER
        assert len(calls) == 1
        assert hub.options_strategy_agent is not None
        assert calls[0]["starting_agent"] is hub.options_strategy_agent.agent
        assert calls[0]["max_turns"] == 7

    def test_handoff_renders_every_block_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        tool = _tool(_hub("solo"))
        arguments = _arguments(
            underlyings=[" XSP ", "SPX"],
            prior_run_ids=["run_a", "run_b"],
            context="- Current XSP chain snapshot dated in the handoff.",
        )

        asyncio.run(_invoke(tool, arguments))

        assert calls[0]["input"] == (
            f"User request:\n{_QUERY}\n\n"
            "Requested action:\nbacktest\n\n"
            "Underlyings:\nXSP, SPX\n\n"
            "Prior run IDs:\nrun_a, run_b\n\n"
            "Context:\n- Current XSP chain snapshot dated in the handoff."
        )

    def test_handoff_omits_empty_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A capability question carries no underlyings, run ids or context."""
        calls = _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        tool = _tool(_hub("solo"))
        arguments = _arguments(underlyings=[" "], context="  ", requested_action="explain")

        asyncio.run(_invoke(tool, arguments))

        assert calls[0]["input"] == f"User request:\n{_QUERY}\n\nRequested action:\nexplain"

    @pytest.mark.parametrize("final_output", [None, "", " \n "], ids=["none", "empty", "blank"])
    def test_empty_output_is_relayed_as_the_failed_short_form(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        final_output: str | None,
    ) -> None:
        """A silent specialist never hands authorship back to the hub (ADR 0003 §8, F3)."""
        _record_specialist_calls(monkeypatch, _FakeSpecialistRun(final_output))
        tool = _tool(_hub("solo"))

        with caplog.at_level(logging.WARNING, logger=central_hub_agent.logger.name):
            output = asyncio.run(_invoke(tool, _arguments()))

        assert output.startswith(_MARKER)
        body = output.removeprefix(_MARKER)
        headings = ("**Status**", "**Reference**", "**Supported next action**", "**Explanation**")
        positions = [body.index(heading) for heading in headings]
        assert positions == sorted(positions)
        assert "`failed`" in body
        assert "returned no text" in body
        assert "Error" not in body
        assert _get_options_strategy_passthrough() == body
        hub_records = [r for r in caplog.records if r.name == central_hub_agent.logger.name]
        assert [record.levelno for record in hub_records] == [logging.WARNING]


class TestOptionsStrategyFailureHook:
    """Wrapper failures are terminal (ADR 0003 §2.5)."""

    def test_max_turns_becomes_a_relayed_failed_short_form(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _record_specialist_calls(monkeypatch, MaxTurnsExceeded("Max turns (7) exceeded"))
        tool = _tool(_hub("solo"))

        with caplog.at_level(logging.ERROR, logger=central_hub_agent.logger.name):
            output = asyncio.run(_invoke(tool, _arguments()))

        assert output.startswith(_MARKER)
        body = output.removeprefix(_MARKER)
        headings = ("**Status**", "**Reference**", "**Supported next action**", "**Explanation**")
        positions = [body.index(heading) for heading in headings]
        assert positions == sorted(positions)
        assert "`failed`" in body
        assert "MaxTurnsExceeded" in body
        assert "An error occurred" not in body
        assert _get_options_strategy_passthrough() == body

        errors = [record for record in caplog.records if record.levelno == logging.ERROR]
        assert len(errors) == 1
        assert errors[0].exc_info is not None
        assert isinstance(errors[0].exc_info[1], MaxTurnsExceeded)

    def test_failure_reports_the_class_never_a_number_from_the_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the service may put a number in front of the user."""
        _record_specialist_calls(monkeypatch, RuntimeError("model said 12.5% CAGR"))
        tool = _tool(_hub("solo"))

        output = asyncio.run(_invoke(tool, _arguments()))

        assert "RuntimeError" in output
        assert "12.5" not in output

    def test_failure_makes_no_claim_about_what_the_specialist_validated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hook cannot see the run, which may have validated before it stopped."""
        _record_specialist_calls(monkeypatch, MaxTurnsExceeded("Max turns (7) exceeded"))
        tool = _tool(_hub("solo"))

        body = asyncio.run(_invoke(tool, _arguments())).removeprefix(_MARKER)

        assert re.search(r"\bvalidated\b", body) is None
        assert "no run exists" in body

    @pytest.mark.parametrize(
        ("payload", "detail"),
        [
            (
                json.dumps(_arguments(requested_action="simulate")),
                "Invalid JSON input for tool options_strategy_analysis",
            ),
            (json.dumps(_arguments())[:-5], ""),
        ],
        ids=["invalid_action", "truncated_json"],
    )
    def test_argument_rejection_goes_back_to_the_hub_unwrapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        payload: str,
        detail: str,
    ) -> None:
        """An SDK argument rejection is not a specialist answer (ADR 0003 §2.5).

        The hub gets the SDK's default text and can retry; nothing is relayed,
        and the judge still sees the argument rejection.
        """
        calls = _record_specialist_calls(monkeypatch, _FakeSpecialistRun(_ANSWER))
        tool = _tool(_hub("solo"))

        with caplog.at_level(logging.WARNING, logger=central_hub_agent.logger.name):
            output = asyncio.run(_invoke_raw(tool, payload))

        assert output.startswith("An error occurred while")
        assert detail in output
        assert "__TERMINAL_TOOL_OUTPUT__" not in output
        assert _get_options_strategy_passthrough() is None
        assert calls == []
        hub_records = [r for r in caplog.records if r.name == central_hub_agent.logger.name]
        assert [record.levelno for record in hub_records] == [logging.WARNING]


class TestOptionsStrategyInvocationState:
    """Invocation-scoped terminal state (ADR 0003 §2.6)."""

    def test_write_in_a_copied_context_is_visible_to_the_parent(self) -> None:
        """The SDK runs tools in a copied context; the holder is shared."""
        _clear_options_strategy_passthrough()

        copy_context().run(_set_options_strategy_passthrough, "child answer")

        assert _get_options_strategy_passthrough() == "child answer"

    def test_clear_installs_a_fresh_empty_holder(self) -> None:
        _set_options_strategy_passthrough("previous answer")

        _clear_options_strategy_passthrough()

        assert _get_options_strategy_passthrough() is None


class TestHubRunRelay:
    """``run()`` detection, emit and ``finally`` (ADR 0003 §2.6)."""

    def test_run_relays_the_specialist_output_and_drops_later_hub_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hub = _hub("solo")
        assert hub.options_strategy_agent is not None
        runs: dict[int, object] = {
            id(hub.agent): _FakeHubRun(_tool(hub), _arguments(), _noop),
            id(hub.options_strategy_agent.agent): _FakeSpecialistRun(_ANSWER),
        }
        _install_runs(monkeypatch, runs)

        events = asyncio.run(_collect(hub, _QUERY))

        deltas = [
            event.data.delta
            for event in events
            if isinstance(event, RawResponsesStreamEvent)
            and isinstance(event.data, ResponseTextDeltaEvent)
        ]
        assert deltas == ["Routing to the options specialist."]
        assert events[-1] == OptionsStrategyPassthroughEvent(content=_ANSWER)

    def test_a_second_terminal_in_one_turn_is_logged_and_first_wins_stands(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Detection order still picks one terminal; the dropped one is never silent.

        ADR 0003 §8 (M4): two terminals in one turn is a routing error, not a
        designed path, so the relay stays first-wins and a warning names both.
        """
        monkeypatch.setattr(central_hub_agent, "_strategy_passthrough", None)
        hub = _hub("solo")
        assert hub.options_strategy_agent is not None

        async def _strategy_also_fired() -> None:
            central_hub_agent._set_strategy_passthrough("Equity strategy answer", "other")

        runs: dict[int, object] = {
            id(hub.agent): _FakeHubRun(_tool(hub), _arguments(), _strategy_also_fired),
            id(hub.options_strategy_agent.agent): _FakeSpecialistRun(_ANSWER),
        }
        _install_runs(monkeypatch, runs)

        with caplog.at_level(logging.WARNING, logger=central_hub_agent.logger.name):
            events = asyncio.run(_collect(hub, _QUERY))

        relayed = [
            event
            for event in events
            if isinstance(event, StrategyPassthroughEvent | OptionsStrategyPassthroughEvent)
        ]
        assert relayed == [StrategyPassthroughEvent(content="Equity strategy answer")]
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.name == central_hub_agent.logger.name and record.levelno == logging.WARNING
        ]
        assert len(warnings) == 1
        assert "strategy, options_strategy" in warnings[0]
        assert "relaying strategy only" in warnings[0]

    def test_concurrent_runs_keep_separate_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both tools write before either run reaches its tail."""
        barrier = asyncio.Barrier(2)
        hubs = {name: _hub(name, query=f"{name}: {_QUERY}") for name in ("alpha", "beta")}
        runs: dict[int, object] = {}
        for name, hub in hubs.items():
            assert hub.options_strategy_agent is not None
            arguments = _arguments(user_request=f"{name}: {_QUERY}")
            runs[id(hub.agent)] = _FakeHubRun(_tool(hub), arguments, barrier.wait)
            runs[id(hub.options_strategy_agent.agent)] = _FakeSpecialistRun(f"{name} answer")
        _install_runs(monkeypatch, runs)

        async def _both() -> list[list[object]]:
            return await asyncio.gather(
                *(_collect(hub, f"{name}: {_QUERY}") for name, hub in hubs.items())
            )

        alpha_events, beta_events = asyncio.run(_both())

        assert alpha_events[-1] == OptionsStrategyPassthroughEvent(content="alpha answer")
        assert beta_events[-1] == OptionsStrategyPassthroughEvent(content="beta answer")

    def test_cancelled_run_leaves_no_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hub = _hub("solo")
        assert hub.options_strategy_agent is not None
        written = asyncio.Event()
        never = asyncio.Event()

        async def _block() -> None:
            written.set()
            await never.wait()

        runs: dict[int, object] = {
            id(hub.agent): _FakeHubRun(_tool(hub), _arguments(), _block),
            id(hub.options_strategy_agent.agent): _FakeSpecialistRun(_ANSWER),
        }
        _install_runs(monkeypatch, runs)

        async def _scenario() -> tuple[str | None, str | None]:
            task = asyncio.create_task(_collect(hub, _QUERY))
            await written.wait()
            before = task.get_context().run(_get_options_strategy_passthrough)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return before, task.get_context().run(_get_options_strategy_passthrough)

        before, after = asyncio.run(_scenario())

        assert before == _ANSWER
        assert after is None


async def _initialize_offline(self: BaseAgent) -> None:
    """Stand-in for ``BaseAgent.initialize`` that builds an SDK agent offline."""
    self.agent = Agent(name=self.sdk_agent_name, instructions="stub", model="stub-model")
    self._initialized = True


async def _initialize_unreachable(self: BaseAgent) -> None:
    """Stand-in for ``BaseAgent.initialize`` against a server that is down.

    Raises:
        MCPClientError: Always.
    """
    raise MCPClientError(f"{self.agent_name}: Connection refused")


@pytest.fixture
def offline_hub_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Patch every specialist's ``initialize`` and the hub prompt; isolate config.

    The options-strategy route is opt-in (ADR 0004 §7), so the fixture opts in;
    tests of the off state override or remove the variable themselves.
    """
    for agent_class in _AGENT_CLASSES:
        monkeypatch.setattr(agent_class, "initialize", _initialize_offline)
    monkeypatch.setattr(central_hub_agent, "load_prompt", lambda *_a, **_k: "hub instructions")
    monkeypatch.setenv("ENABLE_GUARDRAILS", "false")
    monkeypatch.setenv("ENABLE_OPTIONS_STRATEGY", "true")
    reset_config()
    yield monkeypatch
    reset_config()


def _tool_names(hub: CentralHubAgent) -> set[str]:
    """Names of the tools the built hub agent carries.

    Args:
        hub: An initialized hub.

    Returns:
        Tool names.
    """
    assert hub.agent is not None
    return {tool.name for tool in hub.agent.tools}


class TestOptionsStrategyInit:
    """Registration, degradation and the enable flag (ADR 0003 §2.7)."""

    def test_healthy_server_registers_the_tool_and_the_specialist(
        self, offline_hub_env: pytest.MonkeyPatch
    ) -> None:
        hub = CentralHubAgent()

        asyncio.run(hub.initialize())

        assert isinstance(hub.options_strategy_agent, OptionsStrategyAgent)
        assert "options_strategy_analysis" in _tool_names(hub)
        assert hub.degraded_capabilities == []
        assert hub.get_specialist("options_strategy") is hub.options_strategy_agent.agent

    def test_failed_initialize_degrades_only_options_strategy(
        self, offline_hub_env: pytest.MonkeyPatch
    ) -> None:
        offline_hub_env.setattr(OptionsStrategyAgent, "initialize", _initialize_unreachable)
        hub = CentralHubAgent()

        asyncio.run(hub.initialize())

        assert hub.options_strategy_agent is None
        assert hub.degraded_capabilities == ["options_strategy"]
        assert hub.research_agent is not None
        assert hub.prediction_markets_agent is not None
        assert hub.crypto_agent is not None
        names = _tool_names(hub)
        assert "options_strategy_analysis" not in names
        assert {"crypto_analysis", "strategy_analysis", "options_analysis"} <= names
        with pytest.raises(ValueError, match="options_strategy not initialized"):
            hub.get_specialist("options_strategy")

    def test_disabled_flag_never_constructs_and_is_not_degraded(
        self, offline_hub_env: pytest.MonkeyPatch
    ) -> None:
        offline_hub_env.setenv("ENABLE_OPTIONS_STRATEGY", "false")
        reset_config()
        constructor = MagicMock(side_effect=AssertionError("constructed while disabled"))
        offline_hub_env.setattr(central_hub_agent, "OptionsStrategyAgent", constructor)
        hub = CentralHubAgent()

        asyncio.run(hub.initialize())

        constructor.assert_not_called()
        assert hub.options_strategy_agent is None
        assert hub.degraded_capabilities == []
        assert "options_strategy_analysis" not in _tool_names(hub)

    def test_unset_flag_is_the_default_off_state(self, offline_hub_env: pytest.MonkeyPatch) -> None:
        """A fresh install never writes the opt-in, so the variable is absent (ADR 0004 §7)."""
        offline_hub_env.delenv("ENABLE_OPTIONS_STRATEGY")
        reset_config()
        constructor = MagicMock(side_effect=AssertionError("constructed without the opt-in"))
        offline_hub_env.setattr(central_hub_agent, "OptionsStrategyAgent", constructor)
        hub = CentralHubAgent()

        asyncio.run(hub.initialize())

        constructor.assert_not_called()
        assert hub.options_strategy_agent is None
        assert hub.degraded_capabilities == []
        assert "options_strategy_analysis" not in _tool_names(hub)

    def test_cleanup_nulls_the_specialist(self, offline_hub_env: pytest.MonkeyPatch) -> None:
        hub = CentralHubAgent()
        asyncio.run(hub.initialize())

        asyncio.run(hub.close())

        assert hub.options_strategy_agent is None

    def test_options_analysis_description_is_current_market_only(
        self, offline_hub_env: pytest.MonkeyPatch
    ) -> None:
        """The hub reads the as_tool description, not the handoff description."""
        hub = CentralHubAgent()
        asyncio.run(hub.initialize())

        assert hub.agent is not None
        tools = {tool.name: tool for tool in hub.agent.tools}
        options_tool = tools["options_analysis"]
        assert isinstance(options_tool, FunctionTool)
        assert "any options" not in options_tool.description
        assert "options_strategy_analysis" in options_tool.description


def _read(relative: str) -> str:
    """Read a file under ``core_agents``.

    Args:
        relative: Path relative to the ``core_agents`` package.

    Returns:
        The file text.
    """
    return (_CORE_AGENTS_DIR / relative).read_text()


def _skill_description(name: str) -> str:
    """Return a hub skill's frontmatter description line.

    Args:
        name: Skill directory name.

    Returns:
        The description text.
    """
    for line in _read(f"hub_skills/{name}/SKILL.md").splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    msg = f"{name} has no description line"
    raise AssertionError(msg)


class TestOptionsStrategyRoutingFacts:
    """Routing boundaries and where they live (ADR 0003 §3)."""

    def test_base_prompt_splits_current_options_from_options_strategies(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        assert "- Current options market analytics" in prompt
        assert "Options chains, Greeks, implied volatility, open interest, spreads: use" not in (
            prompt
        )
        invariant = next(
            line for line in prompt.splitlines() if line.startswith("- Options strategies over")
        )
        for case in (
            "historical performance",
            "validation of options strategy rules or a strategy document",
            "covered calls",
            "cash-secured puts",
            "wheels",
            "rolls",
            "what options-strategy backtesting OBaI supports",
        ):
            assert case in invariant
        assert invariant.count("`options_strategy_analysis`") == 1
        assert "- Equity and ETF share strategy design" in prompt
        assert "route to `options_strategy_analysis`, not `strategy_analysis`" in prompt

    def test_base_prompt_keeps_current_evidence_dated_in_mixed_requests(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        assert "call `options_analysis` and `options_strategy_analysis` separately" in prompt
        assert "never passed as historical state" in prompt

    def test_base_prompt_lists_the_new_terminal_everywhere(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        assert "`obai-options-strategy-routing`: **mandatory** before calling" in prompt
        terminal_line = next(line for line in prompt.splitlines() if "- Terminal authors:" in line)
        assert "`options_strategy_analysis`" in terminal_line
        relay_line = next(line for line in prompt.splitlines() if "- Relay mechanism" in line)
        assert "`options_strategy_analysis`" in relay_line
        error_line = next(
            line for line in prompt.splitlines() if line.startswith("For terminal-author")
        )
        assert "`options_strategy_analysis`" in error_line

    def test_base_prompt_has_the_options_strategy_preflight(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        preflight = next(
            line
            for line in prompt.splitlines()
            if line.startswith("- Options-strategy pre-flight (mandatory):")
        )
        assert "you MUST call `load_skill('obai-options-strategy-routing')` first" in preflight
        assert "before any call to `options_strategy_analysis`" in preflight

    def test_base_prompt_names_the_handoff_control_signal(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        signal = next(
            line for line in prompt.splitlines() if "`OPTIONS_STRATEGY_HANDOFF_ERROR:`" in line
        )
        assert "control signal" in signal
        assert "never an answer" in signal
        assert "user's original wording verbatim" in signal

    def test_base_prompt_has_the_absent_route_rule(self) -> None:
        prompt = _read("prompts/central_hub_base.md")

        assert "not among your available tools" in prompt
        assert "say that capability's server is unavailable" in prompt
        assert "Do not substitute another specialist or your training data" in prompt

    def test_covered_call_and_wheel_are_no_longer_equity_objectives(self) -> None:
        assert _has_strategy_objective("covered call on SPY") is False
        assert _has_strategy_objective("covered-call on SPY") is False
        assert _has_strategy_objective("run the wheel on SPY") is False
        assert _has_strategy_objective("momentum on SPY") is True

    def test_tool_description_is_generic_and_routing_only(self) -> None:
        description = _OPTIONS_STRATEGY_TOOL_DESCRIPTION

        assert "load_skill('obai-options-strategy-routing') in the same turn" in description
        assert "`user_request` as the user's wording verbatim" in description
        assert "terminal author" in description
        for contract_token in ("**Status**", "SPXW", "DATA_ENTITLEMENT_MISSING", "Reference"):
            assert contract_token not in description

    def test_options_agent_handoff_names_current_market_scope_only(self) -> None:
        description = OptionsAgent().handoff_description

        assert "Use for any options-related queries" not in description
        assert "current" in description
        assert "options_strategy_analysis" in description

    def test_strategy_agent_handoff_names_equity_and_etf_share_strategies(self) -> None:
        description = StrategyAgent().handoff_description

        assert "equity and ETF share" in description
        assert "options_strategy_analysis" in description

    def test_new_skill_frontmatter_routes_options_strategies(self) -> None:
        skill = _read("hub_skills/obai-options-strategy-routing/SKILL.md")
        description = _skill_description("obai-options-strategy-routing")

        assert skill.startswith("---\nname: obai-options-strategy-routing\n")
        for case in ("historical performance", "covered calls", "wheels", "what options-strategy"):
            assert case in description
        assert "excludes current options chains" in description
        assert "equity or ETF share strategy backtests" in description

    def test_new_skill_documents_handoff_relay_and_unavailability(self) -> None:
        skill = _read("hub_skills/obai-options-strategy-routing/SKILL.md")

        for argument in _ARGUMENT_NAMES:
            assert f"`{argument}`" in skill
        assert "__TERMINAL_TOOL_OUTPUT__:options_strategy_analysis:" in skill
        assert "OPTIONS_STRATEGY_HANDOFF_ERROR:" in skill
        assert "e.g." not in skill

    def test_new_skill_names_the_absent_route_as_an_optional_component(self) -> None:
        """An absent tool is either not opted in or an opted-in server that is down.

        The hub cannot tell the two apart, so the skill names both; no command (ADR 0004 §7).
        """
        skill = _read("hub_skills/obai-options-strategy-routing/SKILL.md")

        assert "optional component" in skill
        assert "not enabled in this installation" in skill
        assert "its server is not running" in skill
        assert "options backtest server is unavailable" not in skill
        assert "--with-options-backtest" not in skill

    def test_strategy_routing_skill_excludes_options_structures(self) -> None:
        skill = _read("hub_skills/obai-strategy-routing/SKILL.md")

        assert "Excludes options-structure strategies" in _skill_description(
            "obai-strategy-routing"
        )
        row = next(line for line in skill.splitlines() if line.startswith("- Options-structure"))
        assert "`options_strategy_analysis`" in row
        assert "`options_analysis`" not in row

    def test_stock_synthesis_skill_excludes_the_new_terminal(self) -> None:
        assert "terminal options-strategy" in _skill_description("obai-stock-synthesis")
