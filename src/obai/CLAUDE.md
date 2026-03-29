# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## OBaI Architecture Overview

**OBaI** (pronounced Obi-Wan) is a multi-agent financial research assistant using OpenAI Agent SDK with MCP (Model Context Protocol) for tool integration.

### Key Architectural Principles

1. **Bundled Deployment**: Agents + clients in single container (NOT microservices)
   - Communication within container: Direct Python imports
   - External communication: HTTP to MCP servers only

2. **Multi-Agent Hierarchy**:
   - 1 Central Hub Agent (routes queries, synthesizes responses)
   - 8 Specialist Agents (each connects to dedicated MCP server)
     - Market Data Agent → market-data-server (quotes, technicals)
     - Fundamentals Agent → fundamentals-server (financials, ratios)
     - Events/News Agent → events-news-server (news, earnings)
     - Options Agent → options-server (options chains, Greeks)
     - Screener Agent → screening-server (ticker lookup, screening)
     - Portfolio Agent → portfolio-server (positions, risk prefs, ETF holdings)
     - Strategy Agent → backtest-server (backtesting, strategy design)
     - Research Agent → research-server (semantic search, company research)

3. **File-Based Prompts**: Agent instructions stored in `core_agents/prompts/*.md`
   - Allows prompt iteration without code changes
   - Git-tracked with diffs
   - Loaded via `prompt_loader.py`

4. **Session Management**:
   - Client-side responsibility (NOT agent-side)
   - CLI: In-memory `SQLiteSession` (ephemeral)
   - Discord: Persistent `SQLiteSession` with thread IDs
   - Sessions passed to `Runner.run(session=...)`, NOT to agents

5. **Configuration**: Pydantic settings with environment variables
   - Model selection: `ORCHESTRATOR_MODEL` (gpt-5.1), `SPECIALIST_MODEL` (gpt-5-mini)
   - Per-agent overrides: `MARKET_DATA_MODEL`, `FUNDAMENTALS_MODEL`, etc.
   - Guardrails: `ENABLE_GUARDRAILS=true` (input validation)

## Critical Directory Structure

```
src/OBaI/                         # Bundled application root
├── core_agents/                       # Core agent system (importable package)
│   ├── central_hub_agent.py      # Routes to specialists, synthesizes
│   ├── {specialist}_agent.py     # 8 specialist agents
│   ├── guardrails.py            # Input validation (financial queries only)
│   ├── config.py                # Pydantic settings
│   ├── prompt_loader.py         # Load prompts from markdown files
│   ├── mcp/                     # MCP integration layer
│   │   ├── client.py            # Async HTTP client with retry
│   │   └── tool_converter.py   # MCP tools → Agent SDK format
│   └── prompts/                 # Agent instructions (markdown)
│       ├── central_hub.md
│       ├── guardrail.md
│       └── {specialist}.md
│
└── clients/                      # Client applications
    ├── cli/                      # CLI clients
    │   ├── tui.py               # Textual TUI app
    │   ├── chat.py              # Headless CLI (obai command)
    │   └── test_connection.py  # MCP server connectivity check
    └── discord/                  # Future Discord bot
```

## Development Commands

### MCP Servers (External Dependencies)

Start all 8 MCP servers before running agents:

```bash
# Terminal 1: Fundamentals (port 8001)
cd src/servers/fundamentals-server && uv run fastmcp run server.py

# Terminal 2: Market Data (port 8002)
cd src/servers/market-data-server && uv run fastmcp run server.py

# Terminal 3: Events/News (port 8003)
cd src/servers/events-news-server && uv run fastmcp run server.py

# Terminal 4: Options (port 8004)
cd src/servers/options-server && uv run fastmcp run server.py

# Terminal 5: Screening (port 8005)
cd src/servers/screening-server && uv run fastmcp run server.py

# Terminal 6: Portfolio (port 8006)
cd src/servers/portfolio-server && uv run fastmcp run server.py

# Terminal 7: Backtest/Strategy (port 8007)
cd src/servers/backtest-server && uv run fastmcp run server.py

# Terminal 8: Research (port 8008)
cd src/servers/research-server && uv run fastmcp run server.py
```

### Agent System

```bash
# Install dependencies
cd src/OBaI/agents
uv sync

# Run type checks (strict mode required)
uv run mypy . --strict

# Run linter/formatter
uv run ruff check . --fix
uv run ruff format .

# Run tests
uv run pytest
```

### CLI Test Client

