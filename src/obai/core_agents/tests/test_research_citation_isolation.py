"""Tests that each research invocation verifies citations against its own evidence.

The hub runs specialist tools in parallel, so two `research_analysis` calls
can be in flight at once. Both used to read their evidence as a slice of one
shared module list, which meant a fabricated URL in one answer passed
verification whenever the sibling call happened to retrieve it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from agents import FunctionTool, Runner
from agents.tool_context import ToolContext

from core_agents import central_hub_agent
from core_agents.central_hub_agent import (
    _UNVERIFIED_URL_MARKER,
    CentralHubAgent,
    _record_inner_tool_output,
    get_inner_tool_outputs,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

URL_A = "https://example.com/alpha-filing"
URL_B = "https://example.com/beta-filing"


@pytest.fixture(autouse=True)
def _clean_capture() -> Iterator[None]:
    """Keep the module-level scoring list from leaking between tests."""
    central_hub_agent._inner_tool_outputs.clear()
    yield
    central_hub_agent._inner_tool_outputs.clear()


class _FakeRun:
    """Stand-in for a streamed specialist run that retrieves one URL."""

    def __init__(self, url: str, answer: str, barrier: asyncio.Barrier) -> None:
        self._url = url
        self._barrier = barrier
        self.final_output = answer

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """Record this run's retrieved URL, then wait for the sibling run."""
        _record_inner_tool_output(
            {
                "specialist": "Research Agent",
                "tool_name": "web_search",
                "output": {"results": [{"url": self._url}]},
            }
        )
        # Both runs have now recorded, so each one's evidence is visible to
        # the other if the capture is shared. This is the interleaving the
        # slice-based capture could not survive.
        await self._barrier.wait()
        return
        yield {}  # pragma: no cover - makes this an async generator


def _research_tool(
    monkeypatch: pytest.MonkeyPatch,
    runs: dict[str, _FakeRun],
) -> FunctionTool:
    """Build the research tool with its specialist run and streaming stubbed."""

    async def _no_stream(_payload: dict[str, Any]) -> None:
        return None

    def _fake_run_streamed(*, starting_agent: Any, input: str, context: Any) -> _FakeRun:  # noqa: A002, ARG001
        return runs[input]

    monkeypatch.setattr(central_hub_agent, "_create_stream_handler", lambda *_: _no_stream)
    monkeypatch.setattr(Runner, "run_streamed", _fake_run_streamed)

    hub = object.__new__(CentralHubAgent)
    hub.research_agent = SimpleNamespace(agent=SimpleNamespace(name="stub_research"))  # type: ignore[assignment]
    tool = hub._build_research_tool()  # noqa: SLF001
    assert isinstance(tool, FunctionTool)
    return tool


async def _invoke(tool: FunctionTool, query: str) -> str:
    """Call the tool the way the SDK does, with a real tool context."""
    arguments = json.dumps({"input": query})
    context: ToolContext[None] = ToolContext(
        context=None,
        tool_name=tool.name,
        tool_call_id=f"call_{query}",
        tool_arguments=arguments,
    )
    return str(await tool.on_invoke_tool(context, arguments))


def test_a_parallel_call_cannot_lend_its_retrieved_url_to_a_fabricated_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call A cites a URL only call B retrieved; it must still be redacted."""
    barrier = asyncio.Barrier(2)
    runs = {
        "alpha": _FakeRun(URL_A, f"Alpha stands at 4%. Sources: {URL_A} and {URL_B}", barrier),
        "beta": _FakeRun(URL_B, f"Beta stands at 7%. Source: {URL_B}", barrier),
    }
    tool = _research_tool(monkeypatch, runs)

    async def _both() -> tuple[str, str]:
        alpha, beta = await asyncio.gather(_invoke(tool, "alpha"), _invoke(tool, "beta"))
        return alpha, beta

    alpha, beta = asyncio.run(_both())

    assert URL_A in alpha
    assert URL_B not in alpha
    assert _UNVERIFIED_URL_MARKER in alpha
    # B cited only what B retrieved, so its answer must come through untouched.
    assert URL_B in beta
    assert _UNVERIFIED_URL_MARKER not in beta


def test_parallel_calls_still_record_all_evidence_for_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-call scoping must not narrow what the faithfulness scorer sees."""
    barrier = asyncio.Barrier(2)
    runs = {
        "alpha": _FakeRun(URL_A, f"Alpha. Source: {URL_A}", barrier),
        "beta": _FakeRun(URL_B, f"Beta. Source: {URL_B}", barrier),
    }
    tool = _research_tool(monkeypatch, runs)

    async def _both() -> tuple[str, str]:
        alpha, beta = await asyncio.gather(_invoke(tool, "alpha"), _invoke(tool, "beta"))
        return alpha, beta

    asyncio.run(_both())

    recorded = json.dumps(get_inner_tool_outputs())
    assert URL_A in recorded
    assert URL_B in recorded


def test_recording_outside_an_invocation_only_reaches_the_scoring_list() -> None:
    """Cache and passthrough writes happen with no invocation in scope."""
    _record_inner_tool_output(
        {"specialist": "cache", "tool_name": "semantic_cache", "output": "cached"}
    )

    assert get_inner_tool_outputs() == [
        {"specialist": "cache", "tool_name": "semantic_cache", "output": "cached"}
    ]
