# Changelog

All notable changes to OBaI will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [1.4.0b2] - 2026-05-08 (beta)

Iteration on the 1.4.0b1 beta. Same release line; promote 1.4.0 final only
after the beta tag validates.

### Changed

- **Strategy specialist default model reverted to `gpt-5.1`** (from `gpt-5.5`).
  Trace comparison on the same query showed `gpt-5.1` reaches the same
  verdict and operator selection ~$0.13/query cheaper, with the trade-off
  of ~18s additional latency. Set `STRATEGY_MODEL=gpt-5.5` to keep the
  earlier default.
- **Hub default verbosity lowered to `low`** (from `medium`). Reduces
  conversational filler in the user-facing answer without affecting tool
  routing.
- **Strategy-routing skill: header allowlist promoted, denylist removed.**
  The handoff-format section now leads with the rule that only `User
  request:` and `Strategy context:` are valid top-level headers; the prior
  10-item denylist of forbidden header names is replaced by the
  allowlist. New `Preferences:` bullet inside `Strategy context:`
  formalizes how the hub passes saved user preferences (benchmark, capital,
  risk tolerance, horizon) to the strategy specialist — preventing the
  hub from inventing ad-hoc preference sections like
  `Routing context/preferences:` or `Desired strategy constraints:`.
- **Strategy-routing skill: terminal-marker strip rule tightened.** Adds
  an explicit instruction to strip the `__TERMINAL_TOOL_OUTPUT__:strategy_analysis:<status>`
  control marker (and the blank line after it) from the hub's relayed
  output. Fixes a leak where the marker prefix appeared at the top of
  user-visible strategy responses.

### Fixed

- **Web client: thinking-break fired on tool-call output as well.** The
  hub-to-UI bridge now flushes accumulated narration as "thinking" on
  both `tool_call_item` and `tool_call_output_item` events, since
  `text_delta` chunks can arrive between the two and were previously
  leaking into the saved final response.
- **Web client: agent label restored when switching back to a running
  session.** Session-scoped `lastAgentBySession` map tracks the active
  agent label per session so that switching to a still-streaming session
  shows the correct trail label rather than a blank "Thinking" stub.
  Cleanup on `complete` / `error` / session delete prevents leaks.

### Infra

- Pin Opik docker images (`opik-backend`, `opik-python-backend`,
  `opik-frontend`) to `2.0.27` instead of `:latest` for reproducible
  local Opik bring-up.

## [1.4.0b1] - 2026-05-07 (beta)

First beta of the 1.4 line. Validates a major upgrade to the hub runtime,
the agent prompt structure, and the underlying SDK. Promote to 1.4.0 final
only after beta validation completes; do not move the beta tag.

### Highlights

- **Central Hub now runs as a SandboxAgent with lazy-loaded skills.** The
  Hub's previous 282-line monolithic prompt is replaced by a compact
  100-line `central_hub_base.md` plus five skills under
  `core_agents/hub_skills/` that the model loads conditionally based on
  the turn. Skill metadata (name + description) is read eagerly; full
  bodies are fetched only when the model decides a skill applies.
  Toggle via `ENABLE_SANDBOX_HUB` (default true). Falls back to the
  legacy plain-Agent Hub when disabled.
- **Default orchestrator model bumped to `gpt-5.5`** (from `gpt-5.1`).
  Performing well in early beta runs against the SandboxAgent + skill
  structure on routing, synthesis, and pred-market relay turns; formal
  benchmarks vs 5.1 are pending. **Expect higher API cost than 5.1.**
  Set `ORCHESTRATOR_MODEL=gpt-5.1` to keep the previous defaults if
  cost is a hard constraint.
- **openai-agents SDK 0.14.2 → 0.16.0.** Picks up MCP fixes, explicit
  `ModelRefusalError` handling, server-prefixed MCP tool naming, and
  per-run tool-execution concurrency. All `Agent` / `SandboxAgent`
  instantiations pass `model=` explicitly, so the new 0.16 implicit
  default model has no effect on existing configs.

