---
name: obai-crypto-routing
description: Use when the user asks about Coinbase spot crypto products, crypto OHLCV, crypto order books, latest crypto trades or bid/ask, Coinbase spot strategy backtests, crypto strategy artifacts, or follow-ups on prior crypto backtests or artifacts. Excludes equities, options, Polymarket, DeFi, on-chain, and derivatives.
---

# OBaI Crypto Routing

## When to use

Use this skill before `crypto_analysis` when the request depends on Coinbase spot crypto market data, Coinbase spot backtesting, internal Coinbase paper-ledger artifacts, or a follow-up on prior crypto output.

Route to `crypto_analysis` for:
- Coinbase-tradable spot product lookup
- OHLCV, order book, latest trade, best bid/ask
- Coinbase spot strategy design or backtesting
- crypto strategy trade-log, job-status, validation, or artifact export

Do not route here for:
- equities or ETFs
- equity strategy backtesting
- options
- Polymarket or prediction markets
- DeFi TVL, yield pools, stablecoin risk, wallets, on-chain tracing
- perpetuals, funding rates, open interest, basis, liquidations
- live order placement

If crypto and equity intent are mixed, route each to its specialist in separate turns. Crypto terminal output controls final rendering once `crypto_analysis` fires.

## Handoff rules

Preserve the user's request verbatim. Pass only factual Hub-resolved context:
- Coinbase product ID, if resolved
- user-provided timeframe and date range
- explicit strategy family or rules
- explicit risk, fee, slippage, or paper-artifact constraints
- prior crypto job ID, artifact fingerprint, or product ID from tool output

Do not invent missing strategy rules, dates, fees, risk caps, or unsupported provider data. Do not translate Coinbase spot intent into another venue.

For ambiguous symbols or asset names, either pass the ambiguity to `crypto_analysis` or call it with the user's original query so it can use `crypto_resolve_symbol`.

## Output handling

`crypto_analysis` is a terminal author **when its result carries the marker** `__TERMINAL_TOOL_OUTPUT__:crypto_analysis:`. Only then does the runtime emit the specialist output directly and discard Hub-authored text. A result without that marker is not terminal, and what you write is what the user reads.

For a marked result, treat the first line as a control marker; everything after the first blank line is the user-facing output. Relay marked completed, blocked, error, missing-input, job-status, and artifact-validation outputs unchanged. Do not summarize, rename sections, add deployment claims, or convert research-only data into execution-grade language.

## Scope rejections

A result starting `MISSING_CRYPTO_INPUTS:` without the terminal marker is a pre-flight control signal, not an answer. Never relay it, and never repeat the same call.

Answer it yourself: name every capability the request asks for that Coinbase spot v1 does not cover, not only the one the signal reports, say that nothing was retrieved for any of them, and point to the supported Coinbase spot alternatives. Invent no values.

## Follow-ups

Preserve prior crypto identifiers exactly:
1. `job_id`
2. artifact `fingerprint`
3. `product_id`
4. timeframe and date range

If a follow-up asks whether an artifact can be exported, route back to `crypto_analysis`; the Hub must not decide export eligibility from memory.

## Fallback behavior

If `crypto_analysis` is unavailable, say the crypto server is unavailable and do not substitute equity, prediction-market, or training-data analysis for current crypto market state.
