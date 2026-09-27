# ADR 0003 — OBaI integration, routing and honest-refusal slice

Status: accepted, 2026-09-26 (owner decisions of 2026-09-26 are binding and recorded in §0; §9's default applies). Scope: the part of design §15.4, §15.5, §16.1 and §17.3 that can be built before qualified historical data exists — a local single-user MCP server for `src/options-backtest-server`, an Options Strategy terminal specialist in `src/obai`, hub routing, offline tests and new paid-gate cases. Builds on ADR 0001 and ADR 0002 as amended; WP1–WP3 code changes only where named in §1.1 and §5. "design §n" is `docs/design/options-backtesting-system-v3.md`.

## Decision

The server exposes exactly two of the thirteen §15.1 tools, `options_backtest_capabilities_tool` and `options_backtest_validate_strategy_tool`, both read-only, and advertises the other eleven as unavailable with a `missing_capability` and the work package that supplies them. Historical backtesting is reported as unavailable through one constant §15.2 `Issue` (`DATA_ENTITLEMENT_MISSING`, `missing_capability="historical_options_data"`), reachable from `capabilities` without a strategy and from `validate_strategy` with one. No synthetic-data run is reachable over MCP: `server.py` never imports `synthetic` or `engine.simulator`, and a test pins that allowlist. The specialist `options_strategy_analysis(user_request, underlyings, context, prior_run_ids, requested_action)` compiles the user's mechanics into a strategy document, validates it against the server, re-validates at most twice when an issue names an unambiguous fix, and returns the §15.5 short contract: status, reference, supported next action, precise explanation. It never produces a performance number, and the hub relays every non-empty response verbatim, including errors and the tool wrapper's own failures.

Implementation is phased. Phase A creates new files and edits only the wholly clean `src/options-backtest-server/` tree; Phase B edits existing `src/obai`, gate and deployment files, dirty ones only once `git diff --stat HEAD -- <path>` is empty. Implementation stops at offline lint, unit tests and `run_suite.py --dry-run`; the paid gate runs only on the owner's explicit go-ahead.

## 0. Owner decisions and design amendments

Owner decisions (2026-09-26): (1) scope is routing plus honest refusal, no synthetic backtest exposed through the hub; (2) phased implementation around another session's dirty files, coordinated with that session (obai-ec), which on 2026-09-26 agreed that this work builds on top of its uncommitted hunks, never reverts, reformats or rewrites them, and keeps them out of this work's commits (hunk-level staging); it will likewise stage only its own hunks; (3) no paid E2E run without explicit go-ahead. Binding lessons: the hub stays generic (no specialist format text in `central_hub_agent.py` constants); prompt edits carry no inline examples or numeric financial defaults, and prompt tests read the `.md` file, never `load_prompt`; specialist output beats hub memory; gate cases assert structural facts only.

This ADR amends the design in three places, each an engineering reading the design's own rules force:

1. **§15.1 "strict structured objects, not strategy JSON inside unvalidated text".** The strategy is passed as `strategy_json: str` and fed to `load_strategy(bytes)`. A typed `StrategySpec` parameter fails over FastMCP on the vendored example itself (nine `tuple_type`/`is_instance_of Decimal` errors: FastMCP validates the already-decoded argument tree), and the MCP envelope decode silently drops duplicate keys, non-finite literals, exact decimal text and the 64 KiB limit that design §9.1 and ADR 0001 §4 require rejecting. The text is not unvalidated: `load_strategy` is the only path that applies all seven ingestion stages. The *result* is the strict structured object. Repo precedent: `backtest_run_strategy_tool(strategy_json: str)`, `options_...(contracts_json: str)`.
2. **§17.4 / §20 step 4 "WP2–WP5 before wiring a public specialist route".** A routing slice (call it WP6a) is pulled ahead by owner decision. The WP6 exit gate is preserved in substance: no R1 backtest capability is enabled, because no tool that consumes data or produces a run exists on the server. WP6 proper (report, run references, R1 qualification) still follows WP2–WP5.
3. **§17.4 WP0 "public capability is still disabled".** Exposing `capabilities` and `validate_strategy` is not public capability. Neither returns a performance figure or touches market data; together they are exactly the "advertise unavailable capability, not accept a job with ignored mechanics" that §15.1 requires.

## 1. Server (`src/options-backtest-server`)

### 1.1 Modules and import rules

```
src/options_backtest/config.py           Settings (pydantic-settings), DEPLOYMENT_MODE
src/options_backtest/logging_config.py   configure_logging(level), get_logger(name)
src/options_backtest/server.py           mcp, two tools, /health, /health/ready, main()
src/options_backtest/models/run.py       `_root_issues` becomes public `root_issues` (rename only)
```

`server.py` imports only `errors`, `ingest`, `models.run`, `models.strategy`, `models.strategy_checks`, `reference.products`, `config` and `logging_config` from the package. `tests/unit/test_import_graph.py` gains a `SERVER_ALLOWED` allowlist test in the style of `KERNEL_ALLOWED`; the existing `test_no_src_module_imports_synthetic` and acyclicity tests already apply, and `test_every_module_imports` will import `server.py`, so module import has no side effects: `FastMCP(...)` is constructed at import (sibling pattern), `configure_logging` and `Settings()` run in `main()` only. `filterwarnings = ["error"]` therefore requires that importing the locked fastmcp and registering two tools raise no warning (probed clean on 3.2.4; re-verified at lock time, §5).

`root_issues(spec) -> list[Issue]` is the one WP3 change: `resolve()` keeps calling it; `validate_strategy` needs the root-to-underlying check without a `manifest_id`, and the private name was the only barrier.

### 1.2 Configuration (`config.py`)

`Settings(BaseSettings)` with `SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")` and no `env_prefix`, like every server in the fleet, so the variables are `TRANSPORT`, `HOST`, `PORT`, `LOG_LEVEL`. Fields: `transport: Literal["stdio", "http", "sse", "streamable-http"] = "streamable-http"`, `host: str = "127.0.0.1"`, `port: int = 8012` (design §1; 8011 stays reserved), `log_level: str = "INFO"` (validated against the logging level names, fail loud). `DEPLOYMENT_MODE: Final = "local_single_user"` is a module constant, not a setting: under the Individual license it is the only permitted deployment (design §16.1, ADR 0001 §9), so nothing may configure it away. `Settings()` is constructed in `main()` and passed explicitly; there is no module-global singleton and no `get_settings()` (the health routes are stateless and need none).

The host default is loopback because a bare `python -m options_backtest.server` must bind localhost (design §16.1). The container overrides `HOST=0.0.0.0` so compose's port mapping works, and compose binds the *host* side to loopback (§1.10).

### 1.3 Logging (`logging_config.py`)

`configure_logging(log_level: str) -> None` is `backtest-server/src/logging_config.py:10-44` without the httpx/httpcore silencing (this server makes no outbound HTTP call): stdlib `basicConfig` to stdout, structlog processors ending in `JSONRenderer`, `cache_logger_on_first_use=True`. `get_logger(name)` returns `structlog.get_logger(name)`. Tool handlers log one event per call with the tool name, the outcome (`valid`, `rejected`, `error`) and the issue count, never the strategy text.

### 1.4 Transport and health

`mcp = FastMCP("options-backtest-server", version=ENGINE_VERSION)`; `main()` runs `mcp.run_async(transport, host, port, path="/mcp", stateless_http=True)`. No CORS middleware: the hub is `fastmcp.Client`, external skills are MCP clients, no browser talks to this server, and a permissive origin policy on a loopback service has a cost and no user. It is added with the first browser client.

`ENGINE_VERSION` (`models/run.py`) is the server version; `test_run_models.py` already pins it to `VERSION`, so there is no second version reader.

| Route | Status | Body |
|---|---|---|
| `GET /health` | 200 | `status: healthy`, `server`, `version`, `deployment_mode` |
| `GET /health/ready` | 200 | `status: ready`, `server`, `version`, `deployment_mode: local_single_user`, `readiness: {api: ready, control_plane: unavailable, data: unavailable}`, `hosted: {available: false, reason: individual_license}`, `multi_tenant: {available: false, reason: individual_license}`, `historical_data: {available: false, reason: "no qualified historical data (WP2 not built)"}` |

Readiness is 200 because the API is fully able to serve the two tools it has; the three-way `readiness` map is design §16.1's "distinguish API liveness, control-plane readiness and data capability readiness", and the license lines are its "readiness reports hosted and multi-user capability as unavailable". The compose healthcheck and `obai status` (any status below 500 is ok) both pass.

### 1.5 Tools

Both tools carry `annotations={"title", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}`. Both are correctly idempotent, so the hub's converter may cache them (readOnly and idempotent, 60 s TTL, never an `isError` result). Both return dicts; errors are returned, never raised, so the typed shape survives `tool_converter.py:360-368`.

**`options_backtest_capabilities_tool(product_family: str | None = None) -> dict`.** Static facts only (§1.6). The optional family filters nothing (R1 has one family); the payload carries `requested_product_family` and `requested_product_family_supported: bool` so an unknown family is answered truthfully rather than with an error.

**`options_backtest_validate_strategy_tool(strategy_json: str, start_date: str | None = None, end_date: str | None = None) -> dict`.**

1. `raw = strategy_json.encode("utf-8", "surrogatepass")`; a lone surrogate then fails `strict_json`'s strict UTF-8 decode and is reported as `MALFORMED_JSON` at `""` by the existing stage 1, so no new code path handles it.
2. `load_strategy(raw)`; on `SpecRejected` the issues are collected. On success: `resolve_policies(spec)` (unknown policy ids ⇒ `INVALID_STRATEGY_RULE` at `/fee_schedule_id`, `/funding_policy_id`) and `root_issues(spec)` (`UNSUPPORTED_PRODUCT` at `/product/allowed_option_roots/{i}`), both collected.
3. Window syntax, independent of the strategy: each given date must fullmatch `\d{4}-\d{2}-\d{2}` and parse with `date.fromisoformat` (the regex first, because 3.11+ `fromisoformat` accepts other shapes), else `SCHEMA_VIOLATION` at `/start_date` or `/end_date`; `end_date < start_date` ⇒ `SCHEMA_VIOLATION` at `/end_date`. Session membership needs a manifest's session table and is reported as `unchecked_no_manifest`. Pointers `/start_date` and `/end_date` address the tool's argument object; they cannot collide with strategy pointers because the schema has no such top-level keys, so one `issues` list serves both, sorted by `sorted_issues`.
4. Any issue ⇒ `{"isError": True, "valid": False, "error": "strategy rejected: <n> issue(s)", "issues": [<Issue as dict, code as its string>]}`. `isError: True` is the backtest-server precedent for an invalid strategy and keeps the hub converter from caching a rejection.
5. Otherwise:

```text
valid: true
strategy: {name, structure, product: {underlying_symbol, allowed_option_roots, family},
           legs: [{leg_id, side, option_type, ratio}], leg_order, premium_direction}
policies: {fee_schedule: {schedule_id, trade_per_contract, exercise_assignment_per_contract,
                          cash_settlement_per_contract}, funding_policy_id}
window: {start_date, end_date, session_membership: "unchecked_no_manifest"} | null
assumptions: [fee schedule as assumed_schedule; zero-interest, no-borrow funding; fully_funded_v1
              account; natural_package_limit_v1 fills at later observations; European PM cash
              settlement on the official close; product rules cboe_template_unverified_v1]
data_requirements: {historical_options_data: {required: true, available: false, issue: <§1.7>}}
backtest_available: false
versions: {engine: ENGINE_VERSION, product_rules: PRODUCT_RULES_VERSION,
           strategy_schema: {id: "urn:obai:options:strategy:1", version: 1, sha256: "385ee006…7b5e"}}
```

No "normalized spec" is returned: `StrategySpec.model_dump` is documented as a non-canonical view (`models/strategy.py:16-18`), and the canonical resolved spec and digest are WP5's. The `strategy` block is a summary the specialist can quote, not a document `load_strategy` accepts back.

### 1.6 Capabilities payload

```text
schema_version: 1
deployment_mode: local_single_user
versions: {engine, product_rules, strategy_schema: {id, version, sha256}}
supported:
  product_families: [{family: us_european_pm_cash_index, roots: [{root: SPXW, underlying: SPX},
                      {root: XSP, underlying: XSP}], status: template_unverified}]
  structures: [single_long, vertical, iron_condor, long_straddle, long_strangle]   # typing.get_args(Structure)
  clock_profiles: [scheduled_daily_v1]
  account_policies: [fully_funded_v1]
  execution_models: [natural_package_limit_v1]
  fee_schedules: [illustrative_flat_1usd_per_contract_side_v1]
  funding_policies: [illustrative_zero_interest_no_borrow_v1]
  entry_dte_min: 7
rejected_in_r1: [{request: covered_call | cash_secured_put | wheel | naked_short | calendar_or_diagonal
                  | american_equity_or_etf_options | intraday_or_0dte, code: UNSUPPORTED_STRUCTURE
                  | UNSUPPORTED_PRODUCT, work_package: WP7 | WP8},
                 {request: positive min_open_interest, code: DATA_ENTITLEMENT_MISSING,
                  missing_capability: historical_open_interest}]
historical_backtest: {available: false, issue: <§1.7>}
data: {provider_manifests: [], fidelity: unavailable}
tools: {<all 13 §15.1 names>: {available: bool, missing_capability, work_package, reason}}
deployment: {hosted: {available: false, reason: individual_license},
             multi_tenant: {available: false, reason: individual_license}}
requested_product_family, requested_product_family_supported
```

Every list is read from the code that enforces it (`Structure` literal, `R1_OPTION_ROOTS`, `product_rules`, `FEE_SCHEDULES`, `FUNDING_POLICIES`, `PRODUCT_RULES_VERSION`, `ENGINE_VERSION`), so the payload cannot drift from the validator. The schema sha256 is a constant in `server.py`; a test asserts it equals the `strategy.schema.json` line of `tests/contracts/SHA256SUMS`. The schema body is not embedded: it lives under `tests/`, is not in the wheel, and the specialist needs its identity, not its text.

`tools` availability: `capabilities`, `validate_strategy` available. `estimate`, `prepare_data`, `submit` ⇒ `historical_options_data` (WP2, WP5). `job`, `cancel`, `result`, `events`, `compare` ⇒ `control_plane_jobs` (WP5). `create_experiment`, `freeze_candidate`, `research_validation` ⇒ `research_registry` (WP4, WP5).

### 1.7 The typed unavailable reason

One constant, used by `capabilities.historical_backtest.issue` and `validate_strategy.data_requirements.historical_options_data.issue`:

```python
HISTORICAL_DATA_UNAVAILABLE: Final = Issue(
    ErrorCode.DATA_ENTITLEMENT_MISSING,
    "no qualified historical options data: the historical data work package (WP2) is not built",
    "",
    retriable=False,
    missing_capability="historical_options_data",
    remediation="strategy validation is available now; a historical backtest needs the WP2 data "
    "provider and the WP5 job service",
)
```

`DATA_ENTITLEMENT_MISSING` is the right code: the data is not procured or qualified (ADR 0001 §9 owner items), which is an entitlement gap, not a gap inside data that exists (`DATA_COVERAGE_GAP`). It is the house precedent for advertising a missing capability (`strategy_checks.py:565-576`). No `ErrorCode` is added; `test_errors.py` keeps pinning the set.

### 1.8 The eleven unregistered tools

They are not registered, not stubbed. A stub is callable in `tools/list`, so the specialist would be offered a `submit` that can only fail, and a generic unavailable error needs a code the pinned set lacks. Absence plus the `tools` map is the honest advertisement §15.1 asks for. When WP2/WP5 add a tool it is registered with its real behaviour and its `tools` entry flips.

### 1.9 Dependencies

Runtime: `fastmcp>=3.2,<4` (siblings lock 3.2.0, 3.2.4, 3.3.1; tests call decorated tool functions directly, which is 3.x behaviour; the FastMCP 4 migration is only proposed), `pydantic-settings>=2.14.2`, `structlog>=25.1.0`; `pydantic>=2.12.5,<3` unchanged. Dev: `pytest-asyncio>=1.3.0` for the in-process client tests. `[tool.uv]` copies backtest-server's `exclude-newer-package` carve-outs and `constraint-dependencies` (`pyproject.toml:73-76`): with the 7-day age gate and no carve-outs the security floors on `mcp`, `starlette`, `pyjwt`, `cryptography`, `python-multipart`, `authlib`, `urllib3`, `idna` can be unsatisfiable (`CLAUDE.md` pitfall). `uv lock` runs only inside the service directory; exact versions land in `uv.lock`, the reproducibility record (ADR 0001 §8). No numpy, polars, duckdb, httpx.

### 1.10 Dockerfile, compose, CI, enumerations

**Dockerfile** (new; multi-stage like crypto-server, lockfile-pinned unlike every sibling, per ADR 0001 §8 and design §17.1): builder copies `pyproject.toml`, `uv.lock`, `VERSION`, runs `uv sync --frozen --no-dev --no-install-project`; copies `src/`, runs `uv sync --frozen --no-dev` (installs the project into the cached venv; the layout `src/options_backtest` is why the sibling "copy manifests, sync, copy src" order cannot install the project first). Runtime stage copies `/app/.venv`, `/app/src`, `/app/pyproject.toml`, `/app/VERSION`; `ENV PYTHONUNBUFFERED=1`; `EXPOSE 8012`; `CMD ["/app/.venv/bin/python", "-m", "options_backtest.server"]`. **`.dockerignore`** (new): `.venv`, `.coverage`, `.hypothesis`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `__pycache__`, `tests/`, `docs/`. The build is unverified until Phase A's step 6 runs it.

**docker-compose.yml**: service `options-backtest-server`, `image: ghcr.io/sixteen-dev/obai/options-backtest-server:${OBAI_VERSION:-latest}`, `build.context ./src/options-backtest-server`, `container_name obai-options-backtest-server`, `ports: ["127.0.0.1:8012:8012"]` (host side loopback: design §16.1 local single-user; the host CLI and the paid gate run on the host, and a hub container would use the compose network name), environment `TRANSPORT=streamable-http`, `HOST=0.0.0.0`, `PORT=8012`, `LOG_LEVEL=INFO`, no volumes, no secrets, `mem_limit: 512m`, `obai-mcp-network`, `restart: unless-stopped`, healthcheck on `/health/ready` with the sibling timings. Worker, control DB and licensed volumes from design §17.3 arrive with WP5.

**`.github/workflows/docker-publish.yml`**: add `options-backtest-server` to both lists (`:34`, `:38`). **`setup.sh`**: add `options-backtest:8012` to the health list (`:455-465`); the list's pre-existing omission of `crypto:8010` is noted, not fixed. **`scripts/run-all-tests.sh`** already includes the service. **`skills/obai-hub/mcp-config.json`** and `test_mcp_skill_contracts.py` are untouched: no external skill exposes this server in this slice (§7).

## 2. Specialist (`src/obai`)

### 2.1 Module — `core_agents/options_strategy_agent.py`

`OptionsStrategyAgent(BaseAgent)`: `agent_type = "options_strategy"`, `mcp_url_property = "mcp_options_backtest_url"`, so the SDK name is `obai_options_strategy_agent` and the prompt is `prompts/options_strategy.md`. `_get_model` returns `config.options_strategy_model` when set, else `config.get_agent_model("strategy")` (and the reasoning effort likewise), so the specialist is strategy-class by construction and carries no model literal: the committed defaults (`gpt-5.6-terra`) and obai-ec's uncommitted migration (`gpt-6-sol`) both hold without this work touching either (amended 2026-09-26). The strategy agent's own fallback to `orchestrator_model` is not copied. `handoff_description`: historical options-strategy validation and backtesting for US European PM cash-settled index options (SPXW, XSP; verticals, condors, straddles, strangles, single long options), managed options rules including covered call, cash-secured put, wheel and roll requests, which it answers with the supported scope; not for current chains, Greeks, IV, NBBO or current-contract scenario math (`options_analysis`), and not for equity or ETF share strategies (`strategy_analysis`).

### 2.2 Config fields (`config.py`, Phase B)

| Field | Default | Env | Notes |
|---|---|---|---|
| `mcp_options_backtest_url: str` | `http://localhost:8012/mcp` | `MCP_OPTIONS_BACKTEST_URL` | after `mcp_crypto_url` |
| `options_strategy_model: str \| None` | `None` | `OPTIONS_STRATEGY_MODEL` | `None` ⇒ the strategy agent's resolved model (`get_agent_model("strategy")`) |
| `options_strategy_reasoning_effort: ReasoningEffort \| None` | `None` | `OPTIONS_STRATEGY_REASONING_EFFORT` | `None` ⇒ `get_agent_reasoning_effort("strategy")` |
| `options_strategy_max_turns: int` | `12`, `ge=5, le=100` | `OPTIONS_STRATEGY_MAX_TURNS` | capabilities + validate + two re-validations + answer, with headroom |
| `enable_options_strategy: bool` | `True` | `ENABLE_OPTIONS_STRATEGY` | design §17.3 enable flag |

Precedence is the existing chain (init kwargs > env > dotenv > `_HubSettingsSource` > secrets > defaults); `_HubSettingsSource` supplies only the two hub fields and is untouched, per "hub settings are user-owned; specialists are code-owned". `test_config.py`'s `defaults` dict gains `"options_strategy": config.get_agent_model("options_strategy")`; the effort, URL and max-turns tests gain one assertion each.

### 2.3 Prompt — `prompts/options_strategy.md`

Sections, no inline examples, no numeric financial defaults (the user's or the schema's constraints are the only numbers the agent may write into a document):

- Date header (`$TODAY_DATE`) and role: OBaI's options strategy specialist for the options backtest service; a terminal author whose text reaches the user unchanged.
- Scope, read from `options_backtest_capabilities_tool` on every turn that builds or judges a strategy, never from memory: what is supported, what is rejected in R1, and that historical backtesting is unavailable with the issue the server returns. The agent states the server's `missing_capability` and code when it reports unavailability and never predicts when the capability arrives.
- Requested actions. `build`: compile the user's mechanics into one strategy document, validate, report the validation result and that no run can be made. `backtest`: as `build`, then the unavailability with its typed reason; never a substitute analysis, never a figure. `compare`, `status`: there are no runs or jobs in this deployment; say so with the typed reason, name what `prior_run_ids` would need. `explain`: answer from capabilities and, when the user supplied a document or rules, from validation; a capability question is answered from the payload.
- Fidelity to the user's mechanics: the document carries the user's structure, roots, legs, exits and account as stated; an unsupported mechanic is validated as stated so the server's rejection names it. A supported alternative may be offered as a distinct proposal, never applied silently (design §1: an unsupported wheel is not a cash-secured-put backtest).
- Bounded re-validation: after a rejection, re-validate at most twice, and only when an issue's `remediation` or message fixes one field without changing the user's mechanics; otherwise report the issues. The hard bound is `options_strategy_max_turns` in code.
- Output contract, the §15.5 short form, in this order: **Status** (`validated`, `rejected`, `unavailable`, `capability`), **Reference** (schema id and version, engine version, product-rules version; no run or job id exists), **Supported next action**, **Explanation** (every issue with its code, JSON pointer, message and remediation; the assumptions the server listed; the unavailability issue when a run was requested). Never a seven-section report, never a performance, drawdown, win-rate or return figure, never a number the server did not return.
- Never: invent a tool the server does not list; call a tool that is absent; convert an unavailable result into advice about the strategy's merit; drop an issue.

Because `load_prompt` reads Opik first, a prompt edit needs an app restart to deploy, and the prompt-content tests read the file directly.

### 2.4 Hub skill — `hub_skills/obai-options-strategy-routing/SKILL.md` (Phase B)

Frontmatter `name: obai-options-strategy-routing`; `description`: use when the user wants historical performance of an options strategy, validation of an options strategy document or rules, design of managed options rules (verticals, condors, straddles, strangles, covered calls, cash-secured puts, wheels, rolls), or asks what options-strategy backtesting OBaI supports; excludes current options chains, Greeks, IV and scenario math on live contracts, and equity or ETF share strategy backtests. The body documents the boundaries of §3, the handoff arguments, and the relay and unavailable rules, in the shape of `obai-strategy-routing`. The body never reaches the model (`_build_hub_agent` docstring), so every rule the hub must obey is also in `central_hub_base.md` (§3), and the skill is the routing signal the gate asserts with `expected_skills`. It is a Phase B file because `test_central_hub_builders.py` pins the skill count and is dirty.

### 2.5 Hub tool — `_build_options_strategy_tool` (`central_hub_agent.py`, Phase B)

```python
@function_tool(name_override="options_strategy_analysis",
               description_override=_OPTIONS_STRATEGY_TOOL_DESCRIPTION,
               strict_mode=True,
               failure_error_function=_terminal_options_strategy_failure)
async def options_strategy_analysis(ctx, user_request: str, underlyings: list[str], context: str,
                                    prior_run_ids: list[str],
                                    requested_action: Literal["build", "backtest", "compare",
                                                              "explain", "status"]) -> str
```

The description is generic and routing-only, in the shape of `_STRATEGY_TOOL_DESCRIPTION` and `_CRYPTO_TOOL_DESCRIPTION`: what the route is for, the mandatory `load_skill('obai-options-strategy-routing')` pre-condition in the same turn, `user_request` verbatim, `underlyings` as resolved symbols (may be empty for explain, status and capability questions), `context` as hub-resolved dated facts only, `prior_run_ids` as identifiers the user named, and that the tool is a terminal author whose output is relayed. No format or contract text; that lives in the skill and the prompt.

Pre-flight, hard syntactic facts only (`src/obai/CLAUDE.md` pitfall): `_get_options_strategy_handoff_error(user_request, self._current_user_query)` returns `OPTIONS_STRATEGY_HANDOFF_ERROR: …` when the normalized original query is not a substring of the normalized `user_request` (the substring half of `_get_strategy_handoff_fidelity_error`, reusing `_normalize_strategy_handoff_text`; the strategy DSL's threshold-versus-crossover heuristic and its `strategy_analysis` wording are not reused). The error is returned unwrapped so the hub retries with the verbatim request; the base prompt lists the token with the other control signals. There is no missing-inputs gate: what is missing is the specialist's domain judgment, and `requested_action` is enforced by the strict schema (an invalid value is an SDK argument rejection, which the judge reports as `malformed_specialist_invocation`).

Then `_render_options_strategy_handoff` produces the blocks `User request:`, `Requested action:`, `Underlyings:` (omitted when empty), `Prior run IDs:` (omitted when empty), `Context:` (omitted when blank); `Runner.run_streamed(agent, input=handoff, context=ctx.context, max_turns=self.config.options_strategy_max_turns)` streams through `_create_stream_handler("options_strategy_analysis", "Options Strategy Agent")`. An empty final output returns `""` and leaves no state, like the other terminal wrappers. A non-empty output is recorded in the invocation state (§2.6) and returned as `"__TERMINAL_TOOL_OUTPUT__:options_strategy_analysis:render=verbatim_relay\n\n" + output`.

**Wrapper failures are terminal.** Today an exception inside a terminal wrapper (`MaxTurnsExceeded`, a model error) reaches the SDK's `default_tool_error_function`, whose unmarked "An error occurred…" string the hub is free to paraphrase or replace with its own memo — exactly what design §15.4 forbids. `failure_error_function=_terminal_options_strategy_failure` is the SDK hook for this (`agents/tool.py`, `ToolErrorFunction`): it logs the exception with `exc_info`, records a short-form error (`Status: failed` with the exception class and the supported next action) in the invocation state, and returns it marker-wrapped. The SDK still attaches the error to the tool span, so the gate sees a real failure as a span error. Nothing is swallowed: logged, recorded, returned typed.

### 2.6 Invocation-scoped terminal state and `run()`

```python
@dataclass
class OptionsStrategyState:
    content: str | None = None


_options_strategy_state: ContextVar[OptionsStrategyState | None] = ContextVar(
    "options_strategy_state", default=None
)
```

The invocation is one `CentralHubAgent.run()` call. `run()` sets a fresh holder at its start and clears it in a `finally` around the whole body (`_clear_options_strategy_passthrough()` sets a fresh empty holder; it does not `Token.reset`, because an async generator abandoned by its consumer is finalized by the event loop in another context, where `reset` raises `ValueError`). The tool task runs in a copied context and sees the same mutable holder, so its write is visible to the stream loop (the crypto mechanism, `test_crypto_agent.py:287-294`). Two concurrent `run()` calls in two tasks hold different objects by construction; a cancelled `run()` leaves no content behind. Typed run references are not added to the holder: this slice has no runs, and the field arrives with the first tool that returns a `run_id` (WP5/WP6). The existing module-global strategy and prediction holders are not migrated here (§7).

`run()` detection order becomes prediction, crypto, strategy, options strategy; after the terminal fires, hub text is buffered as today, and the tail emits `OptionsStrategyPassthroughEvent(content)` (new frozen dataclass beside the other three) and caches the passthrough as the response.

### 2.7 Server down or specialist disabled

`_init_specialists_parallel` constructs `OptionsStrategyAgent()` only when `config.enable_options_strategy` is true (disabled: an info log, no agent, no tool, not degraded). Enabled, it joins the `optional` list; a failed `initialize()` sets the field to `None`, appends `"options_strategy"` to `degraded_capabilities` and logs a warning; `_build_hub_agent` receives no `options_strategy_analysis` tool; `_cleanup_agents` nulls the field; `get_specialist` gains `"options_strategy"`. Mid-run server failures surface to the specialist as `isError` JSON (`tool_converter.py:360-368`) and become a terminal `Status: unavailable` answer.

When the tool is absent the hub currently has no rule and could route a covered-call request to `options_analysis` or `strategy_analysis` instead. One generic rule is added to `central_hub_base.md`: when a route named in the routing invariants is not among the available tools, say that capability's server is unavailable and do not substitute another specialist or training data. It covers crypto and prediction markets too.

### 2.8 Display metadata

`clients/shared.py` `SPECIALIST_TOOLS["options_strategy_analysis"] = "Options Strategy Agent"`, which is what nests the two MCP tool calls under the specialist in the web and TUI clients and labels the stream handler. The three passthrough `isinstance` unions gain the new event: `clients/web/hub_bridge.py`, `clients/cli/chat.py` (`_run_query`), `clients/cli/tui.py`; `clients/cli/test_multi_domain.py` optionally. `format_tool_args` shows an empty argument string for structured tools today (strategy included) and is left alone.

## 3. Routing boundaries

| Request | Route | Mode |
|---|---|---|
| Current chains, Greeks, IV, open interest, NBBO, contract snapshots, scenario or payoff math on current contracts, position risk | `options_analysis` | evidence supplier |
| Historical performance of an options strategy; validation of options strategy rules or a document; design of managed options rules — verticals, condors, straddles, strangles, single long options, and covered call, cash-secured put, wheel or roll requests (answered with the supported scope); what options-strategy backtesting OBaI supports | `options_strategy_analysis` | terminal |
| Equity and ETF share strategies, intraday and daily OHLCV backtests, walk-forward, `bt_<id>` follow-ups | `strategy_analysis` | terminal |
| Polymarket and prediction-market setups | `prediction_market_analysis` | terminal (unchanged) |
| Coinbase spot crypto | `crypto_analysis` | terminal (unchanged; already excludes options) |
| A current opportunity plus a historical test | both routes, in separate calls; current evidence keeps its date and is never fed as historical state (design §15.4) | — |

Where each rule lives (Phase B unless noted):

- `prompts/central_hub_base.md`: the `options_analysis` invariant narrows to current-market analytics; a new invariant names the historical, managed-rules, validation and capability cases for `options_strategy_analysis`; the strategy invariant says equity and ETF share strategies; the skill list, terminal-author list, mandatory pre-flight paragraph (same shape as the crypto one), runtime-relay list, control-signal list (`OPTIONS_STRATEGY_HANDOFF_ERROR:`) and the terminal error-handling list each gain the new name; plus the absent-route rule of §2.7.
- `_OPTIONS_STRATEGY_TOOL_DESCRIPTION` (§2.5); `OptionsAgent.handoff_description` loses "Use for any options-related queries" and names the current-market scope; `StrategyAgent.handoff_description` names equity and ETF share strategies and sends options structures to the new route.
- `_STRATEGY_OBJECTIVE_PATTERNS` (`central_hub_agent.py:411`) drops `covered[- ]call|wheel`: those words made an options-structure request a valid equity objective, which is how "unsupported options reach the equity engine" (design §17.3). A covered-call request that still lands on `strategy_analysis` now returns `MISSING_STRATEGY_INPUTS`, which the base prompt tells the hub to answer by pointing to what is supported; the new invariant is what keeps it from landing there.
- `hub_skills/obai-strategy-routing/SKILL.md`: description adds "Excludes options-structure strategies"; the options-structure context row (`:77`) points to `options_strategy_analysis`. `hub_skills/obai-stock-synthesis/SKILL.md`: description adds the new terminal to its exclusions. Skill bodies are documentation and routing signals, kept consistent for readers.

## 4. Tests

### 4.1 Server unit tests (Phase A, in-process, count toward coverage ≥ 90 %)

| File | Asserts |
|---|---|
| `tests/unit/test_config.py` | defaults (transport, `127.0.0.1`, 8012, INFO); env override of `PORT`; invalid `LOG_LEVEL` raises; `DEPLOYMENT_MODE == "local_single_user"` |
| `tests/unit/test_logging_config.py` | JSON renderer configured; level applied; idempotent |
| `tests/unit/test_server_tools.py` | via `fastmcp.Client(mcp)`: tool list is exactly the two names; annotations of both; capabilities: 13 `tools` entries, exactly two available, schema sha256 equals `SHA256SUMS`, `engine == VERSION`, roots, structures equal `get_args(Structure)`, `deployment_mode`, `historical_backtest.issue` shape and code, unknown family flagged; validate: `example-strategy.json` and every `tests/e2e/strategies/*.json` are valid with `leg_order`/`premium_direction` equal to `load_strategy`'s, `backtest_available is False`; rejection matrix with exact code and pointer — malformed, BOM, > 64 KiB, depth > 16, duplicate key, NaN, unknown field, `schema_version` `1.0`, SPX root, `min_open_interest 1`, put `+0.30`, unknown `fee_schedule_id`, lone surrogate in `strategy_json`; window: `2024/03/04`, `20240304`, `2024-02-30`, `end < start`; strategy and window issues reported together; `/health` and `/health/ready` bodies through `mcp.http_app()` with an ASGI transport |
| `tests/unit/test_import_graph.py` | `SERVER_ALLOWED` allowlist for `server`, `config`, `logging_config` (existing synthetic and acyclicity tests unchanged) |
| `tests/unit/test_run_models.py` | `root_issues` public: SPXW under underlying XSP ⇒ `UNSUPPORTED_PRODUCT` at index pointer; `resolve` still raises the same |

### 4.2 Service-local MCP E2E suite (Phase A, zero cost) — `tests/mcp/test_server_e2e.py`

This is the "e2e script with the new suite" that can run today, without a hub route or a paid call: it starts `python -m options_backtest.server` as a subprocess on `127.0.0.1` and a free port (`HOST`, `PORT`, `LOG_LEVEL=WARNING` in the child's env), polls `/health/ready` with an explicit cap (at most 50 attempts, 100 ms apart; on the cap it fails with the child's captured stderr), connects `fastmcp.Client("http://127.0.0.1:<port>/mcp")`, and asserts over the wire: the two tool names and annotations, the capabilities facts of §1.6, validation of `example-strategy.json`, a rejection subset (duplicate key, NaN, SPX root, `min_open_interest 1`) with codes and pointers, and both health bodies including `deployment_mode`. The process is terminated in `finally`, killed after a bounded wait. Subprocess precedent: `tests/e2e/test_suite_properties.py`. `scripts/run-all-tests.sh:25` picks it up unchanged; `tests/e2e/` keeps its golden-scenario meaning.

### 4.3 Hub offline tests

Phase A, `core_agents/tests/test_options_strategy_agent.py`: `agent_type`, `mcp_url_property`, SDK name; the handoff description names historical and managed-rules scope and excludes current chains and equity strategies; `_get_model` resolves through `get_agent_model("options_strategy")`; `initialize` against a failing `MCPToolConverter.load_tools` cleans up (the `test_prediction_markets_agent.py:151-172` shape); prompt content read from `prompts/options_strategy.md` — the five `requested_action` names, the two-re-validation bound, the four short-form headings in order, the no-figures rule, the "offer, never apply" rule.

Phase B, `core_agents/tests/test_options_strategy_hub.py` (new) and edits: `test_central_hub_builders.py` skill count 6 → 7 and the strict-schema parametrize gains `("_build_options_strategy_tool", "options_strategy_agent")`; wrapper via `on_invoke_tool` (the `test_crypto_agent.py:237-273` harness): handoff error returned unwrapped with state `None`; non-empty output wrapped with the marker and recorded; empty output returns `""` and records nothing; `Runner.run_streamed` patched to raise `MaxTurnsExceeded` ⇒ marker-wrapped `Status: failed`, state recorded, error logged; state: write in `copy_context()` visible to the parent; two concurrent invocations with an `asyncio.Barrier` keep separate content; a cancelled invocation leaves `None` in the parent (the C32 relay and cancellation obligations); init: patched `initialize` on every agent class with the options-strategy one raising ⇒ field `None`, `"options_strategy"` in `degraded_capabilities`, other optional agents intact, no `options_strategy_analysis` tool built; `enable_options_strategy=False` ⇒ never constructed, not degraded; routing facts read from `central_hub_base.md` (new invariant, pre-flight paragraph, terminal lists, control signal, absent-route rule), `_has_strategy_objective("covered call on SPY")` is false, both handoff descriptions, both skill descriptions; `clients/web/tests/test_hub_bridge_assembly.py`: `_ScriptedHub` yields hub text then `OptionsStrategyPassthroughEvent` ⇒ `response_text` is the specialist content; `test_config.py` additions of §2.2; `.claude/skills/obai-e2e-regression/tests/test_judge_packet.py`: `FINANCIAL_SPECIALIST_TOOLS` contains `options_strategy_analysis` (without it the async specialist check, error classification and the `max_specialist_calls` ceiling ignore the route).

### 4.4 Paid-gate cases (Phase B, `cases/cases.yaml`; executed only on the owner's go-ahead)

Three core cases, one CLI turn each, `expected_skills: [obai-options-strategy-routing]`, `cost: {class: low, max_specialist_calls: 1}`, `date_policy: frozen`, `data_contract_id: options-backtest-routing-slice-v1`, `provider_revisions_allowed: true`, no relative-time words, no `chain_from`. Lint's per-turn floor is `3 + 1 + 2 × 1 = 6`, so `estimated_api_calls: 6` each. Every text spec is structural and pins a product-emitted token or an identifier; no lexical `required_text` is added.

| Id | Query intent (in words) | Structural assertions |
|---|---|---|
| `CORE-OPTSTRAT-UNAVAILABLE` | a historical backtest of a supported R1 structure (an XSP put credit vertical with target-DTE and delta selection over a multi-year window), asking for a headline return | `expected_tools: [options_strategy_analysis]`; `expected_sequence: [options_strategy_analysis, options_backtest_validate_strategy_tool]`; `expected_outcome: data_unavailable` with `degraded_outcome_patterns.data_unavailable: \bDATA_ENTITLEMENT_MISSING\b|\bhistorical_options_data\b`; `required_text`: `\bXSP\b`, `\bDATA_ENTITLEMENT_MISSING\b`; `forbidden_text` (structural, `only_when_asserted: true`): `\b(?:CAGR|Sharpe|Sortino|total return|win rate|max(?:imum)? drawdown|P&L|profit)\b[^.\n]{0,25}[-+$]?\d`; `forbidden_tools: [strategy_analysis, options_analysis]`; `forbidden_claims: [new_backtest_job]`; manual: the unavailability reason traces to the captured validate payload and no figure appears |
| `CORE-OPTSTRAT-VALIDATE-ERROR` | validate and backtest the same vertical on the monthly AM-settled SPX root, explicitly not the weekly root | `expected_tools`, `expected_skills`, `expected_sequence` as above; `expected_outcome: partial_refusal`, `acceptable_outcomes: [partial_refusal, specialist_error]` (the rejection envelope carries `isError`, and the judge's outcome classifier may read it either way), `degraded_outcome_patterns.partial_refusal: \bUNSUPPORTED_PRODUCT\b`; `required_text`: `\bUNSUPPORTED_PRODUCT\b`, `/product/allowed_option_roots/\d+`, `\bSPX\b`; same performance `forbidden_text`; `forbidden_tools: [strategy_analysis, options_analysis]`; manual: SPX was validated as requested, SPXW or XSP offered at most as an alternative, never substituted |
| `CORE-OPTSTRAT-CAPABILITY` | what options-strategy backtesting OBaI supports today and whether it can backtest a wheel on an equity ETF | `expected_tools: [options_strategy_analysis]`; `expected_sequence: [options_strategy_analysis, options_backtest_capabilities_tool]`; `expected_outcome: partial_refusal`, `degraded_outcome_patterns.partial_refusal: \bUNSUPPORTED_STRUCTURE\b|\bhistorical_options_data\b`; `required_text`: `\bSPXW\b`, `\bXSP\b`, `\bhistorical_options_data\b` (the prompt requires capability answers to name supported roots and the unavailable capability tokens); same performance `forbidden_text`; `forbidden_tools: [options_analysis, strategy_analysis]`; manual: the wheel is reported unsupported in R1 without a substitute cash-secured-put analysis |

Budget changes, exact in both directions (`enforce_exact_tier_budgets: true`): `core_max_cases` 22 → 25, `core_max_api_calls` 194 → 212; smoke (8/45) and live (8/48) unchanged. `SKILL.md`: case count 38 → 41, core row `25 | 212`, core command `--max-api-calls 212`; `test_skill_doc_tier_table_matches_the_case_file` enforces the table. A smoke entry is deferred until the route carries a real run. Dependency closure is empty (no `chain_from`); `run_suite.py --dry-run --id` on the three ids must show exactly them, estimate 18, `attempted_count: 0`.

Preflight and fingerprints: `obai status` (`chat.py:666-677`) gains `("Options Backtest", config.mcp_options_backtest_url)`, so the paid gate refuses to start while the container is down — the crypto precedent (optional at runtime, required by preflight). The new `MCP_OPTIONS_BACKTEST_URL` variable and the `src/` changes invalidate every existing checkpoint, as any server change does.

Why the hub gate and not only a `--cases` file: the three cases protect a routing invariant the release gate exists for, and the owner's lesson asks for exact budget changes rather than a side suite. `src/obai/evaluation/test_cases/suite.yaml` rows are a follow-up (§7).

## 5. Phases and checklist

### Phase A — new files, plus the wholly clean `src/options-backtest-server/` tree

New: `src/options_backtest/{config.py,logging_config.py,server.py}`, `Dockerfile`, `.dockerignore`, `tests/unit/{test_config.py,test_logging_config.py,test_server_tools.py}`, `tests/mcp/{__init__.py,test_server_e2e.py}`, `src/obai/core_agents/options_strategy_agent.py`, `src/obai/core_agents/prompts/options_strategy.md`, `src/obai/core_agents/tests/test_options_strategy_agent.py`, this ADR. Existing, clean, inside the service: `pyproject.toml`, `uv.lock`, `README.md` (status and run command), `src/options_backtest/models/run.py` (rename), `tests/unit/test_import_graph.py`, `tests/unit/test_run_models.py`.

| Step | Verify |
|---|---|
| 1. Write the failing server tests (§4.1, §4.2) and `test_options_strategy_agent.py` | `uv run pytest --no-cov tests/unit/test_server_tools.py` fails on import |
| 2. Add deps and `[tool.uv]` carve-outs; `uv lock` inside the service | `uv sync`; `uv run python -W error -c "import fastmcp"` clean; lock names fastmcp 3.x |
| 3. `config.py`, `logging_config.py`, `server.py`, `root_issues` rename | `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src` |
| 4. Service suite | `uv run pytest` (coverage ≥ 90, `tests/mcp` included) |
| 5. `OptionsStrategyAgent`, prompt, agent tests | from `src/obai`: `uv run pytest core_agents/tests -q`, `uv run mypy core_agents`, `uv run ruff check .` — every pre-existing test still green |
| 6. Dockerfile, `.dockerignore` | `docker build -t obai/options-backtest-server:dev src/options-backtest-server` and `docker run --rm -p 127.0.0.1:8012:8012 -e HOST=0.0.0.0 …` then `curl -sf http://127.0.0.1:8012/health/ready` shows `deployment_mode` |
| 7. Repo scripts | `./scripts/run-all-tests.sh`, `./scripts/run-all-typechecks.sh` |

### Phase B — existing files; dirty ones only once clean

Dirty now, wait: `src/obai/core_agents/config.py`, `central_hub_agent.py`, `prompts/central_hub_base.md`, `tests/test_config.py`, `tests/test_central_hub_builders.py`, `src/obai/clients/cli/chat.py`, `.env.example`, `src/obai/clients/cli/.env.example`, `.claude/skills/obai-e2e-regression/{SKILL.md,cases/cases.yaml}`, `README.md`, `src/obai/README.md`, `src/obai/CLAUDE.md` ("9 specialist agents" → 10). Clean, edit in B for coherence: `hub_skills/obai-options-strategy-routing/SKILL.md` (new), `hub_skills/obai-strategy-routing/SKILL.md`, `hub_skills/obai-stock-synthesis/SKILL.md`, `options_agent.py`, `strategy_agent.py`, `core_agents/__init__.py` (lazy export), `clients/shared.py`, `clients/web/hub_bridge.py`, `clients/cli/tui.py`, `clients/cli/test_multi_domain.py` (optional), `core_agents/tests/test_options_strategy_hub.py` (new), `clients/web/tests/test_hub_bridge_assembly.py`, `docker-compose.yml`, `.github/workflows/docker-publish.yml`, `setup.sh`, `.claude/skills/obai-e2e-regression/scripts/judge_packet.py`, `.claude/skills/obai-e2e-regression/tests/test_judge_packet.py`.

| Step | Verify |
|---|---|
| 1. Snapshot every dirty path (done 2026-09-26: `scratchpad/obai-ec-baseline/`); edit on top of obai-ec's hunks without reverting, reformatting or rewriting them; ruff format/fix only the files this work changes, and only when doing so leaves obai-ec's hunks byte-identical | `diff` of each dirty file against its baseline shows only this work's hunks |
| 2. Failing tests first: `test_options_strategy_hub.py`, `test_config.py`, `test_central_hub_builders.py`, `test_hub_bridge_assembly.py`, `test_judge_packet.py` | they fail for the right reason |
| 3. `config.py` fields; `central_hub_agent.py` (import, field, event, state, description, wrapper, failure hook, handoff check, registration, init/cleanup/`get_specialist`, `run()` detection/emit/`finally`, objective-token removal); base prompt; skills; handoff descriptions; clients | `uv run pytest core_agents/tests clients -q`, `uv run mypy .`, `uv run ruff check . --fix && uv run ruff format .` from `src/obai` |
| 4. `docker-compose.yml`, workflow, `setup.sh`, env examples, docs | `docker compose -p obai config` parses; `docker compose -p obai up -d options-backtest-server`; `uv run obai status --json` (repo root) lists the server ok |
| 5. Gate: three cases, budgets, `SKILL.md`, `judge_packet.py` | `uv run python .claude/skills/obai-e2e-regression/scripts/lint_cases.py .claude/skills/obai-e2e-regression/cases/cases.yaml --strict` — 0 errors, 0 warnings; `uv run pytest .claude/skills/obai-e2e-regression/tests -q` |
| 6. Dry run | `uv run python .claude/skills/obai-e2e-regression/scripts/run_suite.py --dry-run --id CORE-OPTSTRAT-UNAVAILABLE --id CORE-OPTSTRAT-VALIDATE-ERROR --id CORE-OPTSTRAT-CAPABILITY --run-dir <new>` shows the three ids, estimate 18, `attempted_count: 0` |
| 7. Whole repo | `./scripts/run-all-typechecks.sh`, `./scripts/run-all-tests.sh` |
| 8. **Stop.** Paid execution only on the owner's explicit go-ahead: `--execute --id … --max-api-calls 18 --run-dir <new>` for the three, or the full core gate at `--max-api-calls 212` | disclose tier, count, estimate and the in-flight overshoot limit first (skill contract) |

Restart the app after Phase B: the prompt and base prompt deploy on start.

## 6. Risks

- **Routing habit.** The hub may still send covered-call or wheel wording to `strategy_analysis`. Four coordinated changes (invariant, description, objective tokens, skill description) and two gate cases with `forbidden_tools` are the controls; a failure is a gate failure, not a silent proxy backtest.
- **Judge classification of a rejection.** `validate_strategy` returns `isError: True` for a rejected document. `_span_error_evidence` does not unwrap the span envelope, so it is not a span error, and `SPECIALIST_ERROR_RE` does not match "strategy rejected", but the second case declares both `partial_refusal` and `specialist_error` acceptable so a classifier change cannot flip it to `fail_product`.
- **fastmcp drift.** Floor 3.2, ceiling < 4; the `-W error` import and direct decorated-function calls are re-verified against the locked version in Phase A step 2.
- **Preflight coupling.** With the server in `obai status`, every paid gate needs the container up; that is the crypto precedent and the price of a real dependency.
- **Checkpoint invalidation.** The new env variable and `src/` changes invalidate existing gate checkpoints; expected.
- **Prompt compliance for the capability case.** `CORE-OPTSTRAT-CAPABILITY` pins tokens the prompt requires in capability answers; if the model omits them the case fails as a product failure, which is the intended pressure.
- **Docker layout.** The two-step `uv sync` with `--no-install-project` is the documented uv pattern but unverified here until step 6 builds it.

## 7. Out of scope

WP2 providers, manifests and as-of data; WP4 metrics and research; WP5 jobs, control DB, artifacts, the eleven unregistered tools, worker and DB compose services, `run_one.py`'s missing `validating`/`finalizing`/`invalid` job states; WP6 proper (seven-section report, run references in the terminal state, R1 qualification, financial reviewer sign-off); WP7 American, stock, dividend and wheel lifecycle; an external `skills/obai-options-strategy/` skill, `skills/obai-hub/mcp-config.json` and `test_mcp_skill_contracts.py`; `src/obai/evaluation/test_cases/suite.yaml` rows; the pre-existing gaps the map found — `evaluation/trace/capture.py` dropping every `PassthroughEvent`, `degraded_capabilities` never shown to users, the module-global strategy and prediction holders, `setup.sh`'s missing crypto entry, `format_tool_args` showing nothing for structured tools. Each is noted, none is fixed here.

## 8. Engineering decisions recorded (not owner questions)

Strategy as validated JSON text over MCP (§0.1). Two registered tools, eleven advertised absent (§1.8). One constant `Issue` for unavailability, `DATA_ENTITLEMENT_MISSING`, no new code (§1.7). No normalized spec until WP5 (§1.5). Schema identity, not body, in capabilities (§1.6). `root_issues` made public rather than a new seam (§1.1). Readiness 200 with a three-way readiness map (§1.4). Loopback default host; host-side loopback port binding; no CORS (§1.2, §1.4, §1.10). Lockfile-pinned image, two-step sync (§1.10). Specialist model through the generic override pattern, not the strategy agent's orchestrator fallback (§2.1). `options_strategy_max_turns` 12 as the hard bound of the two-re-validation rule (§2.2, §2.3). Handoff gate is the substring check only; no missing-inputs gate (§2.5). Wrapper failures made terminal through `failure_error_function` (§2.5). Invocation state without run references; cleared by `set` in `finally`, not `Token.reset` (§2.6). Disabled is not degraded (§2.7). Generic absent-route rule in the base prompt (§2.7). `covered call` and `wheel` removed from the equity objective patterns (§3). Three core cases, budgets 25/212, no smoke case yet (§4.4). Hub skill deferred to Phase B because the skill-count test is dirty (§2.4).

Amendments of 2026-09-26, decided by the architect on the fixer's blocked report; each line supersedes the section text it names, and none is an owner question.

Amendment 2026-09-26 (H1): The options-structure control on the equity route is prompt-level and lives where design §17.3 puts it, `prompts/strategy.md` (clean in git; edited in Phase B alongside the other clean prompt and skill files): the Core Mandate gains one exception and Mode 3 one row — a request whose mechanics are an options structure (bought or written calls or puts, covered calls, cash-secured puts, wheels, spreads, condors, straddles, strangles, rolls) is never proxied with a share strategy; the Strategy Agent answers that OBaI's options strategy specialist (`options_strategy_analysis`) owns it and stops, while a share strategy in an option-income fund stays an equity request; `test_strategy_agent.py` reads the file for the rule. No lexical pre-flight is added to `strategy_analysis`; §3's claim that a covered-call request landing there returns `MISSING_STRATEGY_INPUTS` holds only for wording with no other objective token, and the §6 "Routing habit" controls become five, with the Strategy Agent's relayed refusal as the terminal answer for the rest — a token gate cannot tell written calls from an ETF that sells them (QYLD against QQQ is an equity request), so it would block valid equity work, and the `src/obai/CLAUDE.md` pitfall limits hub pre-flight to hard syntactic facts; the Strategy Agent reads the whole request and can make the distinction, so a mis-route ends in an honest refusal, never a proxy backtest.

Amendment 2026-09-26 (M4): §2.6's first-wins detection order stands; `run()` logs a warning naming every terminal holder that is set when more than one is, and a test pins that with the strategy and options holders both set the strategy output is emitted and the options content is not — two terminals in one turn is not a designed path (§3's "both routes" row pairs an evidence supplier with one terminal), it arises only from the routing error the gate's `forbidden_tools` already fails, H1 gives the mis-routed half its own honest refusal, and a multi-terminal relay policy belongs to the follow-up that migrates the module-global strategy and prediction holders (§7), not to a slice that must not change their relay.

Amendment 2026-09-26 (F1/F2/M2/M1): Three changes. (i) §1.6 "identity, not body" is reversed: the vendored `strategy.schema.json` ships as package data (`src/options_backtest/contracts/strategy.schema.json`, included in the wheel), `test_contracts_digests` covers the copy, and the capabilities payload's `versions.strategy_schema` gains `body` (the parsed schema, about 9.7 KB minified); `validate_strategy`'s `versions` stays identity-only; the prompt's "field names and values the service documents or returns" becomes "the schema body the capabilities payload returns". (ii) §1.5 step 2 is amended: when `load_strategy` rejects, the server also runs the product checks whenever stages 1–4 passed and `/product` validates as `Product` — `strategy_checks.root_membership_issues(product)` (public, extracted from `_check_roots`, same issues) and, only when that list is empty, `run.root_issues(product: Product)` (signature narrowed; `resolve()` passes `spec.product`; `test_run_models.py` follows) — and merges them with the failing stage's issues, deduplicated by (pointer, code) and sorted; `server.py`'s import allowlist (§1.1) gains `strict_json`; the validate docstring and the prompt's staging sentence say that a rejection carries the first failing ingestion stage's issues plus the product root checks whenever the product block is itself well-formed, and that later stages did not run (this supersedes the fixer's "first failing stage only" wording); `test_a_schema_rejection_stops_before_the_product_root_check` is replaced by three tests: a schema-rejected SPX document yields `SCHEMA_VIOLATION` plus one `UNSUPPORTED_PRODUCT` at `/product/allowed_option_roots/0`; a malformed product block yields the schema issues only; malformed JSON or a wrong `schema_version` yields no product issue. (iii) The non-const numeric fields the user did not state (risk fractions, liquidity thresholds, execution limits, delta tolerance, DTE bounds, holding limits) stay unsupplied: the specialist omits them and names them as inputs the user must supply, as §2.3 already says, so a bare request ends `rejected` with the missing inputs and the typed unavailability; server-side versioned assumption profiles for them are a schema change for a later work package, a follow-up in the sense of §7. `CORE-OPTSTRAT-VALIDATE-ERROR` (§4.4) is unchanged and its query is not lengthened — the specialist cannot write a document against a schema it has never seen, so the body must travel; with strict required fields and no defaults the honest first answer is a schema rejection, and running the product checks on the well-formed product block names SPX in that same answer instead of two turns later, keeps the gate's exact code-and-pointer pin, and makes a silent SPXW substitution fail structurally (its rejection carries no `UNSUPPORTED_PRODUCT`) where today only the manual assertion catches it.

Amendment 2026-09-26 (L3): `_rejection` keeps exactly `{isError, valid, error, issues}`; `CORE-OPTSTRAT-UNAVAILABLE` and `CORE-OPTSTRAT-VALIDATE-ERROR` pin `expected_sequence: [options_strategy_analysis, options_backtest_capabilities_tool, options_backtest_validate_strategy_tool]`, and `CORE-OPTSTRAT-UNAVAILABLE` gains `acceptable_outcomes: [data_unavailable, specialist_error]` for the same `isError` reason as its sibling — the typed reason already comes from a returned payload the prompt makes mandatory, and with the schema body in capabilities that call is now load-bearing for compiling the document, so the sequence is a structural fact; copying the unavailability into a rejection would duplicate a constant and imply data requirements for a document that has none.

Amendment 2026-09-26 (M5): The current-market half of a mixed request reaches the user through the specialist: §2.3's Explanation gains an optional final section, headed as the hub's dated current-market context, that relays the `Context:` block verbatim with its dates, is omitted when the block is blank, is exempt from the no-foreign-number rule for that section only, and never feeds the document or the validation report; the prompt test pins the rule. A fourth core case, `CORE-OPTSTRAT-MIXED`, covers §3's last row: a current quote question and a historical test on one underlying that both routes accept (verified offline against the options server's symbol support before the case is written); `expected_tools: [options_analysis, options_strategy_analysis]`, `expected_sequence: [options_analysis, options_strategy_analysis, options_backtest_capabilities_tool, options_backtest_validate_strategy_tool]`, `expected_skills: [obai-options-strategy-routing]`, `expected_outcome: data_unavailable`, `acceptable_outcomes: [data_unavailable, partial_refusal, specialist_error]`, `degraded_outcome_patterns` as the siblings plus `partial_refusal: \bSCHEMA_VIOLATION\b|\bUNSUPPORTED_STRUCTURE\b|\bUNSUPPORTED_PRODUCT\b`; `required_text`: the underlying token, `\bDATA_ENTITLEMENT_MISSING\b`, an ISO date `\b20\d{2}-\d{2}-\d{2}\b`; `forbidden_tools: [strategy_analysis]`; the performance `forbidden_text` without `P&L|profit` (payoff math on a current contract legitimately names a maximum profit); `date_policy: relative` with the live-data core siblings' timezone and age fields; `cost: {class: medium, max_specialist_calls: 2}`, `estimated_api_calls: 8` (floor 3 + 1 + 2 × 2); manual: every current-market figure sits only in the labelled section, traces to the captured `options_analysis` payloads, and none entered the validated document. Budgets become `core_max_cases` 26 and `core_max_api_calls` 220; `SKILL.md` 42 cases, core row `26 | 220`, command `--max-api-calls 220`; the §5 step 6 dry run names four ids and shows estimate 26 — design §15.4 puts dated current evidence into `context` for the specialist, which is pointless if the specialist may never show it; answering only the historical half contradicts that design row, and relaying hub synthesis beside the terminal output reopens the hub-authored memo §15.4 forbids; the relay rule is user-facing behaviour no offline test can exercise, so it gets the gate coverage §4.4's rationale demands.

