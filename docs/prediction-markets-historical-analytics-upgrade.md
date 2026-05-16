# Prediction Markets Historical Analytics Upgrade

**Status**: Design draft
**Date**: 2026-05-16
**Companions**:
- [Prediction Markets Agent (Polymarket V1)](/home/sujshe/src/obai/docs/design/POLYMARKET_ANALYSIS_SYSTEM.md)
- [Prediction Markets Implementation Checklist](/home/sujshe/src/obai/docs/prediction-markets-implementation-checklist.md)
- [Prediction Markets Multi-Venue Architecture](/home/sujshe/src/obai/docs/prediction-markets-multi-venue-architecture.md)

This file lives in the tracked `docs/` root because `docs/design/` is ignored in this repository.

---

## 1. Summary

Upgrade the existing `prediction-markets-server` from a stateless live Polymarket decision-support server into a live-plus-historical Polymarket analytics server.

The upgrade should not download or bundle the full public Becker dataset. Instead, it should follow the existing equity backtest-server pattern:

1. Fetch Polymarket data on demand.
2. Normalize it into a local DuckDB cache.
3. Reuse cached history for future analysis.
4. Run deterministic calibration, backtest, and risk tools over the local cache.
5. Keep the Prediction Markets Agent responsible for interpretation, not computation.

Near-term scope remains Polymarket only. Kalshi is explicitly out of scope for this upgrade.

---

## 2. Why This Upgrade

The current server is strong for live manual trading decisions:

- market discovery
- executable bid/ask/depth snapshots
- price history
- trade flow
- holders
- leaderboard and wallet summaries
- limited setup testing

The current server is weak for historical alpha research:

- no persistent DuckDB store
- no normalized historical schema
- no robust resolved-market calibration
- no proper binary payoff backtest engine
- no Monte Carlo path risk
- no empirical Kelly or drawdown-constrained sizing
- no reusable local cache

Recent prediction-market research suggests the highest-value historical analytics are not generic price charts. They are:

- calibration by price bucket
- calibration by time-to-resolution
- longshot and favorite bias
- category-specific distortion
- execution-aware returns
- path-dependent drawdown risk
- rule performance under realistic entry/exit assumptions

This design adds those capabilities without turning the server into a low-latency trading system.

---

## 3. Current Server Baseline

The current `prediction-markets-server` is effectively stateless.

### Existing clients

- `GammaClient`: market discovery, metadata, event grouping, slugs, status, volume, liquidity.
- `ClobClient`: order book, midpoint, spread, last trade, price history.
- `DataClient`: recent trades, holders, leaderboard, wallet activity, wallet profile.

### Existing tool surface

- `search_prediction_markets`
- `explore_trending_markets`
- `get_market_details`
- `get_market_snapshot`
- `get_price_history`
- `compare_prediction_markets`
- `get_trade_flow`
- `get_top_holders`
- `get_trader_leaderboard`
- `get_wallet_activity`
- `get_wallet_profile`
- `backtest_prediction_setup`

### Existing backtest limitation

`backtest_prediction_setup` is a descriptive event study, not a real strategy engine. It:

- scans a limited set of closed markets
- filters by lifetime volume and YES price band
- uses the first history point inside the entry band
- evaluates forward price changes
- does not estimate executable fills, slippage, fees, or historical order-book depth
- does not support a structured rule language
- does not run Monte Carlo path analysis
- does not support reusable local historical state

This upgrade should keep that tool working for backwards compatibility but add a new historical analytics layer beside it.

---

## 4. Product Boundary

### In scope

- Polymarket only
- official Polymarket APIs first
- on-demand historical cache
- DuckDB-backed normalized storage
- resolved-market calibration
- longshot and favorite bias analysis
- structured prediction-market rule backtesting
- terminal-payoff accounting
- Monte Carlo risk analysis
- empirical position sizing estimates
- explicit limitations and data-quality flags

### Out of scope

- Kalshi
- full 36 GB public dataset ingestion
- order placement
- automated trading
- low-latency WebSocket ingestion
- cross-venue arbitrage
- arbitrary user SQL
- hidden "smart money" scores
- maker/taker alpha claims before reliable fill reconstruction
- wallet-level alpha claims without historical controls

---

## 5. Design Principles

### 5.1 Deterministic computation, LLM interpretation

