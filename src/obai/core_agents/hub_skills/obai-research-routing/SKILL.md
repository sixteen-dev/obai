---
name: obai-research-routing
description: Use when the user requests broad investment research, thesis development, bull and bear analysis, evidence comparison, source-grounded company or market research, or synthesis that goes beyond a single data specialist.
---

# OBaI Research Routing

## Purpose

Use this skill to decide when the Hub should involve `research_analysis` and how to combine it with other specialists.

Research routing is for broad evidence synthesis, not for replacing specialist data tools.

## Decision boundary

Use this skill when the user asks for a research-style answer that requires multiple evidence types, source comparison, or thesis construction.

Relevant intent categories include:

- investment thesis
- bull and bear case
- risk analysis
- evidence comparison
- source-grounded research
- multi-factor company research
- macro or sector research
- catalyst interpretation
- uncertainty analysis
- due-diligence style synthesis

## Do not use for

Do not use this skill for:

- pure live price lookup
- pure ticker lookup
- pure screener lookup
- pure options-chain analysis
- pure portfolio math
- terminal strategy output
- terminal prediction-market output
- strategy backtesting
- prediction-market backtesting

Research is not a substitute for specialized current data when the user asks for current state.

## Specialist coordination

Use `research_analysis` when the answer needs broad interpretation, thesis framing, or source comparison.

Use direct specialists when the answer needs concrete data from a specific domain.

The Hub may combine research with:

- market data for price and trend context
- fundamentals for valuation and operating context
- events and news for catalysts
- options for derivatives context
- screener lookup for universe construction
- portfolio analysis for allocation context

Do not call every specialist by default.

## Handoff preparation

Before calling `research_analysis`, identify:

- the research subject
- the requested research depth
- the desired perspective or decision frame
- known tickers or entities
- time horizon
- user constraints
- specialist evidence already gathered
- specific unresolved questions

Pass compact context, not raw transcript bloat.

## Output handling

Research output is usually evidence-supplier output, not terminal output.

The Hub may synthesize research output with other specialists using `obai-stock-synthesis` unless a terminal specialist output contract applies.

When research conflicts with another specialist:

- state the conflict
- identify the evidence type behind each side
- avoid hiding uncertainty

## Boundaries

Do not let research synthesis override:

- strategy terminal artifacts
- prediction-market terminal artifacts
- current market data from a specialist
- current prediction-market data from a specialist
- code-level passthrough or relay validation
