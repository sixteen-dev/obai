---
name: obai-prediction-market-routing
description: Use when the user intent involves prediction markets, Polymarket, event odds, YES or NO market pricing, market discovery, market comparison, trade memos, wallet or trader analysis, holder analysis, leaderboard analysis, or prediction-market backtesting.
---

# OBaI Prediction Market Routing

## Purpose

Use this skill to help the Hub decide whether the request belongs with `prediction_market_analysis` and to prepare a clean handoff.

This skill is for prediction-market workflows only.

The Hub should not price, rank, backtest, or analyze prediction markets by itself when `prediction_market_analysis` is available.

## Decision boundary

Use this skill when the primary user intent depends on prediction-market data, market discovery, market structure, market odds, market liquidity, market side selection, market execution, or market participant analysis.

Relevant intent categories include:

- prediction-market discovery
- current market odds
- YES or NO side analysis
- market comparison
- market memo or trade memo
- executable pricing
- spread or liquidity assessment
- market follow-up
- wallet analysis
- trader analysis
- holder analysis
- leaderboard analysis
- market backtesting
- market setup analysis

## Do not use for

Do not use this skill for:

- ordinary stock analysis
- equity strategy backtesting
- general macro explanation without prediction-market data need
- company fundamentals
- options-chain analysis
- portfolio review
- strategy-engine handoff
- generic event discussion that does not require prediction-market data

Do not route prediction-market backtesting to `strategy_analysis`.

Do not route equity strategy backtesting to `prediction_market_analysis`.

## Market identity preservation

Prediction-market follow-ups require exact routing keys.

Preserve these fields when available:

- market URL
- slug
- exact market question
- market title from the tool
- condition identifier only when needed by the specialist or requested by the user
- token identifier only when needed by the specialist or requested by the user

Do not replace exact routing keys with paraphrased text.

Do not invent market URLs, slugs, condition identifiers, token identifiers, or outcome identifiers.

## Handoff preparation

Before calling `prediction_market_analysis`, identify as many of these fields as the user provided or the Hub resolved:

- original user request
- event or market scope
- market URL or slug
- target side if specified
- requested output type
- time horizon
- execution relevance
- liquidity relevance
- wallet or participant target
- prior market identity from conversation context
- related stock, macro, or news context if explicitly relevant

Do not invent missing fields.

## Specialist context

Use other specialists only when their output materially affects the prediction-market request.

Context may be useful when the request depends on:

- related company or asset movement
- news catalysts
- macro events
- fundamentals
- broader research evidence

Do not gather equity context when the user only asks for prediction-market odds, liquidity, or market identity.

## Output handling

`prediction_market_analysis` is a terminal author. The Hub relays its output with light readability cleanup only.

When `prediction_market_analysis` returns completed output:

- preserve markets, prices, odds, bid, ask, spread, depth, liquidity, execution constraints, timing, catalysts, market URLs, and slugs
- preserve YES and NO side references and market scope
- allowed cleanup: add markdown headings, compact spacing, group related lines, convert dense text into short bullets without changing meaning
- do not rename markets or change scope
- do not invent identifiers
- do not expose `condition_id` or `token_id` unless the user requests raw identifiers, execution payloads, or debugging

When `prediction_market_analysis` returns an error, refusal, or missing-input response:

- treat the error as the terminal output and relay it
- do not author a substitute market analysis
- do not invent market URLs, slugs, prices, odds, or liquidity figures
- do not speculate from training data about market outcomes
- the Hub may add at most one short clarifying line

When the user follows up on prior prediction-market output, preserve routing keys with this priority:

1. market URL
2. slug
3. exact market question
4. other tool-provided identifier

Do not replace tool-provided routing keys with paraphrase. Do not use session memory as a substitute for current odds, current liquidity, or current market state.

When a response mixes prediction-market output with stock, news, or research output:

- preserve the prediction-market section as returned
- place equity or macro evidence in a clearly separate section
- do not blend prediction-market odds with equity metrics unless a specialist output supports the link
- do not let stock formatting overwrite market URLs, slugs, prices, or liquidity details

## Mixed intent

If the request contains both prediction-market and equity-analysis intent, keep the two workflows separate.

Use prediction-market output for market odds, pricing, liquidity, market identity, and market participant analysis.

Use stock, news, research, or fundamentals specialists for equity or macro evidence.

Do not merge the two into one conclusion unless the evidence supports the connection.

## Fallback behavior

If the Hub remains uncertain after loading this skill, prefer routing to `prediction_market_analysis` when the answer would require current prediction-market state, market identity, or market participant data.
