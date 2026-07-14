# Changelog

All notable changes to OBaI will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [1.5.4] - 2026-07-13

Adds first-class lifecycle commands to the `obai` CLI so start, stop, restart,
and upgrade no longer require running the shell scripts by hand.

### Added

- **`obai start` / `obai stop` / `obai restart`** drive the Docker services
  (MCP + Opik) and the web UI by delegating to `setup.sh` / `teardown.sh` (the
  single source of truth). `stop` preserves Docker images and data volumes.
- **`obai upgrade`** pulls the latest version, re-pulls the version-tagged
  images, and restarts — prompting first (`-y`/`--yes` skips it). Managed
  one-liner installs fast-forward the release branch; **source checkouts are
  never reset, recloned, or stashed** — only a clean, strictly-behind current
  branch is fast-forwarded, and dirty / diverged / detached-HEAD states refuse
  with guidance. Install mode is recorded in `~/.obai/install-manifest.json`.

### Security

- Pinned `click>=8.3.3` (PYSEC-2026-2132) and `pillow>=12.3.0`
  (PYSEC-2026-2253/2254/2255/2256/2257) in the root lock; `uv audit` is clean.

### Fixed

- README hub/orchestrator model reference corrected `gpt-5.5` → `gpt-5.6-sol`
  (the actual default since 1.5.3).

### Package versions

- Product line (root, `obai`, `crypto-server`): `1.5.3 → 1.5.4`.

## [1.5.3] - 2026-07-11

Security patch release. Closes a cross-site WebSocket hijacking gap in the web
UI, stops the FMP API key from reaching logs on upstream HTTP errors, and clears
a `joserfc` advisory in the crypto-server lock. Also lands the hub model bump and
per-agent reasoning-effort overrides.

### Security

- **Cross-Site WebSocket Hijacking (CSWSH) on the web UI `/ws` endpoint.** The
  Origin-guard middleware only runs for HTTP-scope requests, so the WebSocket
  handshake was unauthenticated — a malicious browser tab could open the socket,
  drive the hub with the user's keys, and read the streamed responses. The
  handshake now rejects any non-local `Origin` before `accept()`.
- **FMP API key written to logs on upstream HTTP errors** (screening- and
  fundamentals-server). httpx embeds the key-bearing request URL in
  `HTTPStatusError` text; `log_error` now redacts URLs from the message and
  suppresses the traceback for those errors (CWE-532).
- **`joserfc` advisory GHSA-gg9x-qcx2-xmrh** (HS256/384/512 verify accepts an
  empty/nil HMAC key, fixed in 1.6.8). Raised the floor to `>=1.6.8` across all
  fastmcp manifests and re-locked; crypto-server moved off the stale 1.6.7 pin to
  1.7.2. `uv audit` is clean across every service.

### Changed

- **Default hub (orchestrator) model → `gpt-5.6-sol`** (from `gpt-5.5`).
- **Hub reasoning effort → `medium`** (from `high`); **strategy, crypto, and
  prediction-markets specialists → `high`** via new per-agent reasoning-effort
  overrides. Other specialists remain `medium`.

### Package versions

- Product line (root, `obai`, `crypto-server`): `1.5.2 → 1.5.3`.
- `screening-server`: `0.2.1 → 0.3.0`; `fundamentals-server`: `0.1.9 → 0.2.0`.

## [1.4.1] - 2026-05-23

Stable graduation of the 1.4.1 line. Adds the prediction-markets historical
analytics layer on top of 1.4.0, lands a wide cross-server audit pass, and
tightens the prediction-markets agent's routing and tool-feedback discipline.
A second wave (2026-05-18 → 2026-05-23) ships intermediate exits on the
structured backtester, fixes correctness gaps surfaced by the regression
suite, and removes the cold-cache timeout cliff. The `1.4.1b1` beta carried
the same commits; this entry is the single read-this-first summary for the
line.

### Highlights

