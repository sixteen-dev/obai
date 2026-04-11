# Prediction Markets Implementation Checklist

Companion to:

- [Prediction Markets Agent (Polymarket V1)](/home/sujshe/src/obai/docs/design/POLYMARKET_ANALYSIS_SYSTEM.md)
- [Prediction Markets Multi-Venue Architecture](/home/sujshe/src/obai/docs/prediction-markets-multi-venue-architecture.md)

This doc exists separately from the design doc for two reasons:

1. the design doc explains **what** to build
2. this checklist explains **how** to integrate it into OBaI without missing hard-wired system seams

Unlike `docs/design/`, this file lives in a tracked docs path.

---

## 1. Scope lock

Before writing code, keep these constraints fixed:

- Polymarket only
- official Polymarket APIs only
- human-consumable outputs only
- manual execution only
- setup-based backtesting only
- no AutoTrader payloads
- no subgraphs
- no WebSockets
- no Kelly sizing
- no historical wallet scoring inside backtests

If any task pushes beyond that scope, defer it instead of quietly expanding V1.

---

## 2. Deliverables

V1 is complete only when OBaI can do all of the following end-to-end:

1. explain a Polymarket market in plain language
2. show executable market state with bid/ask/spread/depth
3. compare multiple markets
4. summarize recent trade flow and holders
5. inspect a top trader / wallet
6. suggest a manual trade thesis with entry, exit, invalidation, and risks
7. run a setup-based historical test for a user-defined pattern
8. route these requests through the central hub reliably

---

## 3. Implementation order

Build in this order:

1. server scaffold
2. API clients
3. phase-1 MCP tools
4. specialist agent
5. hub integration
6. guardrail updates
7. evaluation and tests
8. status/docs/setup updates

Do not start with wallet scoring or advanced backtests.

---

## 4. Phase 0: Product boundary and routing decisions

### Tasks

- Decide whether OBaI will accept all tradable prediction-market questions or only finance-adjacent ones.
- Confirm that prediction-market backtest requests will route to `prediction_market_analysis`, not `strategy_analysis`.
- Freeze V1 tool list.

### Files to update

- [src/obai/core_agents/prompts/central_hub.md](/home/sujshe/src/obai/src/obai/core_agents/prompts/central_hub.md)
- [src/obai/core_agents/prompts/guardrail.md](/home/sujshe/src/obai/src/obai/core_agents/prompts/guardrail.md)

### Acceptance criteria

- The routing language is unambiguous.
- No current prompt rule still forces prediction-market backtests into the equity strategy agent.
- Guardrail policy matches intended product boundary.

---

## 5. Phase 1: Server scaffold

### Tasks

- Create `src/prediction-markets-server/`.
- Add `pyproject.toml`, `Dockerfile`, `VERSION`, and `src/`.
- Add standard project files mirroring existing MCP servers:
  - `src/server.py`
  - `src/config.py`
  - `src/logging_config.py`
  - `src/__init__.py`
  - `tests/`

### Suggested structure

```text
src/prediction-markets-server/
├── Dockerfile
├── pyproject.toml
├── VERSION
├── src/
│   ├── __init__.py
│   ├── server.py
│   ├── config.py
│   ├── logging_config.py
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── gamma_client.py
│   │   ├── clob_client.py
│   │   └── data_client.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── discovery.py
│   │   ├── market_state.py
│   │   ├── flow.py
│   │   ├── wallets.py
│   │   └── backtest.py
│   └── storage/
│       ├── __init__.py
│       └── cache.py
└── tests/
```

### Acceptance criteria

- The server starts locally.
- MCP `list_tools()` succeeds.
- Health check succeeds.

---

## 6. Phase 2: Official Polymarket clients

### Tasks

- Implement Gamma API client for:
  - market search
  - market details
  - categories / tags if needed
- Implement CLOB client for:
  - order book
  - best bid / ask
  - midpoint
  - spread
  - price history
- Implement Data / leaderboard client for:
  - recent trades
  - holders
  - wallet activity where available
  - trader leaderboard
  - official open interest endpoint if available for the requested market

### Design constraints

- Use explicit timeouts.
- Normalize all IDs early: `condition_id`, `token_id`, slug.
- Do not assume midpoint is executable.
- Keep raw responses isolated in clients; return normalized domain models to tools.

### Acceptance criteria

- Each client has unit tests with mocked API responses.
- Known happy-path markets can be fetched end-to-end.
- Errors return clean, actionable messages.

---

## 7. Phase 3: Storage and caching

### Tasks

