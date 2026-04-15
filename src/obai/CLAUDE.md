# OBaI Agent System

## Project
- **Summary:** Multi-agent financial research assistant — 1 central hub agent routes to 9 specialist agents, each backed by a dedicated MCP server.
- **Stack:** OpenAI Agents SDK, FastMCP, Pydantic, structlog

## Architecture (decision-level only)
- **Bundled deployment:** Agents + clients in a single container. Direct Python imports within, HTTP to MCP servers externally.
- **Sessions are client-side.** Pass to `Runner.run(session=...)`, never to agent constructors.
- **Prompts live in `core_agents/prompts/*.md`.** Edit prompts there, not in Python code.
- **Tools come from MCP servers.** Don't add tools to agent code — add to the relevant MCP server.
- **Guardrails:** Input validation agent rejects non-financial queries before they reach the hub. Controlled by `ENABLE_GUARDRAILS` env var.
- **Config:** Pydantic settings via `core_agents/config.py`. Model selection: `ORCHESTRATOR_MODEL` (hub), `SPECIALIST_MODEL` (default for specialists), per-agent overrides like `MARKET_DATA_MODEL`.

## Known Pitfalls
- Update this section every time the repo teaches you the same lesson twice.