```bash
# Set environment variables (required)
export OPENAI_API_KEY=sk-proj-...
export MCP_FUNDAMENTALS_URL=http://localhost:8001/mcp
export MCP_MARKET_DATA_URL=http://localhost:8002/mcp
export MCP_EVENTS_NEWS_URL=http://localhost:8003/mcp
export MCP_OPTIONS_URL=http://localhost:8004/mcp
export MCP_SCREENER_URL=http://localhost:8005/mcp
export MCP_PORTFOLIO_URL=http://localhost:8006/mcp
export MCP_BACKTEST_URL=http://localhost:8007/mcp
export MCP_RESEARCH_URL=http://localhost:8008/mcp
export ANTHROPIC_API_KEY=sk-ant-...  # Required for evaluation (LLM-judge)

# Test MCP connectivity
cd src/OBaI/clients/cli
python test_connection.py

# Headless CLI (obai command)
cd src/OBaI
obai query "What is AAPL trading at?"    # Single query
obai query "What is AAPL trading at?" --json  # JSON output
obai chat                                 # Interactive REPL
obai status                               # MCP server status

# Run TUI
uv run python -m clients.cli.tui
```

### Testing Workflow

```bash
# 1. Start all 8 MCP servers (see above)
# 2. Test connectivity
python src/OBaI/clients/cli/test_connection.py

# 3. Run comprehensive tests
# See TESTING.md for 11 test cases including:
# - Simple queries (single agent)
# - Multi-agent handoffs
# - Guardrails (valid/invalid queries)
# - Session memory (follow-up questions)
# - Session clear command
```

## Key Implementation Patterns

### Creating an Agent

```python
from core_agents import create_central_hub

# Initialize all 9 agents (central hub + 8 specialists)
hub = await create_central_hub()

# Use with Agent SDK Runner + Session
from openai.agents import Runner, SQLiteSession

session = SQLiteSession("session_id")
runner = Runner(agent=hub.agent)
result = await runner.run("What is AAPL trading at?", session=session)
```

### Adding/Modifying Agent Instructions

Edit markdown files in `core_agents/prompts/`:
- Changes take effect immediately (no code restart)
- Use template variables: `${model}`, `${timestamp_format}`
- Validation on load (required sections checked)

### MCP Tool Integration

Agents automatically get MCP tools via `MCPToolConverter`:
1. Agent initializes → connects to MCP server
2. `list_tools()` fetches available tools
3. Tools converted to Agent SDK format
4. Agent SDK handles tool execution

**Don't manually call MCP tools** - let Agent SDK handle it.

### Input Guardrails

Non-financial queries rejected before reaching central hub:
- Guardrail agent (gpt-5-mini) validates query
- If invalid: raises `InputGuardrailTripwireTriggered`
- If valid: passes to central hub
- Cost savings: ~$0.0048 per rejected query

Disable for development:
```bash
export ENABLE_GUARDRAILS=false
```

## Important Constraints

### What NOT to Do

1. **Don't create sessions in agents** - sessions are client-side
2. **Don't pass session to agent constructor** - pass to `Runner.run()`
3. **Don't use manual conversation history** - sessions handle it automatically
4. **Don't commit with `# type: ignore`** - fix type errors instead
5. **Don't modify agent code to add tools** - add to MCP server instead
6. **Don't hardcode prompts in Python** - use `prompts/*.md` files

### What TO Do

1. **Always initialize agents with `await agent.initialize()`** before use
2. **Always close agents with `await agent.close()`** when done
3. **Use context managers** for agent lifecycle:
   ```python
   async with hub:
       # Agent auto-initialized and auto-closed
   ```
4. **Import agents as package**: `from core_agents.central_hub_agent import create_central_hub`
5. **Pass sessions to Runner**, not to agents
6. **Edit prompts in markdown files**, not in code

## Configuration Reference

### Environment Variables (All Optional Except OPENAI_API_KEY)

