---
name: obai-strategy-routing
description: Use when the user intent involves systematic trading strategy design, backtesting, optimization, robustness analysis, rule generation, strategy repair, strategy comparison, execution handoff, or follow-up on a strategy job. Excludes prediction markets.
---

# OBaI Strategy Routing

## Purpose

Use this skill to help the Hub decide whether the request belongs with `strategy_analysis` and to prepare a clean handoff.

This skill is not the strategy author.

The Hub should not design, backtest, optimize, or judge a systematic strategy by itself when `strategy_analysis` is available.

## Decision boundary

Use this skill when the primary user intent depends on a trading strategy artifact or backtest artifact.

Relevant intent categories include:

- strategy design
- strategy evaluation
- backtest execution
- optimization
- robustness analysis
- walk-forward analysis
- signal-rule generation
- risk-rule generation
- entry-rule or exit-rule work
- universe selection for a strategy
- strategy comparison
- strategy repair
- strategy job follow-up
- execution or engine handoff

## Do not use for

Do not use this skill for:

- ordinary stock analysis without strategy construction or backtesting intent
- prediction-market analysis
- prediction-market backtesting
- Polymarket market analysis
- pure company lookup
- pure news summary
- pure fundamentals analysis
- pure options-chain analysis
- portfolio review without a strategy-construction or strategy-overlay intent
- general education that does not request a strategy artifact or backtest artifact

If prediction-market intent and equity-strategy intent both appear, keep them separate. Prediction-market work belongs to `prediction_market_analysis`. Equity strategy work belongs to `strategy_analysis`.

## Required handoff fields

Before calling `strategy_analysis`, identify as many of these fields as the user provided or the Hub resolved:

- original user request
- tradable universe
- asset type
- strategy objective
- timeframe or bar interval
- entry logic
- exit logic
- risk controls
- capital, sizing, or portfolio constraints
- evaluation horizon
- benchmark or comparison target
- data assumptions
- context gathered from other specialists

Do not invent missing fields.

## Missing-input handling

If the request lacks a concrete tradable universe and the Hub cannot resolve one through an appropriate specialist, ask one concise clarification.

If the request lacks a precise rule set but still clearly asks for strategy design, call `strategy_analysis` with the user’s objective and constraints rather than asking for every missing detail.

If the request is ambiguous between ordinary stock analysis and strategy work, use the user’s requested deliverable as the deciding factor.

## Universe resolution

Resolve the tradable universe before strategy handoff when needed.

Use direct user-provided symbols when present.

Use `screener_lookup` when the user provides a company name, sector, theme, factor, filter, or non-symbol universe description and a tradable universe is needed.

Do not use broad model memory to resolve symbols or candidate universes when a specialist can resolve them.

## Optional pre-strategy context

Gather context from other specialists only when it would materially affect the strategy request.

Context may be useful when the requested strategy depends on:

- event timing
- fundamentals
- market regime
- portfolio constraints
- options structure
- thematic screening
- research evidence

Do not add context calls merely to make the Hub look comprehensive.

Do not gather long context that the Strategy Agent can fetch or evaluate directly.

## Handoff format

Pass a compact, structured handoff into `strategy_analysis`.

The handoff should include:

- the original user request, unchanged
- resolved universe and how it was resolved
- inferred strategy objective, marked as inferred when not explicit
- user-provided constraints
- unresolved fields that matter
- compact specialist context when gathered

Do not paraphrase away technical details from the user’s request.

Do not replace user-specified parameters with more generic language.

## Output handling

`strategy_analysis` is a terminal author. The Hub relays its output.

When `strategy_analysis` returns completed or pending output:

- return the full response unchanged
- preserve all sections, tables, JSON, metadata, risk notes, and job identifiers
- preserve any pending status language, job identifier, and next-action instruction exactly
- do not summarize, restructure, or rename sections
- do not append a separate Hub conclusion
- do not apply stock synthesis formatting or coverage gates from other skills
- do not infer completion state from session memory

When `strategy_analysis` returns an error, refusal, or missing-input response:

- treat the error as the terminal output and relay it
- do not author a substitute strategy, blueprint, or alternative-platform workaround
- do not append Hub-authored portfolio construction, signal definitions, return calculations, or expected-behavior commentary
- do not speculate from training data
- the Hub may add at most one short clarifying line

The base prompt's evidence-supplier error rule does not apply to strategy errors. There is no "available verified data" to continue with for a terminal author.

When the user follows up on prior strategy output:

- preserve any job identifier and handoff metadata
- route follow-ups back through `strategy_analysis` when the answer depends on strategy state, strategy JSON, backtest results, or job status
- do not reinterpret strategy artifacts in the Hub unless the user only asks for plain-language explanation

When a response mixes strategy output with other specialists, terminal strategy output controls the final structure. Do not append separate Hub synthesis unless the Strategy Agent included it.

## Fallback behavior

If the Hub remains uncertain after loading this skill, prefer the specialist boundary over a Hub-authored strategy answer.

When uncertainty is only about missing details, pass the uncertainty explicitly or ask one concise clarification.
