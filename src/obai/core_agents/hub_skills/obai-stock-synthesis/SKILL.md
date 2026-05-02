---
name: obai-stock-synthesis
description: Use when finalizing ordinary stock, ETF, options, company, screener, portfolio, or market research analysis after specialist evidence is available. Do not use for terminal strategy backtest output or terminal prediction-market output.
---

# OBaI Stock Synthesis

## Purpose

Use this skill to turn evidence from non-terminal specialist agents into a concise user-facing answer.

This skill is for synthesis. It is not a routing override and it is not a substitute for specialist tools.

## When to use

Use this skill when the final response depends on one or more evidence-supplier specialists:

- `market_data_analysis`
- `fundamentals_analysis`
- `events_news_analysis`
- `options_analysis`
- `screener_lookup`
- `portfolio_analysis`
- `research_analysis`

Use it only after the needed specialist outputs are available or after deciding that a requested dimension is unavailable.

## When not to use

Do not use this skill when a terminal specialist output controls the final response.

Do not use it to reshape completed or pending output from:

- `strategy_analysis`
- `prediction_market_analysis`

Do not use the regular stock-analysis structure if it would remove, compress, or rename sections from a terminal specialist artifact.

## Synthesis rule

The final answer should preserve the decision-relevant facts from each specialist used.

A fact is decision-relevant when it changes one of these:

- valuation view
- risk view
- catalyst view
- liquidity view
- timing view
- portfolio fit
- tradeoff assessment
- confidence level

If a specialist result does not materially affect the answer, mention that briefly or omit it only when the omission does not hide a relevant risk, conflict, or missing data condition.

## Coverage gate

For each specialist used, include at least one concrete takeaway from its main dimension unless unavailable.

Use these dimensions:

- `market_data_analysis`: price context, trend, returns, range, volume, volatility, or relative movement
- `fundamentals_analysis`: valuation, profitability, growth, leverage, cash flow, estimates, capital returns, or balance-sheet quality
- `events_news_analysis`: catalyst, event timing, sentiment direction, regulatory item, earnings item, macro item, or material uncertainty
- `options_analysis`: implied volatility, liquidity, spread, open interest, skew, Greeks, positioning, or expiration structure
- `screener_lookup`: filters used, matching rationale, ranking rationale, ticker resolution, or candidate exclusion reason
- `portfolio_analysis`: exposure, concentration, correlation, drawdown risk, allocation fit, constraints, or rebalance implication
- `research_analysis`: bull case, bear case, evidence quality, source conflict, risk factor, or unresolved uncertainty

Do not force every possible dimension into the answer. Include the dimensions that are relevant to the user request and the called tools.

## Output structure

Use the smallest structure that fully answers the request.

For a short lookup or narrow answer:

1. Direct answer
2. One caveat if needed

For ordinary analysis:

1. `Answer`
2. `Key Evidence`
3. `Risks or Gaps`
4. `Bottom Line`

For broad analysis:

1. `Summary`
2. `What Supports It`
3. `What Works Against It`
4. `Data Gaps`
5. `Bottom Line`

Do not add every section when the user asked for a narrow answer.

## Numeric claims

Every numeric claim must come from a specialist output or a valid session cache entry.

When using a numeric claim:

- keep the number close to the conclusion it supports
- identify the specialist source when useful for clarity
- avoid unsupported causal language
- distinguish stale, partial, unavailable, or estimated data

## Conflict handling

When specialist outputs conflict:

- state the conflict directly
- identify which evidence supports each side
- avoid forcing a single conclusion unless the evidence supports it
- explain what data would resolve the conflict only when relevant to the user request

## Missing data

If a needed data point is unavailable:

- state the missing data once
- explain how it limits the answer
- avoid filling the gap with model knowledge
- avoid treating missing data as neutral evidence

## Style

Use concise financial analysis language.

Avoid:

- process commentary
- generic disclaimers that do not affect the answer
- repeated conclusions
- unsupported adjectives
- section names that imply more certainty than the evidence supports
