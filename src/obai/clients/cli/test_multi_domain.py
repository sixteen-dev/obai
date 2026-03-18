#!/usr/bin/env python3
"""Test script for multi-domain queries."""

import asyncio
import sys
from pathlib import Path

# Add OBaI root to path
obai_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(obai_root))

from agents import Runner  # noqa: E402
from agents.stream_events import AgentUpdatedStreamEvent  # noqa: E402

from core_agents.central_hub_agent import create_central_hub  # noqa: E402


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

    # Use streamed runner (same as chat.py)
    agents_seen: list[str] = []

    result = Runner.run_streamed(
        starting_agent=hub.agent,
        input=query,
    )

    async for event in result.stream_events():
        if isinstance(event, AgentUpdatedStreamEvent):
            agent_name = event.new_agent.name
            if agent_name not in agents_seen:
                agents_seen.append(agent_name)
                print(f"\n🔀 Agent: {agent_name}")

    # Get final output
    final = result.final_output
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