- Add DuckDB-backed cache layer.
- Cache:
  - `market_metadata`
  - `price_history`
  - `trades`
  - `wallet_snapshots`
  - `backtest_results`
- Define freshness rules:
  - metadata: short TTL
  - price history: append / range-fill
  - trades: dedupe by transaction hash

### Constraints

- Cache should improve latency, not change semantics.
- Historical records must preserve timestamps accurately.
- Do not invent historical book depth that was never stored.

### Acceptance criteria

- Repeated calls hit cache where appropriate.
- Range backfills do not duplicate rows.
- Cached and uncached responses are equivalent.

---

## 8. Phase 4: Phase-1 MCP tools

Implement these tools first and nothing else:

### `search_prediction_markets`

Returns:

- ranked matching markets
- question
- outcomes
- end date
- current odds snapshot if cheap to enrich
- volume / liquidity summary

### `get_market_details`

Returns:

- full question text
- outcomes
- resolution summary
- timing
- status
- tags / category

### `get_market_snapshot`

Returns:

- best bid
- best ask
- midpoint
- spread
- top-of-book depth
- last trade
- volume
- liquidity
- OI if available from official API

### `get_price_history`

Returns:

- YES / NO timeseries
- requested interval
- timestamps normalized

### `compare_prediction_markets`

Returns:

- side-by-side comparison of 2-5 markets on odds, spread, depth, liquidity, and time to resolution

### `get_trade_flow`

Returns:

- recent buy/sell flow summary
- large trade counts
- notable recent prints
- explicit caveat that this is recent flow, not a proof of edge

### `get_top_holders`

Returns:

- top holders
- concentration view
- concentration risk summary

### `get_trader_leaderboard`

Returns:

- official top traders
- relevant metrics available from official endpoint

### `get_wallet_activity`

Returns:

- recent trades
- currently active markets if available
- recent directional behavior

### `get_wallet_profile`

Returns:

- descriptive summary only
- preferred categories
- recent activity level
- recent directional tendency

This tool must not claim durable alpha without proper historical controls.

### `backtest_prediction_setup`

Returns:

- sample size
- forward-return windows
- hit rate
- median / average move
- examples
- limitations

### Acceptance criteria

- All Phase-1 tools are exposed via MCP.
- Tool contracts are documented in code and tests.
- No deferred tool leaks into the implementation.

---

## 9. Phase 5: Specialist agent

### Tasks

- Add [src/obai/core_agents/prediction_markets_agent.py](/home/sujshe/src/obai/src/obai/core_agents/prediction_markets_agent.py)
- Inherit from `BaseAgent`
- Set:
  - `agent_type = "prediction_markets"`
  - `mcp_url_property = "mcp_prediction_markets_url"`
- Add a dedicated prompt file:
  - [src/obai/core_agents/prompts/prediction_markets.md](/home/sujshe/src/obai/src/obai/core_agents/prompts/prediction_markets.md)

### Prompt requirements

The specialist prompt must enforce:

- explain the market first when ambiguity exists
- prefer executable prices over midpoint-only answers
- produce human trade memos, not raw metric dumps
- state uncertainty explicitly
- never claim an edge from leaderboard presence alone
- never use future information in historical analysis

### Acceptance criteria

- Agent initializes and loads tools.
- Simple specialist-only queries produce coherent responses.
- Tool use is bounded to prediction-market scope.

---

## 10. Phase 6: Config integration

### Tasks

- Add config fields in [src/obai/core_agents/config.py](/home/sujshe/src/obai/src/obai/core_agents/config.py):
  - `mcp_prediction_markets_url`
  - `prediction_markets_model`
- Add server config in the new server package for:
  - base URLs
  - timeout
  - cache path
  - data directory

### Acceptance criteria

- New config fields have defaults.
- Overrides work through env vars.
- Missing config fails clearly.

---

## 11. Phase 7: Central hub integration

### Tasks

- Update [src/obai/core_agents/central_hub_agent.py](/home/sujshe/src/obai/src/obai/core_agents/central_hub_agent.py) to:
  - instantiate `PredictionMarketsAgent`
  - initialize it with the specialist set
  - expose `.as_tool()` as `prediction_market_analysis`
  - include stream handler wiring
  - include cleanup logic
- Update [src/obai/core_agents/prompts/central_hub.md](/home/sujshe/src/obai/src/obai/core_agents/prompts/central_hub.md) to:
  - add routing rules
  - define mixed routing with `market_data_analysis` and `research_analysis`
  - carve out prediction-market backtests from `strategy_analysis`

### Acceptance criteria

