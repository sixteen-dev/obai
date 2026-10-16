"""The MCP client reads each tool's input schema under either SDK field name.

MCP SDK v2 renamed ``Tool.inputSchema`` to ``input_schema``. A schema silently
replaced by ``{}`` would present every tool as taking no arguments.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core_agents.mcp.client import MCPClient, MCPClientError

_SCHEMA = {"type": "object", "properties": {"symbol": {"type": "string"}}}


async def _list(tool: Any) -> list[dict[str, Any]]:
    client = MCPClient("http://localhost:1/mcp")
    client._client = AsyncMock()
    client._client.list_tools.return_value = [tool]
    return await client.list_tools()


@pytest.mark.asyncio
async def test_reads_the_v2_input_schema_field() -> None:
    """SDK v2 tools carry ``input_schema``."""
    tools = await _list(SimpleNamespace(name="q", description="", input_schema=_SCHEMA))

    assert tools[0]["inputSchema"] == _SCHEMA


@pytest.mark.asyncio
async def test_reads_the_v1_input_schema_field() -> None:
    """SDK v1 tools carry ``inputSchema``."""
    tools = await _list(SimpleNamespace(name="q", description="", inputSchema=_SCHEMA))

    assert tools[0]["inputSchema"] == _SCHEMA


@pytest.mark.asyncio
async def test_a_tool_without_a_schema_fails_loudly() -> None:
    """A missing schema raises instead of becoming ``{}``."""
    with pytest.raises(MCPClientError, match="input schema"):
        await _list(SimpleNamespace(name="q", description=""))