```bash
# Required
OPENAI_API_KEY=sk-proj-...

# Required for evaluation (LLM-judge scorer uses Claude as cross-family judge)
ANTHROPIC_API_KEY=sk-ant-...

# MCP Server URLs (defaults to localhost:800X)
MCP_FUNDAMENTALS_URL=http://localhost:8001/mcp
MCP_MARKET_DATA_URL=http://localhost:8002/mcp
MCP_EVENTS_NEWS_URL=http://localhost:8003/mcp
MCP_OPTIONS_URL=http://localhost:8004/mcp
MCP_SCREENER_URL=http://localhost:8005/mcp
MCP_PORTFOLIO_URL=http://localhost:8006/mcp
MCP_BACKTEST_URL=http://localhost:8007/mcp
MCP_RESEARCH_URL=http://localhost:8008/mcp

# Model Configuration
ORCHESTRATOR_MODEL=gpt-5.1         # Default (needs strong reasoning)
SPECIALIST_MODEL=gpt-5-mini        # Default (cost-effective)
MARKET_DATA_MODEL=gpt-5-mini       # Override specific agent
FUNDAMENTALS_MODEL=gpt-5-mini      # Override specific agent
EVENTS_NEWS_MODEL=gpt-5-mini       # Override specific agent
OPTIONS_MODEL=gpt-5-mini           # Override specific agent
SCREENER_MODEL=gpt-5-mini          # Override specific agent
PORTFOLIO_MODEL=gpt-5-mini         # Override specific agent

# Features
ENABLE_GUARDRAILS=true             # Default (input validation)

# MCP Client
MCP_TIMEOUT=30                     # Seconds
MCP_MAX_RETRIES=2

# Logging
LOG_LEVEL=INFO                     # DEBUG for detailed output
```

### Per-Agent Model Selection Logic

```python
# config.py
def get_agent_model(self, agent_type: str) -> str:
    # 1. Check agent-specific override (e.g., MARKET_DATA_MODEL)
    # 2. Fall back to SPECIALIST_MODEL
    # Orchestrator always uses ORCHESTRATOR_MODEL
```

## Common Development Scenarios

### Adding a New Specialist Agent

1. Create new MCP server in `src/servers/`
2. Add agent file: `core_agents/new_agent.py`
3. Add prompt file: `core_agents/prompts/new_agent.md`
4. Add to `central_hub_agent.py` initialization
5. Add tool registration in central hub
6. Update config.py with new MCP URL

### Modifying Agent Behavior

**Don't edit agent code** - edit prompt markdown:
1. Open `core_agents/prompts/{agent_name}.md`
2. Modify instructions
3. Test with CLI client
4. Git commit the prompt change

### Debugging Agent Issues

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Run CLI client - shows:
# - Agent SDK tool calls
# - MCP HTTP requests/responses
# - Guardrail validation details
# - Agent handoff decisions
python src/OBaI/clients/cli/chat.py
```

### Testing Session Memory

```bash
# In CLI client:
You: What is AAPL trading at?
# (get response)

You: What's its P/E ratio?
# Should understand "its" = AAPL (session memory working)

You: clear
# Creates new session

You: What's its P/E ratio?
# Should ask "which stock?" (session cleared)
```

## Project-Specific Code Standards

In addition to general Python standards in root CLAUDE.md:

### Agent-Specific Patterns

1. **Agent Initialization**:
   ```python
   async def initialize(self) -> None:
       """Initialize agent and MCP client."""
       if self._initialized:
           return
       # ... initialization
       self._initialized = True
   ```

2. **Async Context Managers**:
   ```python
   async def __aenter__(self) -> "AgentClass":
       await self.initialize()
       return self

   async def __aexit__(self, *_args: Any) -> None:
       await self.close()
   ```

3. **MCP Client Usage**:
   ```python
   self.mcp_client = MCPClient(base_url=config.mcp_xxx_url)
   tools = await self.mcp_client.list_tools()
   converter = MCPToolConverter(self.mcp_client)
   agent_tools = [converter.create_tool(t) for t in tools]
   ```

### Configuration Access

```python
from core_agents.config import get_config

config = get_config()  # Singleton pattern
model = config.get_agent_model("market_data")
```

### Prompt Loading

```python
from core_agents.prompt_loader import load_prompt

instructions = load_prompt(
    "central_hub",
    model="${model}",
    timestamp="ISO8601"
)
```

## References

- Main README: `src/OBaI/README.md` - Quick start, architecture
- Testing Guide: `src/OBaI/TESTING.md` - 11 test cases, troubleshooting
- CLI README: `src/OBaI/clients/cli/README.md` - CLI usage, session memory
- Architecture Plan: `docs/DISCORD_BOT_MULTI_AGENT_PLAN.md` - Full design decisions


<claude-mem-context>
# Recent Activity

### Mar 25, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #247 | 8:28 PM | 🔵 | OBaI Project Packaging and Development Tooling Configuration | ~793 |
</claude-mem-context>