Calibration, returns, Monte Carlo, drawdown, and sizing math must run in server code. The model should interpret results and explain tradeoffs.

### 5.2 Cache data, not conclusions

Store raw and normalized market/history/trade rows. Analysis outputs can be cached separately, but they must be invalidated by a data fingerprint.

### 5.3 No arbitrary SQL over MCP

Expose parameterized analysis tools. Do not expose a raw DuckDB query tool.

### 5.4 Source and fidelity must be explicit

CLOB price history is sampled data, not full tick history or historical book depth. Every historical result must state source, sample fidelity, and known blind spots.

### 5.5 Separate historical analytics from live execution

Historical base rates can inform a trade memo, but current executable bid/ask/depth still comes from live `get_market_snapshot`.

### 5.6 Avoid false maker/taker precision

Polymarket maker/taker reconstruction is more complex than Kalshi. Do not ship maker/taker edge tools until on-chain fills are normalized and validated.

---

## 6. Proposed Architecture

Add an internal historical analytics layer inside `prediction-markets-server`.

```text
prediction-markets-server
  |
  |- clients/
  |    |- gamma_client.py
  |    |- clob_client.py
  |    `- data_client.py
  |
  |- storage/
  |    |- db.py
  |    |- store.py
  |    `- fingerprint.py
  |
  |- data/
  |    |- downloader.py
  |    `- normalizers.py
  |
  |- engine/
  |    |- calibration.py
  |    |- backtester.py
  |    |- risk.py
  |    `- rules.py
  |
  `- tools/
       |- historical.py
       |- backtest.py
       |- market_state.py
       `- ...
```

The existing live tools remain where they are. New historical tools live in `tools/historical.py` or split by concern once they grow.

---

## 7. Configuration

Add settings to `src/prediction-markets-server/src/config.py`.

```python
prediction_duckdb_path: str = Field(default="./data/prediction_markets.duckdb")
prediction_duckdb_memory_limit: str = Field(default="4GB")
prediction_data_freshness_hours: int = Field(default=24)
prediction_max_markets_per_analysis: int = Field(default=500)
prediction_max_history_points: int = Field(default=1_000_000)
prediction_cache_ttl_hours: int = Field(default=24)
prediction_max_db_size_gb: float = Field(default=5.0)
prediction_enable_historical_tools: bool = Field(default=True)
prediction_enable_admin_tools: bool = Field(default=False)
```

Setting meanings:

- `prediction_data_freshness_hours`: freshness of cached source data such as market metadata and price history. Stale source data should be refreshed before analysis.
- `prediction_cache_ttl_hours`: freshness of cached analysis outputs in `pm_analysis_cache`. Analysis cache entries are also invalidated when their data fingerprint changes.
- `prediction_max_history_points`: maximum price-history rows read into one analysis call, across all tokens in the selected universe. If exceeded, the tool should fail with a clear limit message or ask the user to narrow the universe/fidelity.
- `prediction_max_db_size_gb`: local DuckDB disk budget. Phase 1 can report size only; later phases can enforce retention.
- `prediction_enable_admin_tools`: exposes cache/debug tools such as `ensure_prediction_market_history` as MCP tools when true. Normal user-facing analysis tools should call the same internal functions without requiring this flag.

Add dependencies to `src/prediction-markets-server/pyproject.toml`:

- `duckdb`
- `polars`
- `numpy`

Add `scipy` only when confidence intervals or statistical tests require it.

---

## 8. DuckDB Schema

### 8.1 `pm_markets`

One row per Polymarket market.

```sql
CREATE TABLE IF NOT EXISTS pm_markets (
    condition_id       VARCHAR PRIMARY KEY,
    slug               VARCHAR,
    question           VARCHAR NOT NULL,
    description        VARCHAR,
    category           VARCHAR,
    event_slug         VARCHAR,
    event_title        VARCHAR,
    start_date         TIMESTAMP,
    end_date           TIMESTAMP,
    closed_time        TIMESTAMP,
    active             BOOLEAN,
    closed             BOOLEAN,
    accepting_orders   BOOLEAN,
    volume             DOUBLE,
    volume_24h         DOUBLE,
    liquidity          DOUBLE,
    resolution_source  VARCHAR,
    uma_resolution_status VARCHAR,
    winning_outcome    VARCHAR,
    resolution_status  VARCHAR,
    resolution_method  VARCHAR,
    resolution_confidence DOUBLE,
    last_refreshed     TIMESTAMP NOT NULL
);
```