- **Prediction-markets historical analytics layer.** New tool surface for
  resolved-market analysis: `analyze_prediction_calibration` (per-bucket +
  per-category reliability with `low_n` discipline and a `categories`
  fan-out parameter), `analyze_longshot_bias`, `backtest_prediction_rule`
  (structured rule schema with explicit `volume_filter_mode` contamination
  contract), `monte_carlo_prediction_risk`, and `estimate_empirical_kelly`.
  Server-side category + date filters; ISO date inputs fail loud;
  coverage timestamps promoted to UTC-aware at the DB boundary.
- **Cross-server audit pass (P0-P2, batches 1-8).** Multi-week sweep
  across every specialist server fixing data-quality, error-handling,
  and resource-management gaps. Highlights: web XSS, tool cache shape,
  sell-side risk controls, options BSM dividend yield + IV bracketing,
  research Exa client leak, jobs persistence, trade-log cache reuse,
  backtest capital + benchmark alignment, options breakeven normalize,
  market-data retry, BBANDS/BETA, portfolio NaN guards, MCP
  timeout/retry, autotrader market hours + buying power + session days.
- **Prediction-markets agent upgrade.** Default model pinned to
  `gpt-5.1` (from `specialist_model` fallback = `gpt-5-mini`). Prompt
  carries the `backtest_prediction_rule` filter schema so the LLM
  stops sending unsupported date bounds and remembers
  `volume_filter_mode='lifetime_static'` is required with
  `min_lifetime_volume`. Tool-feedback rule broadened from "filter not
  honored" to "errors, times out, or ignores a filter" and now
  explicitly bans identical retries.
- **MCP infra tightening.** MCP response cap raised to 40k tokens; web
  message bubbles widened; MCP timeout + retry behavior unified;
  `python-multipart` bumped to 0.0.27 (GHSA-pp6c-gr5w-3c5g).
- **Dependabot sweep.** `litellm` 1.83.7 → 1.85.0
  (GHSA-wxxx-gvqv-xp7p, sandbox escape in custom-code guardrail);
  `authlib` 1.7.0 → 1.7.2 across all per-server lockfiles
  (GHSA-r95x-qfjj-fjj2, OIDC open redirect);
  `urllib3` 2.6.x → 2.7.0 in events-news and fundamentals server
  lockfiles (GHSA-mf9v-mfxr-j63j + GHSA-qccp-gfcp-xxvc, decompression
  bomb + sensitive header forwarding).

### Added (post-2026-05-18 refinements)

- **Stop / take-profit / max-hold intermediate exits on `backtest_prediction_rule`.**
  `ExitRule` widened into a discriminated union of `HoldToResolutionExit`
  and the new `StopTakeProfitExit`. The engine walks sampled rows after
  entry; the first crossing of stop, take-profit, or max-hold wins,
  booking PnL at the observed sample price. Cross-field validator
  rejects rules whose entry band could itself satisfy a trigger. Trades
  carry `exit_reason` + `time_to_exit_days`; the response gains an
  `exit_breakdown` block (count / share / avg_return_on_cost /
  median_time_to_exit_days per `stop` / `take_profit` / `expiry` /
  `resolution`, plus `win_rate_at_resolution` on the `resolution` slot)
  and surfaces the three fidelity caveats (intra-bucket triggers,
  sampled-row exit prices, zero market impact) in `limitations`.
- **`no_returns_to_simulate` quality flag.** Fires whenever
  `observations_used == 0` so callers know the empty
  `monte_carlo_input.returns` array is a designed terminal state, not a
  transport bug. The prediction-markets prompt short-circuits on it,
  responding in ≤10 lines with the dominant skip reason + one
  next-step suggestion instead of dumping placeholder zero metrics.
- **Simulator-side skip reasons now surface in responses.**
  `_skipped_counts` was only forwarding `ambiguous`/`unresolved` and
  silently dropping `no_eligible_entry`, `ttr_min_unmet`,
  `ttr_max_exceeded`, `no_exit_price_for_max_hold`, and
  `missing_price_history`. Each now passes through so the response
  explains where every selected market dropped out.

### Changed