- Hub can route a simple Polymarket question correctly.
- Hub can route a mixed question to multiple specialists.
- Prediction-market backtest prompts do not go to the equity strategy agent.

---

## 12. Phase 8: Guardrails

### Tasks

- Update [src/obai/core_agents/prompts/guardrail.md](/home/sujshe/src/obai/src/obai/core_agents/prompts/guardrail.md)
- Possibly update [src/obai/core_agents/guardrails.py](/home/sujshe/src/obai/src/obai/core_agents/guardrails.py) only if messaging or model contract needs adjustment

### Requirements

Guardrail must allow:

- Polymarket
- prediction-market odds questions
- wallet / trader questions on Polymarket
- prediction-market trade analysis
- prediction-market setup backtests

It must still reject:

- general sports chat
- general politics chat
- non-market entertainment questions

unless product scope is explicitly broadened beyond trading analysis.

### Acceptance criteria

- Prediction-market prompts pass.
- Non-market sports/politics prompts still fail if intended.

---

## 13. Phase 9: Backtesting implementation

### Scope

Implement only `backtest_prediction_setup`.

### Tasks

- Define normalized setup schema.
- Support filters such as:
  - category
  - price threshold
  - volume threshold
  - liquidity threshold
  - time-to-resolution window
  - optional recent wallet activity filters, but descriptive only unless as-of-time rules are enforced
- Compute forward outcomes over fixed windows.

### Hard rules

- No end-of-history wallet ranking.
- No assumed fills from missing historical book data.
- No generalized "strategy JSON" engine in V1.

### Acceptance criteria

- Tool can answer a concrete setup question over historical data.
- Output includes sample size and limitations.
- Tool refuses unsupported assumptions instead of faking them.

---

## 14. Phase 10: Tests

### Server tests

Add tests for:

- client parsing
- tool schemas
- tool happy paths
- tool failure paths
- cache behavior
- backtest setup logic

### Agent tests

Add tests for:

- prediction-markets agent initialization
- prompt loading
- hub registration
- cleanup behavior

### Routing tests

Add hub/eval tests for:

- "What is this Polymarket asking?"
- "Compare these two markets"
- "What is the best current Polymarket bet?"
- "Show top Polymarket traders"
- "Trace this wallet"
- "Backtest this setup over the last year"
- mixed prediction-market + equity query

### Acceptance criteria

- Tests cover both happy path and blocked path.
- Prediction-market routing is explicit in evaluation fixtures.

---

## 15. Phase 11: Status, setup, docs

### Tasks

- Update CLI/server status checks:
  - [src/obai/evaluation/cli.py](/home/sujshe/src/obai/src/obai/evaluation/cli.py)
  - [src/obai/clients/cli/test_connection.py](/home/sujshe/src/obai/src/obai/clients/cli/test_connection.py)
- Update top-level docs from 8 MCP servers to 9 where applicable:
  - [README.md](/home/sujshe/src/obai/README.md)
  - [src/obai/README.md](/home/sujshe/src/obai/src/obai/README.md)
- Add Docker Compose service for `prediction-markets-server`

### Acceptance criteria

- Status command includes the new server.
- Setup docs are internally consistent.
- Architecture docs do not still describe only 8 servers.

---

## 16. V1 definition of done

V1 is done when:

- the new server runs
- the new specialist agent loads
- the hub routes correctly
- guardrails allow intended queries
- a user can get:
  - market understanding
  - market comparison
  - a manual trade memo
  - wallet/trader inspection
  - a setup-based backtest
- tests exist for the new routing and tool surface
- docs and status flows recognize the ninth MCP server

If any of the following are still required for the demo to work, V1 is not actually done:

- manual prompt hacks
- bypassing guardrails
- using the equity strategy engine for prediction-market backtests
- undocumented assumptions about midpoint fills or future wallet rankings

---

## 17. Deferred backlog

Do not start these until V1 works:

- as-of-time wallet scoring
- calibration engine
- smart-money composite signal
- cross-asset correlation engine
- trader fingerprinting
- cross-platform support
- machine-consumable AutoTrader payloads
- continuous monitoring / alerts

---

## 18. Recommended next coding slice

If implementing immediately, start with this smallest useful slice:

1. scaffold `prediction-markets-server`
2. implement `search_prediction_markets`
3. implement `get_market_details`
4. implement `get_market_snapshot`
5. add `PredictionMarketsAgent`
6. wire `prediction_market_analysis` into the hub
7. add one routing test and one end-to-end happy-path query

That gets a real vertical slice working before the more complex wallet and backtest work.