Resolution handling is a blocker for every downstream historical tool.

As of 2026-05-16, sampled closed Gamma market payloads expose fields such as `closed`, `closedTime`, `umaResolutionStatus`, `resolvedBy`, and terminal `outcomePrices`, but the sampled payloads did not expose a direct `winningOutcome` field. Implementation must still check for an explicit winning-outcome field on every payload and prefer it if present in current API responses.

Resolution rules:

1. If an explicit API winner field exists, store it in `winning_outcome`, set `resolution_method = "explicit_api"`, `resolution_confidence = 1.0`, and `resolution_status = "resolved"`.
2. Else, use terminal `outcomePrices` only when all are parseable, `closed = true`, and `umaResolutionStatus = "resolved"`.
3. For inferred terminal prices, exactly one outcome must have price `>= 0.99` and every other outcome must have price `<= 0.01`.
4. If the terminal prices are exactly `1` and `0`, set `resolution_method = "terminal_price_exact"` and `resolution_confidence = 0.99`.
5. If the terminal prices only pass the near-terminal threshold, set `resolution_method = "terminal_price_threshold"` and `resolution_confidence = 0.90`.
6. If the rule cannot identify exactly one winner, store `winning_outcome = NULL`, `resolution_status = "ambiguous"` or `"unresolved"`, and exclude the market from calibration/backtest metrics while reporting it in skipped counts.

Downstream tools must report how many markets used explicit outcomes, exact terminal-price inference, threshold inference, and how many were skipped for ambiguous resolution.

### 8.2 `pm_tokens`

One row per outcome token.

```sql
CREATE TABLE IF NOT EXISTS pm_tokens (
    token_id        VARCHAR PRIMARY KEY,
    condition_id    VARCHAR NOT NULL,
    outcome_index   INTEGER NOT NULL,
    outcome_label   VARCHAR NOT NULL
);
```

### 8.3 `pm_price_history`

Sampled outcome price history.

```sql
CREATE TABLE IF NOT EXISTS pm_price_history (
    token_id          VARCHAR NOT NULL,
    condition_id      VARCHAR NOT NULL,
    timestamp         TIMESTAMP NOT NULL,
    price             DOUBLE NOT NULL,
    fidelity_minutes  INTEGER NOT NULL,
    source            VARCHAR NOT NULL,
    fetched_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (token_id, timestamp, fidelity_minutes, source)
);
```

### 8.4 `pm_trades`

Recent/API trades first. On-chain fills can extend this later.

```sql
CREATE TABLE IF NOT EXISTS pm_trades (
    trade_key          VARCHAR PRIMARY KEY,
    source             VARCHAR NOT NULL,
    source_trade_id    VARCHAR,
    transaction_hash   VARCHAR,
    log_index          BIGINT,
    asset_id           VARCHAR,
    condition_id       VARCHAR NOT NULL,
    timestamp          TIMESTAMP,
    price              DOUBLE,
    size               DOUBLE,
    side               VARCHAR,
    outcome            VARCHAR,
    wallet             VARCHAR,
    fetched_at         TIMESTAMP NOT NULL
);
```

`trade_key` is a deterministic source-specific key, not blindly `transactionHash`. For on-chain fills, prefer `source:transaction_hash:log_index:asset_id`. For Data API rows that lack log index or asset, include enough stable normalized fields to avoid transaction-level collisions. Do not treat this as complete historical tick data unless and until a full fill backfill exists.

### 8.5 `_pm_meta`

Coverage and freshness metadata.

```sql
CREATE TABLE IF NOT EXISTS _pm_meta (
    entity_type       VARCHAR NOT NULL CHECK (
        entity_type IN ('market', 'token', 'price_history', 'trades', 'analysis')
    ),
    entity_id         VARCHAR NOT NULL,
    source            VARCHAR NOT NULL,
    first_timestamp   TIMESTAMP,
    last_timestamp    TIMESTAMP,
    row_count         BIGINT,
    fidelity_minutes  INTEGER,
    quality_flags     VARCHAR,
    last_refreshed    TIMESTAMP NOT NULL,
    PRIMARY KEY (entity_type, entity_id, source, fidelity_minutes)
);
```

### 8.6 `pm_analysis_cache`

Optional cached outputs.