- **Prediction-markets agent leads with the answer.** Response-modes
  preamble + reworked Backtest summary tell the agent to put the
  metric, decision, or outcome in the first one-or-two sentences;
  setup, filters, and limitations follow as supporting context, not
  preamble.
- **Agent prompt now restrains `max_markets`.** First historical-tool
  call omits `max_markets` so the default (100) binds; only pass it
  on a retry after a tool response shows the cap was binding, and
  never above 250 in a single call. Previously the agent was
  proactively bumping to 500 on broad-window queries and triggering
  MCP timeouts.

### Performance

- **CLOB backfill concurrency 5 → 20.** The fetch endpoint is a public
  read path with no observed per-IP throttle at our request volume;
  widening the semaphore cuts cold-cache backfill latency ~4x without
  tripping rate limits.
- **Sticky `no_clob_history` cache flag.** Polymarket CLOB returns an
  empty `history` array for ~50% of closed binary-market tokens
  (favorite sides priced to 1.0 with no on-CLOB activity). The
  downloader tags those meta rows with
  `quality_flags="no_clob_history"`; `classify_cache_action`
  short-circuits to `cached` / reason `sticky_no_clob_history` within
  the freshness window. Cuts repeat-fetch cost roughly in half once
  the cache has been seeded. Late CLOB activity is still picked up
  after `prediction_data_freshness_hours`.

### Fixed

- **Zero or one as entry price bounds.** `EntryRule.price_min/max` used
  `ge=0.0/le=1.0`; combined with the stop_take_profit exit math
  (`return_on_cost = pnl / entry_price`), a band like `[0.0, 0.05]`
  plus a sampled 0.0 YES price crashed the engine with
  `ZeroDivisionError`. Tightened to `gt=0.0/lt=1.0` to match the
  existing constraints on `StopTakeProfitExit` trigger prices.
- **`max_hold_days` lost precedence to later stop/TP triggers.**
  `_trigger_for_row` checked stop, then take-profit, then expiry. A
  row that both crossed take-profit and sat past the max-hold boundary
  got labeled `take_profit`, even though the trade had logically
  expired first. Reorder so boundary wins when the row is at or past
  it; `exit_breakdown` now attributes max-hold exits to `expiry`.
- **Stop_take_profit limitation string contradicted itself.**
  `_limitations` always emitted "Exit = hold to resolution; no
  historical order-book depth or fees modeled.", then appended the
  stop/take-profit caveats — contradictory user-facing text. The exit
  line is now conditional on the rule's exit variant.

### Security

- **`idna` 3.11 → 3.16** (GHSA-65pc-fj4g-8rjx). DoS via crafted inputs
  that bypassed the CVE-2024-3651 fix. Constraint added to every
  per-server pyproject so all 11 lockfiles refresh in lockstep.
- **`starlette` → 1.0.1** (GHSA-86qp-5c8j-p5mr / PYSEC-2026-161).
  Missing Host header validation poisoned `request.url.path` and
  bypassed path-based security checks; fix added to the same
  constraint block as `idna`.
- **Removed unused `asyncio-mqtt`.** The package was archived upstream
  and never imported anywhere in the codebase; dropped from the root
  `pyproject.toml` `all` dependency group (`paho-mqtt` followed it
  out of the lockfile as a transitive). Clears the only remaining
  `uv audit` adverse-status warning.

### Infra

- **PM server DuckDB persists across container restarts.** The
  prediction-markets-server's `/app/data/` directory now mounts a
  `prediction-data` named volume (same pattern as `backtest-server`).
  Without this, every restart wiped the cache and the next
  historical-analytics call paid full cold-cache cost. Existing
  containers need `docker compose up -d` to pick up the mount.
- **Local pre-commit `uv audit` demoted to advisory.** CI's
  `dependency-audit` job (`.github/workflows/security.yml`) remains
  the authoritative gate; the local hook was treating every
  non-zero exit as a vulnerability, including upstream OSV parser
  flakes on malformed advisory records.

### Notes

