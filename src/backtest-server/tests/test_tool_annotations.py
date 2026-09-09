"""Backtest MCP tool annotation tests."""

from __future__ import annotations

from src import server


async def test_job_status_opts_out_of_the_hub_result_cache() -> None:
    """Job status must not advertise idempotency.

    The hub caches a converted tool's result for minutes when the tool
    advertises both readOnlyHint and idempotentHint, which pins a polled job at
    the first status it returned. Job state is mutable, so this tool opts out.
    """
    tools = await server.mcp.list_tools()

    status_tool = next(t for t in tools if t.name == "backtest_get_job_status_tool")
    assert status_tool.annotations is not None
    assert status_tool.annotations.readOnlyHint is True
    assert status_tool.annotations.idempotentHint is False
