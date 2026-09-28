---
name: obai-options-strategy-routing
description: Use when the user wants historical performance of an options strategy, validation of an options strategy document or rules, design of managed options rules (verticals, condors, straddles, strangles, covered calls, cash-secured puts, wheels, rolls), or asks what options-strategy backtesting OBaI supports; excludes current options chains, Greeks, IV and scenario math on live contracts, and equity or ETF share strategy backtests.
---

# OBaI Options Strategy Routing

## When to use

Use this skill to decide whether a request belongs with `options_strategy_analysis` and to prepare its handoff. This skill is not the options-strategy author: the Hub never validates an options strategy, judges its merit, or reports its historical performance by itself.

Route to `options_strategy_analysis` for:

- historical performance or a backtest of an options strategy
- validation of options strategy rules or a strategy document
- design of managed options rules: verticals, iron condors, straddles, strangles, single long options, and covered call, cash-secured put, wheel, or roll requests
- questions about what options-strategy backtesting OBaI supports

Route there even when the requested structure, product, or window is unsupported. The specialist validates the request as stated and answers with the supported scope; the Hub does not pre-judge support.

Do not route here for:

- current options chains, Greeks, implied volatility, open interest, NBBO quotes, contract snapshots, position risk, or scenario and payoff math on current contracts — these go to `options_analysis`
- equity and ETF share strategies, intraday or daily OHLCV backtests, walk-forward analysis, and `bt_<id>` follow-ups — these go to `strategy_analysis`
- Polymarket and prediction-market setups — `prediction_market_analysis`
- Coinbase spot crypto — `crypto_analysis`

When a request pairs a current options opportunity with a historical test, route both, in separate calls: `options_analysis` for the current evidence and `options_strategy_analysis` for the test. Current evidence keeps its date and travels only as dated `context`; it is never historical state for the test.

## Handoff arguments

`options_strategy_analysis` takes five arguments. The runtime renders the blocks the specialist reads, so there is no text template to reproduce.

- `user_request` — the user's wording, verbatim. Never rewrite, summarize, or translate the user's structure, roots, legs, exits, or account into other terms.
- `underlyings` — the resolved underlying symbols the user named. Leave it empty for explain, status, and capability questions that name none.
- `context` — Hub-resolved facts only, each with its date. Never the user's mechanics, which stay in `user_request`, and never a figure the Hub supplied from memory.
- `prior_run_ids` — run identifiers the user named, exactly as written. Leave it empty when the user named none.
- `requested_action` — `build` to compile and validate a strategy, `backtest` for historical performance, `compare` for runs the user named, `status` for a job or run the user named, `explain` for a capability or rules question.

Do not invent missing mechanics. The specialist reports what is missing as an input the user must supply.

## Output handling

`options_strategy_analysis` is a terminal author. Every response — validated, rejected, unavailable, capability, or failed, and an empty specialist answer as failed — carries the marker `__TERMINAL_TOOL_OUTPUT__:options_strategy_analysis:` on its first line, and the runtime relays everything after the blank line that follows it verbatim. Anything the Hub authors after the tool returns is discarded, so never prefix it with routing, retry, or error narration.

A result starting `OPTIONS_STRATEGY_HANDOFF_ERROR:` without the marker is a pre-flight control signal, not an answer. Never relay it. Call `options_strategy_analysis` again with `user_request` set to the user's original wording verbatim and resolved facts in `context`.

## Unavailable capability

Historical options backtesting is unavailable in this deployment. The specialist reports that with the service's typed reason. The Hub never substitutes a performance, drawdown, win-rate, or return figure, a proxy equity backtest through `strategy_analysis`, or current-market analysis through `options_analysis` for the unavailable result.

If `options_strategy_analysis` is not among the available tools, say that options-strategy backtesting is unavailable right now, and that either this optional component is not enabled in this installation (it is enabled at install or start time) or its server is not running. Do not route the request to `strategy_analysis` or `options_analysis`, and do not answer from training data.

## Follow-ups

Route follow-ups on prior options-strategy output back through `options_strategy_analysis`, with the user's wording in `user_request` and any identifiers the user named in `prior_run_ids`. The Hub does not reinterpret a validation result or a capability answer from session memory.

## Fallback behavior

If the Hub remains uncertain after loading this skill, prefer the specialist boundary over a Hub-authored answer.
