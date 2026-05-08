---
name: obai-grounding-and-cache
description: Use when deciding whether cached data is sufficient, when live market or prediction-market freshness matters, or when finalizing numeric, time-sensitive, or source-sensitive claims.
---

# OBaI Grounding and Cache Rules

## Purpose

Use this skill to decide whether the Hub can rely on prior context or must call a specialist.

This skill governs numeric claims, live data, stale data, and session-cache use.

## Core rule

Use specialist tools for live, time-sensitive, numeric, or state-dependent financial claims.

The Hub may answer static conceptual questions without tools, but it should state when no live data was used if that matters to the user request.

## Data that usually requires tools

Call a specialist when the answer depends on current or recent state from any of these categories:

- prices
- returns
- volume
- volatility
- fundamentals
- estimates
- earnings data
- news
- catalysts
- options chains
- implied volatility
- portfolio state
- screener results
- prediction-market odds
- prediction-market liquidity
- prediction-market participant data
- backtest results
- strategy job state

## Session cache use

Use session cache only when all of these are true:

- the cached result directly answers the current request
- the cached result came from a specialist or trusted runtime source
- the data is not stale for the requested purpose
- the user is asking a follow-up that depends on already retrieved evidence
- using the cache would not change the route that should be taken

## Do not use cache as final source for

Do not use cache as the final source for:

- strategy design
- backtesting
- strategy job state
- prediction-market analysis
- current odds
- current liquidity
- market participant data
- current price
- current options chain
- recent news
- current screener result

Cache may provide continuity, but the relevant specialist should be called when current state matters.

## Numeric claims

For each numeric claim:

- tie it to a specialist output or valid cache entry
- avoid rounding that changes interpretation
- preserve units, dates, periods, and sides
- distinguish current, historical, forecast, and backtested values
- avoid causal language unless the evidence supports timing and mechanism

## Time sensitivity

Treat data as time-sensitive when the user asks about current state, recent events, active markets, open jobs, execution, risk exposure, or any changing market condition.

When freshness is unclear, prefer a specialist call over a cache-only answer.

## Forward-looking and hypothetical questions

When the user asks a forward-looking or hypothetical question, gather evidence from specialists first and frame the answer around what the data supports. Do not answer from model memory just because the question is about the future — current state still grounds the analysis.

## Missing or stale data

If needed data is missing or stale:

- state the limitation directly
- explain how it affects confidence or scope
- avoid filling the gap from model memory
- avoid presenting stale values as current values

## Source priority

When sources conflict, use this order:

1. specialist output generated for the current request
2. specialist output from the same session when still fresh for the task
3. trusted runtime context
4. model knowledge only for static concepts

Do not let generic model knowledge override a current specialist result.

## Final answer discipline

Keep unsupported claims out of the final answer.

When evidence is partial, answer within the evidence boundary rather than expanding the conclusion. Prefer fewer verified facts over broad but uncertain coverage.