```sql
CREATE TABLE IF NOT EXISTS pm_analysis_cache (
    analysis_key       VARCHAR PRIMARY KEY,
    data_fingerprint   VARCHAR NOT NULL,
    result_json        VARCHAR NOT NULL,
    created_at         TIMESTAMP NOT NULL
);
```

---

## 9. Fetch-On-Demand Pipeline

### 9.1 Market resolution path

Input can be:

- slug
- condition ID
- Polymarket URL
- topic query

Resolution order:

1. If slug or URL is present, use slug lookup.
2. If condition ID is present, use condition lookup.
3. If topic query is present, search markets and select candidates using explicit filters.
4. Store normalized market metadata.
5. Store token mapping.

### 9.2 Price history backfill

For each token:

1. Check `_pm_meta` coverage for requested fidelity.
2. If fresh and covers requested range, use cache.
3. If missing or stale, call CLOB price history.
4. Upsert into `pm_price_history`.
5. Update `_pm_meta`.

Fidelity rules:

- `fidelity_minutes` is part of the cache identity.
- A request for finer data cannot be satisfied from coarser cached data. For example, `fidelity = 15` must not be served from `fidelity = 60`.
- A request for coarser data may be served by resampling finer cached data if the finer cache fully covers the requested range.
- If both exact-fidelity and finer cache exist, prefer exact fidelity unless the tool explicitly requests resampling.
- Responses must include `cache_action` per source: `"fetched"`, `"cached"`, `"refreshed"`, or `"resampled_from_finer_cache"`.

### 9.3 Candidate universe backfill

For broad analyses:

1. Query Gamma for resolved candidate markets by topic/category/date.
2. Cap candidates with `prediction_max_markets_per_analysis`.
3. Backfill price history only for candidates that pass volume/status/date filters.
4. Emit skipped-market counts by reason.

Universe selection must be deterministic:

1. Apply explicit user filters first.
2. Require resolved or resolvable markets for calibration/backtests.
3. Sort candidates by `volume DESC`, `end_date DESC`, then `condition_id ASC`.
4. Apply `prediction_max_markets_per_analysis` after sorting.
5. Return and optionally cache the selected `condition_id` list and ordering in every analysis response.

This selected universe is part of the data fingerprint. Re-running the same analysis against the same cached data should use the same market set.

### 9.4 Trade backfill

Initial implementation:

- fetch recent/API trades for specified markets
- store them in `pm_trades`
- label coverage as partial

Later implementation:

- decode Polygon fill events
- map token IDs to outcomes
- reconstruct price, size, side, maker, taker
- validate against Data API samples

---

## 10. Historical Tool Surface

### 10.1 `ensure_prediction_market_history`

Purpose: populate cache for one or more markets.

This should be an internal function by default, not a user-facing MCP tool. Expose it as an MCP tool only when `prediction_enable_admin_tools = true`. Normal analysis tools should call the same backfill path internally and include cache behavior in their response.

Inputs:

- `identifiers: list[str]`
- `interval: str = "max"`
- `fidelity: int = 60`
- `include_trades: bool = false`

Output:

- markets found
- tokens stored
- price rows stored
- date coverage
- freshness
- quality flags

This function is useful for debugging and explicit maintenance workflows. It has no analytical value by itself, so hiding it by default keeps the MCP tool surface smaller and reduces hub routing ambiguity.

### 10.2 `analyze_prediction_calibration`

Purpose: estimate whether prices correspond to realized probabilities.

Inputs:

- `query` or `category`
- `start_date`, `end_date`
- `price_bucket_size`
- `time_to_resolution_buckets`
- `min_lifetime_volume`
- `max_markets`
- `fidelity`

Output:

- bucketed implied probability
- realized frequency
- sample size
- Brier score
- log loss
- expected calibration error
- data-quality notes

This tool implements the core insight from calibration research: prices are not always face-value probabilities, and miscalibration depends on domain and time-to-resolution.

### 10.3 `analyze_longshot_bias`

Purpose: evaluate low-probability and high-probability tail behavior.

Inputs:

- same universe filters as calibration
- `longshot_max_price`
- `favorite_min_price`
- `side: "yes" | "no" | "both"`

Output:

- longshot realized win rate
- implied probability
- excess return
- favorite comparison
- bucket-level detail
- category/time breakdown where sample size allows

### 10.4 `backtest_prediction_rule`

