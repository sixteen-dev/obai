# Audit Closure Status

Branch: `worktree-audit-fixes` (in `.claude/worktrees/audit-fixes`).

Three commits, scoped to high-impact items. Linter/typecheck/tests all pass per service.

## Fixed

| ID | Pri | Where | Change |
| --- | --- | --- | --- |
| OBAI-AUDIT-001 | P0 | `src/obai/clients/web/static/app.js` | Sanitize parsed markdown DOM — allowlist tags, strip `on*`, neutralize unsafe URL schemes. |
| OBAI-AUDIT-004 | P0 | `src/obai/core_agents/mcp/tool_converter.py` | Cache only when `readOnlyHint AND idempotentHint` (was OR) — real-time tools opt out via `idempotentHint=False`. |
| OBAI-AUDIT-005 | P0 | `skills/autotrader/lib/risk.py` | Sell orders past existing long apply position-size and exposure checks; pure reductions skip. New `_sized_order` helper. |
| OBAI-AUDIT-006 | P0 | `src/backtest-server/src/engine/backtester.py` + `server.py` | `BacktestConfig.initial_capital` threaded from `ExecutionConfig`; seeds equity in single-symbol and independent paths. |
| OBAI-AUDIT-008 | P0 | `src/backtest-server/src/engine/metrics.py` | `_compute_bench_metrics` aligns strategy/benchmark returns by date keys (new `_align_returns_by_date`). |
| OBAI-AUDIT-009 | P0 | `src/portfolio-server/src/tools/parse.py` | Dollar regex captures `k`/`m`/`b` suffix; ticker regex accepts `BRK.B` (also closes -072). |
| OBAI-AUDIT-010 | P0 | `skills/autotrader/lib/risk.py` | `qty <= 0` and non-finite values rejected before risk math. |
| OBAI-AUDIT-011 | P1 | `src/obai/core_agents/mcp/client.py` | `timeout` enforced via `asyncio.wait_for`; `max_retries` honored with linear backoff. |
| OBAI-AUDIT-013 | P1 | `src/obai/core_agents/guardrails.py` + `config.py` | Guardrail model sourced from `AgentConfig.guardrail_model` (default `gpt-5-mini`). |
| OBAI-AUDIT-018 | P1 | `src/obai/Dockerfile` | Package path `agents/ → core_agents/`; entrypoint changed to `obai` script. |
| OBAI-AUDIT-022 | P1 | `src/backtest-server/src/engine/indicators.py` | Dual-input indicators (`BETA`, `CORREL`) consult registry `second_source` default; precedence: user param > registry > `config.source`. |
| OBAI-AUDIT-023 | P1 | `src/backtest-server/src/engine/indicators.py` | BBANDS `std_dev` fanned out to both `nbdevup` and `nbdevdn` via `_build_talib_kwargs`. |
| OBAI-AUDIT-024 | P1 | `src/backtest-server/src/server.py` | Benchmark in universe reused via new `_resolve_benchmark_df` helper. |
| OBAI-AUDIT-029 | P1 | `src/market-data-server/src/{utils.py,clients/fmp_client.py}` | Added local `retry_async` + `is_retryable_httpx_exc` (mirrors fundamentals server); 429/5xx/network retried. |
| OBAI-AUDIT-030 | P1 | `src/options-server/src/engine/pricing.py` + `scenarios.py` | `option_type` and `direction` validated; invalid values raise `ValueError` instead of silently flipping payoff/side. |
| OBAI-AUDIT-038 | P1 | `src/portfolio-server/src/engine/risk.py` | When all quotes fail, raise instead of silently falling back to equal weights. |
| OBAI-AUDIT-039 | P1 | `src/portfolio-server/src/server.py` | Removed 100-holding ETF truncation in look-through math; display caps still apply per tool. |
| OBAI-AUDIT-042 | P1 | `src/research-server/src/clients/exa_client.py` + all 5 tools | `ExaClient` is now an async context manager; tools use `async with` so HTTP client closes on every path. |
| OBAI-AUDIT-045 | P1 | `src/events-news-server/src/server.py` | News-search failures labeled `Tavily` (was `FMP`). |
| OBAI-AUDIT-066 | P2 | `src/prediction-markets-server/src/tools/wallets.py` | EVM address format validated at tool boundary. |
| OBAI-AUDIT-072 | P2 | (parser change above) | Ticker grammar now accepts `BRK.B` etc. |

## Pushed back — not applicable under current architecture

| ID | Reason |
| --- | --- |
| OBAI-AUDIT-002 | Hub module globals are scoped to a single in-flight query by design. Web bridge already serializes through `asyncio.Lock` (see AUDIT-003 fix below); CLI is single-shot. Removing module globals is a major refactor for a hypothetical concurrency model OBaI does not deploy. Revisit if/when multi-process or true concurrent execution is introduced. |
| OBAI-AUDIT-003 | The web bridge lock exists for the reason in -002. Replacing it requires -002 first. Out of scope without a concrete multi-user concurrency requirement. |
| OBAI-AUDIT-007 | "Independent" multi-symbol mode averages per-symbol equity curves because that's what `allocation_mode="independent"` means. Users who want shared capital pick `allocation_mode="portfolio"`, which already exists and is wired through. Renaming/relabelling is UX work, not correctness. |

## Remaining — not yet addressed (still in audit)

These were either time-bounded out of the first pass or judged lower marginal value vs the items above. Recommended ordering follows the audit's "Highest-Value Test Backlog" section.

- P1: 012, 014, 015, 016, 017, 019, 020, 021, 025-028, 031-037, 040-041, 043-044, 046-050
- P2: 051-080 (most are reliability/edge-case work)
- P3: 082

## Verification

Per-service: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest`.

- `skills/autotrader`: 32 risk tests pass (includes 7 new sell-side + qty cases).
- `src/backtest-server`: 267 tests pass.
- `src/portfolio-server`: 46 tests pass (includes 2 new `$50k`/`$1.2M`/`BRK.B` cases).
- `src/options-server`: 64 tests pass.
- `src/research-server`: 9 tests pass after test fixture update for `async with` shape.
- `src/market-data-server`, `src/prediction-markets-server`: pre-commit hook ran them clean.