Amendment 2026-09-26 (F3): An empty final output from the Options Strategy Agent is terminal: the wrapper records and returns, marker-wrapped, the §2.5 `failed` short form with the fixed reason that the specialist returned no text (no exception class), and `test_empty_output_returns_empty_and_records_nothing` becomes a test that the empty output is relayed as that short form; the other three terminal wrappers keep their behaviour, a pre-existing gap in the sense of §7 — SDK 0.19.1 can end a run with an empty `final_output` (a reasoning-only last turn), and the reproduction shows the hub then authoring an options memo with an invented return, which design §15.4 forbids; parity with wrappers that predate the rule is no reason to keep the hole open on the one route this slice owns, and a prompt-only fix is the control that just failed.

Amendment 2026-09-26 (L2/F5): `_REJECTED_IN_R1`'s `intraday_or_0dte` entry advertises `SCHEMA_VIOLATION` (§1.6's code set gains it for this entry alone), and a test validates one representative document per `rejected_in_r1` entry and asserts the returned code equals the advertised one — §1.6 promises the payload is read from the code that enforces it, and the code that refuses 0DTE and intraday is the schema (`min_dte` ≥ 7, the single `clock_profile`), which emits `SCHEMA_VIOLATION`; remapping ingest codes would amend ADR 0001's pinned conformance contract for a cosmetic gain, and both paths already refuse.

