# Prediction Markets Multi-Venue Architecture

Companion to:

- [Prediction Markets Agent (Polymarket V1)](/home/sujshe/src/obai/docs/design/POLYMARKET_ANALYSIS_SYSTEM.md)
- [Prediction Markets Implementation Checklist](/home/sujshe/src/obai/docs/prediction-markets-implementation-checklist.md)

This note defines how the current Polymarket-only implementation should evolve if OBaI later adds Kalshi or other prediction-market venues.

---

## 1. Core decision

Prediction markets should remain a **separate domain** from stocks, spot crypto, options, and portfolio analysis.

Do **not** merge prediction-market logic into the generic market-data stack.

Reason:

- stocks and spot crypto analyze assets
- prediction markets analyze contracts with explicit resolution rules
- the important fields are different
- the execution model is different
- cross-venue event matching is harder than same-venue analysis

The correct boundary is:

- one `PredictionMarketsAgent`
- one prediction-markets server domain
- many venue adapters underneath it over time

---

## 2. What stays the same

When more venues are added later, these should stay stable:

- hub tool name: `prediction_market_analysis`
- user-facing domain: prediction-market analysis
- human output contract: trade memo, market explainer, wallet/trader summary
- stock / crypto / options specialists remain separate

This means the user asks for a prediction-market question once, and the hub routes to one specialist regardless of venue.

---

## 3. What changes

The current implementation is effectively:

- `PredictionMarketsAgent`
- `prediction-markets-server`
- Polymarket-specific clients
- Polymarket-specific tool behavior

The future shape should be:

```text
Central Hub
  |
  `- prediction_market_analysis
         |
         `- prediction-markets-server
                |
                |- venue router
                |    |- polymarket adapter
                |    `- kalshi adapter
                |
                |- canonical market model
                |- canonical orderbook model
                |- canonical wallet/trader model
                `- venue-aware tools
```

---

## 4. The real hard problem

Adding another venue client is not the hard part.

The hard part is **market identity and comparability** across venues.

Examples:

- same event, different wording
- same event, different resolution rules
- binary market on one venue, ranged contract on another
- different fee models
- different tick sizes
- different liquidity depth and spread behavior
- different trader identity surfaces

So the near-term architecture should support:

- single-venue analysis well
- explicit venue selection
- future multi-venue support

It should **not** assume automatic cross-venue merging is easy.

---

## 5. Canonical domain model

Before adding Kalshi, define canonical internal objects.

### 5.1 Canonical market

Minimum canonical fields:

- `venue`
- `market_id`
- `event_id`
- `slug`
- `title`
- `question`
- `outcomes`
- `status`
- `open_time`
- `close_time`
- `resolution_time`
- `resolution_rules`
- `resolution_source`
- `category`
- `tags`

### 5.2 Canonical executable state

- `venue`
- `market_id`
- `outcome`
- `token_or_contract_id`
- `best_bid`
- `best_ask`
- `midpoint`
- `spread`
- `bid_depth_top5`
- `ask_depth_top5`
- `last_trade_price`
- `volume`
- `open_interest`
- `liquidity_score`

### 5.3 Canonical trader / wallet summary

- `venue`
- `trader_id`
- `display_name`
- `profile_url`
- `activity_count`
- `recent_markets`
- `volume_traded`
- `pnl`
- `metadata_quality`

Do not force fields that one venue cannot support. Allow `None` and carry a `limitations` note.

---

## 6. Adapter pattern

Each venue should implement the same logical interface.

Example adapter responsibilities:

- `search_markets`
- `get_market_details`
- `get_market_snapshot`
- `get_price_history`
- `get_trade_flow`
- `get_top_holders_or_equivalent`
- `get_trader_leaderboard_or_equivalent`
- `get_trader_activity_or_equivalent`
- `run_setup_backtest`

Important:

- the adapter returns canonical models
- the tool layer does not parse venue-specific payloads directly
- venue-specific quirks stay inside the adapter

This lets the specialist prompt stay stable.

---

## 7. Tool evolution path

### 7.1 V1

Current tool shape can stay effectively Polymarket-only.

### 7.2 Next safe step

Add an optional `venue` parameter to the internal tool layer, defaulting to `polymarket`.

Examples:

- `search_prediction_markets(query, venue="polymarket")`
- `get_market_details(market_id, venue="polymarket")`
- `compare_prediction_markets(..., venue="polymarket")`

User-facing behavior:

- if no venue is provided, default to Polymarket
- if user explicitly asks for Kalshi before support exists, answer that it is not yet supported

### 7.3 Multi-venue support

Once Kalshi exists:

- allow `venue="kalshi"`
- allow `venue="auto"` only for discovery, not for execution-sensitive comparisons

Do **not** auto-merge venues for trade memos until event matching is reliable.

---

## 8. Routing rules

The hub should route by **domain**, not by venue brand alone.

Good triggers:

- `prediction market`
- `Polymarket`
- `Kalshi`
- `event odds`
- `YES/NO market`
- `market resolution`
- `manual trade on a prediction market`

The hub should not route generic future-outcome questions into prediction markets unless market intent is clear.

Examples:

- `Will Bitcoin hit 100k this year?` -> ambiguous, ask or infer from context
- `What is Polymarket pricing for Bitcoin hitting 100k this year?` -> prediction markets
- `How is BTC trading right now?` -> market data, not prediction markets

---

## 9. Cross-venue comparison policy

This should be a later phase.

Support these phases in order:

1. Polymarket-only analysis
2. Kalshi-only analysis
3. venue-explicit comparisons by the user
4. cross-venue event matching
5. cross-venue trade opportunity comparison

Do not jump from phase 1 to phase 5.

Cross-venue comparison should require:

- contract wording similarity
- matching resolution windows
- compatible outcome structure
- explicit note when rules differ

If those checks are weak, the agent should refuse to present the markets as directly comparable.

---

## 10. Server refactor path

Do this incrementally. Do not rewrite the current branch.

### Step 1

Keep current server and Polymarket tools as-is functionally.

### Step 2

Move current clients behind a Polymarket adapter package:

```text
src/prediction-markets-server/src/adapters/
├── base.py
├── polymarket/
│   ├── adapter.py
│   ├── gamma_client.py
│   ├── clob_client.py
│   └── data_client.py
```

### Step 3

Create canonical model helpers:

- `models/market.py`
- `models/executable_state.py`
- `models/trader.py`

### Step 4

Make tools depend on the adapter interface, not Polymarket clients directly.

### Step 5

Add venue selection in config and tool args.

### Step 6

Only then add `kalshi/adapter.py`.

---

## 11. Prompt and UX guidance

The specialist prompt should say:

- this is a prediction-market specialist
- current supported venue is Polymarket
- other venues may be added later

That avoids baking `Polymarket` into the long-term product identity while staying honest about current implementation limits.

---

## 12. What not to do

Avoid these mistakes:

- merging prediction markets into stock or crypto spot analysis
- making the hub choose between stocks and prediction markets using only asset keywords
- pretending cross-venue markets are identical without resolution-rule checks
- baking Polymarket field names into tool contracts forever
- exposing multi-venue “best bet” logic before event matching is trustworthy

---

## 13. Recommended near-term move

Do this now:

- keep `PredictionMarketsAgent` as a separate specialist
- keep V1 implementation Polymarket-only
- make wording venue-aware: `currently supporting Polymarket`
- treat Kalshi support as an adapter roadmap, not a redesign trigger

That gives you the right product boundary today and the right extension point later.
