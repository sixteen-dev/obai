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
- **Config:** Pydantic settings via `core_agents/config.py`. Model selection: `ORCHESTRATOR_MODEL` (hub), `SPECIALIST_MODEL` (default for specialists), per-agent overrides like `MARKET_DATA_MODEL`. Reasoning effort mirrors it: `ORCHESTRATOR_REASONING_EFFORT`, `SPECIALIST_REASONING_EFFORT`, per-agent `STRATEGY_REASONING_EFFORT` etc., all `none|low|medium|high|xhigh|max` (`minimal` is rejected by every gpt-5.6 model — don't re-add it).
- **Hub settings are user-owned; specialists are code-owned.** `core_agents/hub_settings.py` owns `~/.obai/settings.json` (`hub_model`, `hub_reasoning_effort`), written by the web UI settings modal and `obai config set-model` / `set-effort`. `_HubSettingsSource` sits *below* env in `settings_customise_sources`, so `ORCHESTRATOR_MODEL`/`ORCHESTRATOR_REASONING_EFFORT` still win — the eval A/B and E2E gate depend on that. Any surface that writes the file must warn when the matching env var is set. The web server hot-applies the change to its own hub via `CentralHubAgent.apply_hub_settings` (skipping env-pinned fields); `HubBridge.apply_hub_settings` must never *wait* on the query lock — `run_query` holds it for a whole streamed answer — so it queues mid-answer changes for the next query and reports `pending_apply`. Every other client builds its hub once and picks the change up on next launch. Absent/empty file = shipped defaults; a corrupt file raises `ValueError` and must be reported, never swallowed.

## Known Pitfalls
- Update this section every time the repo teaches you the same lesson twice.
- Hub pre-flight gates may check hard scope facts only (unsupported venue/instrument, missing symbol). Never regex-classify fuzzy intent (export eligibility, follow-ups) in the hub — context words ("paper", "artifact") false-positive; that classification belongs to the skill + specialist.
- When a preflight pattern matches a generated identifier, fixture the id from a captured run, never an invented shape. The crypto artifact gate was "fixed" once against a made-up readable id (`..._coinbase_..._v1`) while the engine mints bare SHA-256 digests, so stored-artifact validation stayed broken until a benchmark replayed the real handoff.