Purpose: simulate a structured prediction-market rule.

Minimum V1 rule schema:

```json
{
  "side": "YES",
  "entry": {
    "price_min": 0.01,
    "price_max": 0.15
  },
  "exit": {
    "type": "hold_to_resolution"
  },
  "filters": {
    "min_lifetime_volume": 1000,
    "volume_filter_mode": "lifetime_static",
    "category": "politics",
    "min_days_to_resolution": 1,
    "max_days_to_resolution": 365
  }
}
```

Initial scope:

- YES side first
- hold-to-resolution first
- one entry per market first
- no historical depth assumptions
- no leverage
- no compounding unless explicitly requested

Output:

- sample size
- win rate
- average return
- median return
- distribution percentiles
- worst trade
- best trade
- market examples
- skipped-market counts
- limitations

Return accounting:

For a YES contract bought at price `p`, terminal payoff is:

- win: `(1 - p) / p`
- lose: `-1`

For PnL per contract:

- win: `1 - p`
- lose: `-p`

The tool should report both return-on-cost and cents-per-contract where possible.

### 10.5 `monte_carlo_prediction_risk`

Purpose: quantify path dependency from a return sample.

Inputs:

- inline returns from `backtest_prediction_rule`
- `num_paths`
- `starting_bankroll`
- `position_fraction`
- `max_drawdown_limit`

V1 should require inline returns or a full `backtest_prediction_rule` result object. Do not expose `backtest_id` until backtest-result persistence, TTL, and garbage collection are explicitly designed.

Output:

- median terminal wealth
- p5/p95 terminal wealth
- median max drawdown
- p95 max drawdown
- p99 max drawdown
- probability of drawdown exceeding limit
- ruin probability under configured threshold

### 10.6 `estimate_empirical_kelly`

Purpose: provide sizing estimates from empirical returns.

Inputs:

- return distribution source
- bankroll constraints
- drawdown limit
- confidence haircut

Output:

- naive Kelly estimate
- half Kelly
- drawdown-constrained fraction
- conservative fraction
- key caveats

For pure hold-to-resolution binary bets, naive Kelly has a closed form when win probability and payoff odds are known. The tool may report that closed-form estimate, but the preferred production answer is still constrained sizing: half Kelly, capped Kelly, or grid search over position fractions subject to Monte Carlo drawdown limits. Grid search becomes more important once V2 supports early exits, repeated entries, and non-binary payoff paths.

---

## 11. Backtest Engine Design

### 11.1 Rule validation

Use typed rule models. Reject unsupported fields explicitly.

Do not silently interpret free text. The agent may translate free text into a structured rule, but the server executes only validated structures.

### 11.2 Entry selection

V1 should support:

- first eligible price point per market
- optional earliest/latest time-to-resolution filter
- optional category/topic filter
- optional `min_lifetime_volume` filter, explicitly labeled as a static universe filter

Later:

- repeated entries per market
- stop/target exits
- time exits
- trend filters
- liquidity filters from historical proxies

### 11.3 Exit handling

V1:

- hold to resolution

V2:

- exit after N hours/days
- exit at target price
- exit at stop price
- exit before event deadline

### 11.4 Data leakage controls

The engine must not use:

- final volume if the rule claims entry at an earlier time unless volume is explicitly treated as a static market filter
- final market status except for selecting resolved markets after the fact
- future price path for entry selection
- wallet leaderboard information
- any field unavailable at simulated entry time

When a filter is not historically reconstructable, the output must say so.

V1 volume filtering is explicitly point-in-time contaminated. Without historical volume snapshots, `min_lifetime_volume` uses final/lifetime volume as a static universe filter. The rule schema must name this field `min_lifetime_volume`, not `min_volume`, and every backtest response that uses it must include a limitation such as:

```text
Volume filter used final/lifetime volume, not volume known at simulated entry time.
```

---

## 12. Calibration Engine Design

### 12.1 Basic bucket calibration

Calibration must define the aggregation unit. Otherwise long-lived markets with many sampled points can dominate a bucket.

Supported sampling modes:

- `market_bucket_once`: one observation per market/outcome/price-bucket/time-to-resolution-bucket. Use the earliest eligible sampled point in that bucket. This is the default for user-facing calibration because `sample_size` means distinct market-bucket observations.
- `sample_weighted`: every sampled price point is an observation. This measures time-weighted market calibration but can overweight stale prices and long-lived markets.
- `both`: compute both views and report them separately.

