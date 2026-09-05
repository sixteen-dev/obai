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

Do not use this skill — route to the listed specialist instead — for:

- live price, quote, chart, technicals → `market_data_analysis`
- ticker resolution or universe construction → `screener_lookup`
- recent headlines, event recaps, earnings, dividends, catalysts → `events_news_analysis`
- structured financial data, SEC filings, insider activity, segment breakdowns, valuation metrics, ratios → `fundamentals_analysis`
- options chains, Greeks, implied volatility → `options_analysis`
- portfolio math, allocations, exposure → `portfolio_analysis`
- terminal strategy output or strategy backtesting → `strategy_analysis`
- terminal prediction-market output or prediction-market backtesting → `prediction_market_analysis`

Research is not a substitute for specialized current data when the user asks for current state.

When a question needs both structured data and qualitative synthesis, call `research_analysis` alongside the relevant specialist instead of choosing one or the other.

When structured-data specialists cannot answer a qualitative or thematic question, route to `research_analysis` rather than answering from model memory. Web synthesis beats training knowledge.

## Required handoff inputs

`research_analysis` runs semantic web search, which needs a company name (not a ticker symbol) to retrieve relevant sources.

Before calling `research_analysis`:

- if the user provided a company name, use it, and do not spend a `screener_lookup` resolving it to a ticker
- if the user provided only a ticker, resolve ticker → company name via `screener_lookup` first
- if the request is sector- or theme-level with no specific company, pass the sector or theme directly

Do not pass a bare ticker symbol when the search target is a company name — the semantic search will return weak results.

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

## Repeat calls

Call `research_analysis` once for the question, then at most one follow-up naming what the first pass left unresolved. If the gap survives that follow-up, report the gap; do not re-ask the same subject from another angle.

Never fan out concurrent `research_analysis` calls over restatements of one question. They return overlapping sources while multiplying cost and latency, and the user waits for the slowest.

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