- A short-lived `feat(kb)` knowledge-base MCP server landed on 2026-05-15
  and was reverted the next day. Not part of this beta.

## [1.4.0] - 2026-05-09

Stable graduation of the 1.4 line. The 1.4 work is a near-total rewrite of
how the Central Hub reasons about a query: from one 282-line monolithic
prompt loaded on every turn to a compact base prompt plus lazy-loaded
skills selected per intent, on a newer Agents SDK with explicit per-agent
reasoning controls and an end-to-end regression harness. Beta history is
preserved in the `1.4.0b1` and `1.4.0b2` entries; this entry is the
single read-this-first summary for the line.

### Headlines

- **Central Hub now runs as a `SandboxAgent` with lazy-loaded skills.**
  The hub's prior 282-line monolithic prompt is replaced by a compact
  ~100-line `central_hub_base.md` plus five skills under
  `core_agents/hub_skills/`. The model reads skill front-matter
  metadata eagerly and loads bodies on demand based on the query's
  intent (routing, synthesis, strategy, research, prediction-market).
  Per-turn system prompt drops from 282 lines always-loaded to ~100
  lines plus whatever skill the turn actually needs. Cheap routing
  turns stay cheap; domain-heavy turns pay context only when they
  have to.
- **OpenAI Agents SDK 0.9.x → 0.16.0.** Seven minor versions
  consolidated: `SandboxAgent` (the headliner above),
  server-prefixed MCP tool naming, explicit `ModelRefusalError`,
  client-side sessions (passed per-`Runner.run` call rather than at
  agent construction), and a `ModelSettings` reorganization with
  reasoning-effort and verbosity knobs. All `Agent`/`SandboxAgent`
  instantiations pass `model=` explicitly so 0.16's implicit
  default-model change is a no-op for existing configs.
- **Default orchestrator model bumped to `gpt-5.5`** (from `gpt-5.1`).
  Better routing and synthesis on the lazy-skills + multi-specialist
  shape. Strategy specialist held back at `gpt-5.1` after a
  same-query trace comparison showed identical verdict, operators,
  and trade count at ~$0.13/query lower cost, with ~18s additional
  latency on an already long-running turn. Other specialists stay
  on `gpt-5-mini`.
- **Allowlist + runtime gate for the strategy handoff format.** The
  strategy-routing skill now enforces a two-block contract
  (`User request:` + `Strategy context:`) with a runtime gate that
  rejects any handoff missing a required header. The error is
  model-readable so retries land cleanly on the second call.
  Replaces the prior whack-a-mole denylist of forbidden
  hub-authored header names.
- **E2E regression harness.** Curated 32-case suite under
  `.agents/skills/obai-e2e-regression` with per-case Opik trace
  resolution and a deterministic three-layer rubric (trace flow →
  specialist output → hub final output). Now ships an HTML report
  renderer alongside the markdown report.

### Added

- Five hub skills under `core_agents/hub_skills/`:
  `obai-grounding-and-cache`, `obai-prediction-market-routing`,
  `obai-research-routing`, `obai-stock-synthesis`,
  `obai-strategy-routing`. Each skill carries routing rules,
  output-format expectations, and domain-specific state for one
  kind of query.
- Tunable reasoning effort and output verbosity per tier
  (orchestrator vs specialist) on `AgentConfig`, mirroring the
  per-agent model-name pattern. Defaults: hub `high` + `low`,
  specialist `medium` + `low`.
- Always-passthrough relay gate for `prediction_market_analysis`. The
  runtime emits the specialist output verbatim and drops any text
  the Hub authors after the tool returns — an architectural fix at
  the runtime layer that survives prompt drift.
- Kalshi as a second prediction-market venue alongside Polymarket.
- `Preferences:` bullet inside `Strategy context:` formalizes how
  the hub passes saved user preferences (benchmark, capital, risk
  tolerance, horizon) to the strategy specialist.

### Changed

- Forward-looking and hypothetical questions now require gathering
  evidence from specialists first; the Hub no longer answers from
  model memory when a specialist could ground the answer.