Amendment 2026-09-26 (F4, as executed): the five shared lines (`SKILL.md` case count, core row and core command; `cases.yaml` `core_max_cases`, `core_max_api_calls`) collide on the same lines with obai-ec's uncommitted CORE-CONGRESS-TRADES hunk, so hunk staging cannot split them. This work committed first, staging an index-only version equal to HEAD plus its own hunks (a three-way merge of the working tree against the pre-work baseline and HEAD), which carries HEAD-based sums: 41 cases, core `25 | 215`, `--max-api-calls 215`, `core_max_cases: 25`, `core_max_api_calls: 215`; that commit lints on its own. The working tree keeps both sessions' sums (42, `26 | 220`), and obai-ec's later commit carries those.

Amendment 2026-09-26 (L1/F6, recorded): `_terminal_options_strategy_failure` returns `default_tool_error_function(ctx, error)`, unwrapped and unrecorded with a warning log, for a `ModelBehaviorError` whose text starts `Invalid JSON input for tool options_strategy_analysis`, and the `failed` short form says no validation result is reported rather than that no strategy was validated — the SDK routes argument-parse errors through the same hook, and §2.5's two claims (wrapper failures are terminal; an invalid argument is an SDK rejection the judge reports as `malformed_specialist_invocation`) hold together only if the hook is scoped to failures after argument parsing; the hook cannot see whether validation already ran.

Amendment 2026-09-26 (F8/F9, recorded): `OptionsStrategyAgent` reads `options_strategy_model` and `options_strategy_reasoning_effort` as attributes, never through a `getattr` default, and the gpt-6 tier guard in `test_config.py` resolves the options-strategy default through `OptionsStrategyAgent()._get_model()`; §2.2's `defaults` entry and §4.3's `get_agent_model("options_strategy")` wording are read as that resolution — a renamed or dropped field must raise, not fall back silently, and after §2.1's 2026-09-26 amendment the literal would return the specialist tier and ignore `strategy_model`, so it no longer states the rule the guard exists to check.

## 9. Open question for the owner

1. **Remediation wording shown to users.** The unavailability `Issue` (§1.7) says "no qualified historical options data: the historical data work package (WP2) is not built". Should the user-facing `remediation` also name the procurement steps that gate WP2 (the Massive Options Advanced and Indices Starter upgrade, the written §5(d) confirmation, the DataShop sample; ADR 0001 §9), or stay at the neutral phrasing above? Default if unanswered: the neutral phrasing; procurement state is the owner's to disclose.
