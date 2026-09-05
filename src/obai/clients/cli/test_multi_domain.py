#!/usr/bin/env python3
"""Test script for multi-domain queries."""

import asyncio
import sys
from pathlib import Path

# Add OBaI root to path
obai_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(obai_root))

from agents.items import ItemHelpers, MessageOutputItem  # noqa: E402
from agents.stream_events import (  # noqa: E402
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from openai.types.responses import ResponseTextDeltaEvent  # noqa: E402

from core_agents.central_hub_agent import (  # noqa: E402
    CryptoPassthroughEvent,
    PredictionPassthroughEvent,
    StrategyPassthroughEvent,
    create_central_hub,
)
from core_agents.response_assembly import AnswerAccumulator  # noqa: E402


async def test_multi_domain() -> None:
    """Test a multi-domain query that requires multiple specialists."""
    print("\n" + "=" * 60)
    print("  Multi-Domain Query Test")
    print("=" * 60 + "\n")

    hub = await create_central_hub()
    assert hub.agent is not None

    # Multi-domain query: price + fundamentals + news
    query = "Analyze TSLA: show me the current price, P/E ratio, and any recent news"
    print(f"Query: {query}\n")
    print("-" * 60)

    # Same entry point as chat.py. Driving hub.agent directly misses the
    # sandbox run config the hub builds, and a SandboxAgent without one
    # raises UserError before a single event arrives.
    agents_seen: list[str] = []
    answer = AnswerAccumulator()
    passthrough: str | None = None

    async for event in hub.run(query):
        if isinstance(
            event,
            PredictionPassthroughEvent | CryptoPassthroughEvent | StrategyPassthroughEvent,
        ):
            passthrough = event.content
            continue

        if isinstance(event, AgentUpdatedStreamEvent):
            agent_name = event.new_agent.name
            if agent_name not in agents_seen:
                agents_seen.append(agent_name)
                print(f"\n🔀 Agent: {agent_name}")

        elif isinstance(event, RunItemStreamEvent):
            item = event.item
            if getattr(item, "type", None) == "tool_call_item":
                answer.reset()
            elif isinstance(item, MessageOutputItem):
                answer.note_message(
                    item.raw_item.id,
                    ItemHelpers.text_message_output(item),
                    item.raw_item.phase,
                )

        elif isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                answer.add_delta(data.item_id, data.delta)

    # Assembled the way chat.py assembles it, so this prints what a user sees.
    final = passthrough if passthrough is not None else answer.text()
    print("\n" + "=" * 60)
    print("  Final Response")
    print("=" * 60)
    print(final)
    print("\n" + "=" * 60)
    print(f"Agents used: {agents_seen}")
    print("=" * 60)

    await hub.close()


if __name__ == "__main__":
    asyncio.run(test_multi_domain())