For each eligible observation:

1. Determine token/outcome.
2. Determine terminal win/loss.
3. Assign to price bucket.
4. Assign to time-to-resolution bucket.
5. Aggregate realized win rate versus implied price.

Responses must report:

- `sampling_mode`
- `sample_size`
- `market_count`
- `effective_n` for `sample_weighted`
- skipped markets by reason

For `sample_weighted`, compute an `effective_n` adjustment at minimum as the count of distinct `condition_id` values contributing to the bucket, and label raw sample count separately as `raw_observation_count`.

### 12.2 Metrics

Report:

- realized frequency
- implied probability
- difference in percentage points
- excess return
- Brier score
- log loss
- expected calibration error
- sample size

### 12.3 Time-to-resolution buckets

Default buckets:

- `0_3h`
- `3_6h`
- `6_12h`
- `12_24h`
- `1_2d`
- `2_7d`
- `1_4w`
- `1m_plus`

Because Polymarket historical timestamp quality may be coarse depending on source, the tool should flag short-horizon buckets as lower confidence when source timestamps are block-derived or sparse.

### 12.4 Category classification

Initial category can use Gamma category/event tags where available.

Later, add deterministic regex classification for:

- politics
- sports
- crypto
- finance
- entertainment
- technology
- other

The classification method must be included in the output metadata.

---

## 13. Risk Engine Design

### 13.1 Return paths

Backtest returns are unordered observations unless a strategy explicitly defines chronology. The risk engine should support both:

- chronological path from actual entry timestamps
- Monte Carlo resampled paths

### 13.2 Monte Carlo

Use bootstrap resampling of historical trade returns.

For each path:

1. Sample returns with replacement or shuffle without replacement, depending on user choice.
2. Apply fixed or fractional position sizing.
3. Track equity curve.
4. Compute max drawdown.
5. Compute terminal wealth.

V1 Monte Carlo is an IID approximation. It does not model event-level correlation, simultaneous open positions, clustered outcomes, or mutually exclusive markets inside the same event. This generally understates real tail risk when a strategy trades many related markets at the same time.

Responses must include this limitation:

```text
Monte Carlo paths resample observed returns as if they were independent. Correlated event exposure and concurrent positions are not modeled, so drawdown tails may be optimistic.
```

Later versions can add event-cluster bootstrap or block bootstrap by `event_slug` and entry date.

### 13.3 Position sizing

Support:

- fixed notional per trade
- fixed fraction of bankroll
- capped fraction

Do not output precise sizing unless bankroll/risk constraints are supplied.

---

## 14. Agent Prompt Updates

Keep the existing conservative prediction-market prompt. Add rules for historical analytics:

- Treat historical analytics as base-rate evidence, not proof of current edge.
- Always separate live executable price from historical fair value or base rate.
- Never imply historical calibration guarantees future profitability.
- When using backtest results, state sample size, universe, filters, source fidelity, and limitations.
- When using Monte Carlo, state that it resamples observed historical returns and does not create new causal evidence.
- When using Monte Carlo, state that IID resampling does not model correlated event exposure unless the tool result says clustered resampling was used.
- Do not claim maker/taker alpha unless the tool result explicitly says maker/taker reconstruction was available and validated.
- For sizing, prefer qualitative guidance unless bankroll and risk constraints are supplied.

Model upgrade can be considered after deterministic tools exist. The model should not compute metrics itself.

---

## 15. Response Contracts

Every historical analytics tool should return:

- `tool`
- `universe`
- `selected_condition_ids`
- `filters`
- `cache_actions`
- `data_coverage`
- `sample_size`
- `metrics`
- `examples`
- `limitations`
- `quality_flags`

Example limitations:

- sampled price history, not tick-level trades
- no historical order-book depth
- one entry per market
- no transaction fees included
- market category inferred from tags/title
- resolved markets only
- final/lifetime volume used as a static universe filter when `min_lifetime_volume` is set
- Monte Carlo assumes independent returns unless clustered resampling is explicitly enabled

This keeps the agent from overstating the result.

---

## 16. Testing Plan

### Unit tests

- DuckDB schema initialization
- market metadata upsert
- token mapping upsert
- price history dedupe
- coverage metadata update
- calibration bucket math
- terminal payoff math
- Monte Carlo reproducibility with seed
- rule validation rejects unsupported fields

