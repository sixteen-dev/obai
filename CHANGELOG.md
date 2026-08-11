# Changelog

All notable changes to OBaI will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **User-settable hub model and reasoning effort**, persisted in
  `~/.obai/settings.json` (`hub_model`: `gpt-5.6-sol` | `gpt-5.6-terra`,
  `hub_reasoning_effort`: `medium` | `high` | `xhigh` | `max`). The web UI
  settings modal and the new `obai config set-model` / `obai config set-effort`
  commands write the same file, so the CLI and web clients — separate processes
  that each build their own hub — resolve one source of truth. Scope is the hub
  only; specialist models and effort tiers stay code-owned.
- Resolution order for those two settings is init kwargs > env >
  `~/.obai/settings.json` > shipped default. `ORCHESTRATOR_MODEL` and
  `ORCHESTRATOR_REASONING_EFFORT` are unchanged and still win — the eval A/B
  comparison and the E2E regression gate pin the hub model by injecting env —
  so a stale export makes the UI and CLI appear to do nothing. Both surfaces
  warn when the matching variable is set.
- Saving in the web UI applies immediately: `PATCH /api/settings` retunes the
  running hub in place (model, reasoning effort, and the compaction threshold,
  which is a fraction of the model's window) under the query lock, so the
  change lands on the next message without dropping the conversation or
  re-initializing MCP. Env-pinned fields are skipped so precedence holds. Other
  clients hold their own hub in their own process and pick the change up when
  they next launch. Save is also a no-op when neither dropdown moved.
- Saving mid-answer never blocks: the bridge queues the change instead of
  waiting on the query lock, the next query applies it, and `/api/settings`
  reports `pending_apply` so the UI says "applies once the current answer
  finishes" rather than falsely asking for a restart.
- No migration on upgrade: an absent or empty settings file means "use the
  shipped defaults", so existing installs behave exactly as before until
  someone changes a setting. A file that exists but does not parse or validate
  is reported as an error instead of silently falling back, so a typo cannot
  quietly move you to another price tier.
- `ORCHESTRATOR_REASONING_EFFORT` is now documented in the README, both
  `.env.example` files, and the `obai` skill. It worked before but appeared
  nowhere user-facing. The specialist and per-agent `*_REASONING_EFFORT`
  variables are documented alongside it.
- `obai web --reload` restarts the server on Python source changes. Static
  assets are deliberately not watched: they are read from disk per request, so
  a browser refresh already suffices and a restart would spend a full hub
  re-init to achieve nothing.

### Changed

- The accepted reasoning-effort set is now `none|low|medium|high|xhigh|max`.
  `minimal` has been removed: it is a valid value in the OpenAI SDK's own type
  but every `gpt-5.6` model rejects it at request time, so accepting it only
  traded a config-time error for a mid-query one.

  **Upgrading:** if you have `ORCHESTRATOR_REASONING_EFFORT=minimal` (or any
  other `*_REASONING_EFFORT=minimal`) exported or set in `~/.obai/.env`, unset
  it or change it to `low` before upgrading. The value was previously accepted
  at config time, so on the new version every `obai` command that builds a
  config will fail validation until it is changed. `obai config show` flags an
  env value that is not accepted.

### Fixed

- Native `<select>` dropdowns in the web UI settings modal rendered
  near-white text on the browser's white popup, unreadable except for the
  hovered row. The stylesheet declared no `color-scheme`, so the browser
  painted native controls in light mode while the options inherited the dark
  theme's text colour. Affected the portfolio preference dropdowns too.
- `setup.sh` aborted at step 6/8 with `mktemp: too few X's in template`. GNU
  mktemp requires three trailing `X`s, and under `set -euo pipefail` the
  failed substitution killed the run before the web UI and final
  configuration steps. BSD/macOS accepts the bare prefix, which is why it
  went unnoticed. Shell scripts now have test coverage for this.

## [1.6.0] - 2026-07-18

Minor: cost-aware evaluation suite with deterministic outcome contracts, plus a
regression gate that no longer false-fails correct answers.

### Added

- Cost-aware evaluation corpus: 210 cases across 8 categories (185 default + 25
  opt-in `extended_only`). New `--include-extended`, `--ids`, and `--limit`
  options allow surgical, cost-capped paid suite runs.
- New scorers: `OutcomeContractScorer` (deterministically classifies success,
  data-unavailable, specialist-error, partial-refusal, and hub-reject from trace
  evidence), `DatePolicyScorer` (freshness / as-of SLA disclosure), and
  `PartialRefusalSemanticScorer` (verifies scoped refusals are complete and free
  of fabricated results or side effects).
- Fail-fast preflight before any paid query: suite, selection, scorer,
  `ANTHROPIC_API_KEY`, and export/report-destination checks. Suite runs return
  defined exit codes (0 pass, 1 contract failure, 2 invalid config, 3 incomplete
  scoring).
- `forbidden_tools` support in tool-orchestration scoring.
- Hub context retention for long sessions, per OpenAI's ARC-AGI-3 finding that
  retained reasoning plus compaction tripled scores on that benchmark. The hub
  now sends `reasoning.context="all_turns"` and server-side
  `context_management` compaction. New `ORCHESTRATOR_COMPACT_RATIO` (default
  `0.9`, `None` disables) sets the threshold as a fraction of the hub model's
  context window rather than a fixed token count, so it tracks the model:
  942,818 tokens on `gpt-5.6-sol` (~1.05M window), 360,000 on `gpt-5.1` (400k).
  An unrecognized model logs a warning and leaves compaction off rather than
  guessing a threshold. Hub-only — specialists carry no `Session`.

### Changed

- **Every agent model moved onto the `gpt-5.6` price tier.** `SPECIALIST_MODEL`
  and the guardrail model `gpt-5-mini → gpt-5.6-luna`; strategy, crypto, and
  prediction markets `gpt-5.1 → gpt-5.6-terra`. The hub stays on
  `gpt-5.6-sol`. Both new IDs carry the same ~1.05M context window as the hub
  model, so the compaction threshold is unchanged where they are used as the
  hub. A new `test_every_default_model_is_gpt_5_6` pins the whole default set so
  a stale model name surfaces as a test failure rather than an invoice
  surprise.
- **Strategy, crypto, and prediction markets reasoning effort `high → medium`.**
  Medium is the balanced starting point OpenAI recommends for GPT-5.6; the
  per-agent env overrides (`STRATEGY_REASONING_EFFORT` and friends) remain the
  first knobs to turn back up if answer quality slips.
- `evaluation query`/`evaluate` no longer stamp traces with a hard-coded
  `gpt-4o` label when `--model` is omitted. `run_query_with_trace` takes
  `str | None` and falls back to the hub's configured model, so trace metadata
  reflects the model that actually ran.
- `openai-agents` `0.17.3 → 0.19.1` and `openai` `2.15.0 → 2.42.0` floors in the
  `obai` service. `Reasoning.context` landed in `openai` 2.42.0, so the older
  floor could not express retained reasoning under `mypy --strict`.
- Faithfulness scoring now labels financial figures (strike, spot, bid/ask,
  greeks, premium) and fails outright on a labeled-number contradiction the
  semantic judge can no longer override; numeric parsing handles signed,
  scientific-notation, and accounting-negative values.
- `EfficiencyScorer` measures redundancy by identical route+argument signature,
  so reusing a specialist with different arguments is no longer penalized.
- Opik dataset names carry a per-selection fingerprint, preventing stale rows in
  a reused base dataset from leaking into a filtered run.
- E2E regression gate: accept a synchronous walk-forward completion instead of
  aborting the suite, normalize typographic hyphens/dashes before text
  assertions, broaden refusal detection to inflected forms, and re-baseline
  specialist-call ceilings to observed multi-step counts (core planning budget
  157 -> 181).
- **Breaking:** the suite loader now raises on malformed YAML, unknown fields,
  duplicate IDs, or multi-turn rows (previously warn-and-skip). Suite exit codes
  were remapped (`2` = invalid config/selection, `3` = incomplete/errored
  scoring), and `evaluate --suite` no longer silently falls back to built-in
  cases when the YAML suite file is missing.

### Security

- `mcp` raised to 1.28.1 across every service lock (CVE-2026-52869 principal
  verification, CVE-2026-52870 task-handler isolation, CVE-2026-59950 WebSocket
  Host/Origin validation).
- `click` raised past PYSEC-2026-2132 in every per-service lock (`obai` to
  8.3.3, the ten service locks to 8.4.2). The 1.5.4 pin covered the root lock
  only, so each service still resolved a vulnerable `click`. `uv audit` now
  reports no known vulnerabilities for the root and every service except
  `obai`, which still carries `setuptools` 82.0.1 (GHSA-h35f-9h28-mq5c /
  PYSEC-2026-3447, fixed in 83.0.0).

### Fixed

- **Empty replies from `strategy_analysis` are fixed at the runtime layer.** The
  relay only surfaced output containing `#### 1. Verdict` or `Job ID` +
  `Estimated Time`; every other shape fell through unrelayed, and because the
  hub is instructed to emit nothing but the relayed output, the user got a blank
  response with no error. A completed walk-forward job-status poll returning
  2917 characters of fold results was reproduced as an empty reply. Also
  silently affected: Mode 3 diagnostics (supported indicators/operators,
  trade-log review), missing-input clarifications, engine errors, and refusals.
  Any non-empty response is now relayed verbatim, matching `crypto_analysis`
  and `prediction_market_analysis`.
- Strategy specialist now formats a **completed** async job-status poll as the
  full Completed Strategy Response (`#### 1. Verdict` contract). Previously a
  completed walk-forward follow-up emitted an ad-hoc summary that the new
  deterministic relay did not recognize, dropping it to an empty UI reply. The
  e2e gate's `CORE-WALKFORWARD` case now requires the verdict deliverable.
- Options chain snapshots no longer fail on a null `day` block. The provider
  sends `"day": null` for contracts with no daily bar, which raised
  `AttributeError` while surfacing daily volume and failed the whole chain
  request (and the single-contract path that reuses the same filter).
- Continuous experiment metrics (efficiency, answer-relevance, LLM-judge rubric
  average) are recorded correctly; the extractor previously read result keys that
  never matched and silently dropped these values.
- Scorers that do not apply to a row no longer record false-zero scores in Opik
  aggregates; N/A rows are omitted with an explicit applicability flag.

### Package versions

- Product line (root, `obai`, `crypto-server`): `1.5.5 → 1.6.0`.

## [1.5.5] - 2026-07-14

Patch: keep the install-manifest's `managed` flag stable across `setup.sh`
re-runs.

### Fixed

- `obai start` / `obai upgrade` re-run `setup.sh` without `OBAI_MANAGED`, which
  previously rewrote `~/.obai/install-manifest.json` to `managed: false` —
  silently downgrading a managed (one-liner) install to `source` after the first
  start/upgrade. `setup.sh` now preserves an existing `managed: true` unless
  `OBAI_MANAGED` is explicitly set, so managed installs stay managed. (Upgrade
  behavior was unaffected either way; this corrects the mode label, messaging,
  and force-push recovery guidance.)

### Package versions

- Product line (root, `obai`, `crypto-server`): `1.5.4 → 1.5.5`.

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

[Unreleased]: https://github.com/sixteen-dev/obai/compare/v1.5.5...HEAD
[1.4.0b1]: https://github.com/sixteen-dev/obai/releases/tag/v1.4.0b1
[0.9.0]: https://github.com/sixteen-dev/obai/releases/tag/v0.9.0
