---
name: obai-prediction-market-routing
description: Use when the user intent involves prediction markets, Polymarket, event odds, YES or NO market pricing, market discovery, market comparison, trade memos, wallet or trader analysis, holder or leaderboard analysis, prediction-market backtesting, or follow-ups on prior prediction-market output. Excludes equity strategy backtesting.
---

# OBaI Prediction Market Routing

## When to use

Use this skill to help the Hub decide whether the request belongs with `prediction_market_analysis` and to prepare a clean handoff. This skill is not the prediction-market analyst — the Hub should not price, rank, backtest, or analyze prediction markets by itself when `prediction_market_analysis` is available. Polymarket is the only supported venue, the order book moves on live event flow, and the Hub has no source for current pricing or liquidity.

Use this skill when the primary user intent depends on prediction-market data, market structure, market odds, market liquidity, market side selection, market execution, or market participant analysis. Relevant intent categories:

- prediction-market discovery
- current market odds
- YES or NO side analysis
- market comparison or ranking
- market memo or trade memo
- executable pricing, spread, or liquidity assessment
- wallet, trader, holder, or leaderboard analysis
- market backtesting or setup analysis
- follow-ups on prior prediction-market output
- historical prediction-market analytics (calibration, longshot bias, base rates by category or time-to-resolution)
- resolved-market calibration ("are 30¢ markets really 30% probability?")
- longshot vs favorite bias analysis
- structured prediction-market rule backtests
- Monte Carlo risk or drawdown analysis over prediction-market returns
- empirical Kelly or drawdown-constrained sizing for prediction markets
- edge or mispricing of a live market vs resolved-market base rates ("is this 30¢ market cheap?")

Do not use this skill for:

- ordinary stock analysis or equity strategy backtesting
- company fundamentals, options-chain analysis, or portfolio review
- general macro explanation without a prediction-market data need
- generic event discussion that does not require prediction-market data
- strategy-engine handoff

If the request mixes prediction-market intent with equity intent, keep them separate and route each to its specialist.

Do not route prediction-market backtests to `strategy_analysis`. The equity strategy engine does not handle binary event markets.

## Required handoff inputs

When calling `prediction_market_analysis`, the Hub is responsible for two things:

- preserve the user's request verbatim — do not change scope, count, intent, market universe, or identifiers
- preserve any tool-provided routing keys (`market_url`, `slug`, `condition_id`, `token_id`) from prior conversation, exactly as they appeared

Everything else — fair-value reasoning, side selection, sizing logic, decision criteria, fee or slippage assumptions — stays inside the user's quoted request. Do not extract these into Hub-authored fields. The Prediction Market Agent owns implementation details.

Identify and pass through these contextual fields when the user provided them or prior conversation made them clear:

- event or market scope
- target side, if specified
- requested output type
- time horizon
- whether the user cares about execution feasibility or liquidity
- wallet or participant target
- related stock, macro, or news context, if explicitly relevant

Do not invent missing fields. Do not paraphrase tool-provided routing keys into descriptions or labels — pass exact identifiers unchanged.

## Optional cross-specialist context

Gather context from other specialists only when their output materially affects the prediction-market request:

- related company or asset movement → `market_data_analysis`
- news catalysts or recent developments → `events_news_analysis`
- macro events → `events_news_analysis`
- company fundamentals → `fundamentals_analysis`
- broader qualitative or thematic research → `research_analysis`

Do not gather equity context when the user only asks for prediction-market odds, liquidity, or market identity. Do not add context calls merely to make the Hub look comprehensive.

When you do call cross-specialists, call them BEFORE `prediction_market_analysis`, not after. The runtime drops any text the Hub authors after `prediction_market_analysis` fires — equity, news, research, or fundamentals context placed after the prediction call will be lost.

## Missing-input handling

If the request references a specific market but no routing key is available and the Hub cannot recover one from prior conversation context or tool output, ask one concise clarification (1–2 sentences) — typically asking for a Polymarket URL or slug.

If the request asks for discovery (find markets about X), pass the topic to `prediction_market_analysis` directly. Do not ask the user for a slug they could not yet have.

## Output handling

`prediction_market_analysis` is a terminal author. The runtime emits the specialist's output to the user directly and discards any text the Hub authors after the tool returns. Once the tool returns, the Hub's job for this turn is finished — do not write a summary, framing, or wrapper text.

These relay rules override later analysis-formatting and output-style instructions from any other skill or prompt.

When the tool result starts with the literal prefix `__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:`, treat the first line as a control marker. Everything after the first blank line is the user-facing prediction output that the runtime will emit.

### What the runtime preserves

The specialist's section layout, every per-market `question`, `slug`, and `market_url`, all pricing fields (YES/NO best bid, best ask, spread, last trade), liquidity and 24h volume, tick size, order minimum size, end date, resolution language and timing, the upcoming catalyst, surfaced risks, and the thesis or interpretation paragraphs the specialist wrote. `condition_id` and `token_id` are kept internal unless the user explicitly asked for raw identifiers, execution payloads, or debugging.

### Error, refusal, or missing-input output

- treat the error as the terminal output and relay it
- do not author a substitute market analysis
- do not invent market URLs, slugs, prices, odds, or liquidity figures
- do not speculate from training data about market outcomes
- the Hub may add at most one short clarifying line

The base prompt's evidence-supplier error rule does not apply to prediction-market errors. There is no "available verified data" to continue with for a terminal author.

### Follow-up output

When the user follows up on prior prediction-market output, preserve routing keys with this priority:

1. `slug` (most reliable for downstream tool calls)
2. `market_url`
3. exact market question
4. other tool-provided identifier

Pass the current user request together with the relevant prior routing keys. Never replace tool-provided routing keys with names, paraphrases, descriptions, or Hub-inferred labels.

If the follow-up asks the specialist to rank, narrow, compare, or select from prior results, pass the prior market set and let `prediction_market_analysis` perform the selection using live data. The Hub must not preselect, rename, or re-search those markets unless the user explicitly asks for new markets — odds and liquidity move on event flow, and the Hub has no source for current state.

Do not use session memory as a substitute for current odds, current liquidity, or current market state. `prediction_market_analysis` requires live API data; cached snapshots go stale immediately.

### Mixed-specialist output

If both prediction-market and equity intent appear in the same request, route each in a separate turn. Within a single turn the runtime emits the prediction specialist output verbatim and drops anything else the Hub writes, so equity, news, research, or fundamentals context cannot be added inline alongside the prediction relay.

## Fallback behavior

If the Hub remains uncertain after loading this skill, prefer routing to `prediction_market_analysis` when the answer would require current prediction-market state, market identity, or market participant data.

When uncertainty is only about missing details, pass the uncertainty explicitly or ask one concise clarification.