- Hub may ask at most one clarification question at a time.
- Prediction-market follow-up routing-key priority: `slug` →
  `market_url` → exact market question.
- `obai-stock-synthesis` skill broadened to cover any non-terminal
  evidence-supplier synthesis (was previously too stock-shaped).
- Strategy specialist's terminal control marker
  (`__TERMINAL_TOOL_OUTPUT__:strategy_analysis:<status>`) now
  stripped from user-facing output by an explicit skill rule.
- Hub default verbosity lowered to `low` to reduce conversational
  filler in the user-facing answer.
- Opik project env var renamed `OPIK_PROJECT` →
  `OPIK_OBAI_PROJECT_NAME` to avoid implicit collision with the
  Opik SDK's own `OPIK_PROJECT_NAME`. The Pydantic field stays
  `opik_project`. No legacy alias — update your `.env`.
- Opik prompt manager passes `project_name` explicitly through the
  client constructor and to `create_prompt`, `get_prompt`, and
  `get_prompt_history`, silencing Opik 2.x's workspace-wide-search
  deprecation warning.
- opik upgraded to `2.0.23` for the CDN-driven model price registry,
  restoring accurate cost capture for `gpt-5.5` and other recent
  models without an SDK pin update.

### Removed

- Legacy plain-Agent Hub path. `central_hub.md` (the 282-line
  monolithic prompt) and the `ENABLE_SANDBOX_HUB` env toggle are
  gone. SandboxAgent + lazy-skills is the only Hub runtime path.
- Strategy-routing denylist of forbidden hub-authored headers.
  Replaced by the allowlist headline above.

### Fixed

- Strategy terminal-marker leak (the `__TERMINAL_TOOL_OUTPUT__`
  prefix appearing at the top of user-facing strategy responses).
- Web client: thinking-break now also fires on `tool_call_output_item`
  events, so late-arriving `text_delta` chunks between the tool
  call and its output don't leak into the saved final response.
- Web client: agent label restored when switching back to a
  still-running session, via a session-scoped `lastAgentBySession`
  map; cleanup on `complete`/`error`/session delete prevents leaks.
- Faithfulness scorer tests stabilized via mocked
  `ANTHROPIC_API_KEY` fixture so judge calls reach the patched
  `structured_completion` path under CI.

### Disabled

- Qdrant educational-PDF search off by default
  (`QDRANT_ENABLED=false`). `QdrantVectorClient`, `vector_search.py`,
  the seed scripts, and the `qdrant-client` dependency remain in
  tree; toggle on by setting the flag and uncommenting the qdrant
  block in `docker-compose.yml`.

### Docs

- Architecture diagram (`docs/architecture.svg`) refreshed for the
  SandboxAgent + 5-skill structure, current model defaults,
  Polymarket + Kalshi prediction-market column, and the
  Qdrant-disabled fundamentals stack. Corrected the Prediction
  Markets agent + MCP labels (previously duplicated from the
  Research column).
- READMEs (top-level + `src/obai` + `src/fundamentals-server`)
  refreshed for current model defaults and the Qdrant-disabled
  default.

### Infra

- Opik docker images pinned to `2.0.27` (`opik-backend`,
  `opik-python-backend`, `opik-frontend`) for reproducible local
  Opik bring-up.
- CI workflows gate `:latest` Docker tags and the GitHub-Release
  `prerelease` flag on the stable-tag regex
  `^v[0-9]+\.[0-9]+\.[0-9]+$`, so beta and rc tags publish only
  their version-pinned images and are marked pre-release.

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
  The legacy plain-Agent Hub path and `ENABLE_SANDBOX_HUB` toggle have
  been removed; the SandboxAgent Hub is now the only runtime path.
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

[Unreleased]: https://github.com/sixteen-dev/obai/compare/v1.5.4...HEAD
[1.4.0b1]: https://github.com/sixteen-dev/obai/releases/tag/v1.4.0b1
[0.9.0]: https://github.com/sixteen-dev/obai/releases/tag/v0.9.0