### Added

- Five hub skills under `core_agents/hub_skills/`:
  `obai-grounding-and-cache`, `obai-prediction-market-routing`,
  `obai-research-routing`, `obai-stock-synthesis`,
  `obai-strategy-routing`. Skills are routing-and-format guidance
  loaded lazily per turn.
- Tunable reasoning effort and output verbosity per tier
  (orchestrator vs specialist) on `AgentConfig`, mirroring the existing
  model-name configuration pattern. Defaults: hub `high` + `medium`,
  specialist `medium` + `low`. Edit `core_agents/config.py` to tune.
- Always-passthrough relay gate for `prediction_market_analysis`. The
  runtime emits the specialist output verbatim and drops any text the
  Hub authors after the tool returns, fixing the long-standing
  prediction-market output compression regression. Fix is
  architectural (runtime layer) so it survives future prompt drift.
- Ticker-not-found → `screener_lookup` fallback rule in the central hub
  base prompt.
- E2E regression harness under `.agents/skills/obai-e2e-regression`
  with a curated 32-case suite and per-case Opik trace resolution.

### Changed

- Forward-looking and hypothetical questions now require gathering
  evidence from specialists first. Hub no longer answers from model
  memory when a specialist could ground the answer.
- Hub may ask at most one clarification question at a time;
  multi-question decompositions are out.
- Prediction-market follow-up routing-key priority: `slug` first, then
  `market_url`, then the exact market question.
- `obai-stock-synthesis` skill description broadened to cover any
  non-terminal evidence-supplier synthesis (was previously too
  stock-shaped and missed thematic and research-only paths).
- **opik upgraded** to pick up the dynamic CDN-driven model price
  registry, restoring accurate cost capture for `gpt-5.5` (and any
  other recent model) without requiring an SDK pin update.

### Deprecated / Disabled

- Qdrant educational-PDF search disabled by default. The
  `fundamentals_search_education_tool` is not registered when
  `QDRANT_ENABLED=false`; the inline Qdrant lookups in
  `get_fundamentals` and `get_company_profile` are short-circuited on
  the same flag. `QdrantVectorClient`, `vector_search.py`, the
  `qdrant-client` dependency, and the PDF seed scripts remain in tree
  so the feature can be re-enabled by setting `QDRANT_ENABLED=true`
  and uncommenting the qdrant block in `docker-compose.yml`.
- `setup.sh --local` now passes `--remove-orphans` to
  `docker compose up`, so a previously running `obai-qdrant` container
  is torn down on the next setup run.

### Known issues / beta watch-items

- gpt-5.5 per-query cost is materially higher than gpt-5.1. Use Opik
  per-trace cost (now reported correctly with the 2.x bump) to size
  the impact for your usage profile.
- Strategy specialist currently inherits the `specialist` reasoning
  tier (`medium` effort). If strategy benchmarks regress versus the
  prior gpt-5.1 hub, the next lever is dialing strategy-specific
  reasoning to `high`.
- `python-multipart 0.0.26` has GHSA-pp6c-gr5w-3c5g (DoS via unbounded
  multipart headers); upgrade to `0.0.27` queued for `1.4.0b2`.

## [0.9.0] - 2026-03-31

### Added
- Initial versioned release
- CLI with `obai query`, `obai chat`, `obai status`, `obai tui` commands
- 8 MCP data servers: fundamentals, market-data, events-news, options, screening, portfolio, backtest, research
- Multi-agent architecture with central hub routing to specialist agents
- Opik tracing integration for observability
- Faithfulness and completeness evaluation scoring
- User preferences system (`~/.obai/preferences.json`)
- Research agent with Exa semantic search
- Automated setup/teardown scripts

[Unreleased]: https://github.com/sixteen-dev/obai/compare/v1.4.0b1...HEAD
[1.4.0b1]: https://github.com/sixteen-dev/obai/releases/tag/v1.4.0b1
[0.9.0]: https://github.com/sixteen-dev/obai/releases/tag/v0.9.0