### Mocked integration tests

- `ensure_prediction_market_history` with mocked Gamma/CLOB responses
- calibration over two resolved markets
- longshot bias over a small synthetic universe
- hold-to-resolution backtest
- cache hit versus cache miss

### Regression tests

- existing 12 live tools still work
- existing `backtest_prediction_setup` output remains compatible
- response truncation still handles large historical outputs

---

## 17. Implementation Phases

### Phase 1: Storage foundation

Deliver:

- DuckDB manager
- schema DDL
- store read/write/upsert methods
- explicit resolution normalization and confidence fields
- config settings
- tests

Acceptance:

- server starts with DuckDB path
- schema initializes
- mocked market/history data can be upserted and read back
- resolution inference is deterministic, auditable, and covered by tests for explicit, exact-terminal, threshold-terminal, unresolved, and ambiguous markets

### Phase 2: On-demand history backfill

Deliver:

- downloader
- normalizers
- `ensure_prediction_market_history`
- data coverage metadata

Acceptance:

- repeated calls dedupe rows
- cache coverage is visible
- stale data refresh works
- fidelity upgrade and downgrade rules are tested
- candidate universe selection is deterministic and returned in the response

### Phase 3: Calibration and longshot tools

Deliver:

- `analyze_prediction_calibration`
- `analyze_longshot_bias`
- category/time/price buckets
- sampling modes: `market_bucket_once`, `sample_weighted`, and `both`
- basic metrics
- update `src/prediction-markets-server/CLAUDE.md` with schema/prompt dependency notes for the Prediction Markets Agent

Acceptance:

- synthetic test cases produce expected calibration
- limitations appear in every response
- long-lived market fixtures do not silently dominate default calibration output

### Phase 4: Backtest engine

Deliver:

- structured rule model
- hold-to-resolution simulation
- terminal payoff accounting
- skipped-market reasons
- `backtest_prediction_rule`
- deprecate `backtest_prediction_setup` in prompt routing and prefer `backtest_prediction_rule` for new historical requests

Acceptance:

- backtest is deterministic
- no unsupported rules are silently accepted
- sample-size and data-quality flags are clear
- lifetime-volume contamination is named in response limitations whenever `min_lifetime_volume` is used

### Phase 5: Risk and sizing

Deliver:

- Monte Carlo risk tool
- empirical Kelly grid search
- drawdown-constrained sizing
- IID limitation in tool output and agent prompt

Acceptance:

- seeded simulations are reproducible
- sizing output requires constraints or stays qualitative
- Monte Carlo output states whether it used IID, block, or event-cluster resampling

### Phase 6: Trade/fill reconstruction

Deliver later:

- deeper trade history ingestion
- on-chain fill decoding
- maker/taker role normalization
- validation against API trades

Acceptance:

- maker/taker fields are populated only when validated
- tool output can distinguish partial API coverage from full reconstructed fills

---

## 18. Highest-Value First Slice

The first implementation should be intentionally narrow:

1. Add DuckDB schema for markets, tokens, and price history.
2. Add on-demand history backfill for specific markets.
3. Add resolved-market calibration by price bucket.
4. Add longshot bias analysis.
5. Add hold-to-resolution rule backtest.
6. Add Monte Carlo drawdown over backtest returns.

This gives OBaI a defensible research layer quickly, without needing the full public dataset or hard maker/taker reconstruction.

---

## 19. Open Questions

1. Should historical tools be enabled by default in all installs, or behind `prediction_enable_historical_tools`?
2. What disk budget should the prediction DuckDB cache enforce?
3. Should candidate universe discovery rely only on Gamma tags, or add local deterministic title classification in Phase 3?
4. How much historical trade detail can the public Data API provide reliably before on-chain decoding is necessary?

Resolved design decision:

- `backtest_prediction_setup` should remain temporarily for backwards compatibility, but Phase 4 must mark it as legacy in tool descriptions and update the Prediction Markets Agent prompt to prefer `backtest_prediction_rule` for new historical backtest requests.

---

## 20. Non-Goals To Revisit Later

- Kalshi integration
- full public dataset import
- WebSocket real-time ingestion
- full historical order-book reconstruction
- account-level PnL research
- insider-trading detection
- market-manipulation scoring
- automated execution
- AutoTrader integration
