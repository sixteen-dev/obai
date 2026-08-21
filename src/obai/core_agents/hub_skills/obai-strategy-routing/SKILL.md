---
name: obai-strategy-routing
description: Use when the user wants to build, test, backtest, optimize, refine, repair, compare, or follow up on a systematic trading strategy. Covers strategy design, backtesting, optimization, robustness analysis, rule generation, strategy repair/comparison, execution handoff, and strategy job follow-ups. Excludes prediction markets.
---

# OBaI Strategy Routing

## When to use

Use this skill to help the Hub decide whether the request belongs with `strategy_analysis` and to prepare a clean handoff. This skill is not the strategy author — the Hub should not design, backtest, optimize, or judge a systematic strategy by itself when `strategy_analysis` is available.

Use this skill when the primary user intent depends on a trading strategy artifact or backtest artifact. Relevant intent categories:

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

Do not use this skill for:

- ordinary stock analysis without strategy construction or backtesting intent
- prediction-market or Polymarket work, including prediction-market backtesting — which is separate from equity strategy backtesting and routes to `prediction_market_analysis`
- pure company lookup
- pure news summary
- pure fundamentals analysis
- pure options-chain analysis
- portfolio review without a strategy-construction or strategy-overlay intent
- general education that does not request a strategy artifact or backtest artifact

If prediction-market intent and equity-strategy intent both appear in the same request, keep them separate and route each to its specialist.

## Required handoff inputs

Before calling `strategy_analysis`, the Hub is responsible for resolving only two things:

- a concrete tradable universe (tickers), via direct user input or `screener_lookup`
- the user's strategy objective family (e.g., momentum, mean-reversion, breakout), inferred from the request when not explicit

Everything else — entry logic, exit logic, risk controls, capital, sizing, timeframe, benchmark, data assumptions — stays inside the user's quoted request. Do not extract these into separate Hub-authored fields. The Strategy Agent owns implementation details.

Do not invent missing fields. Fabricated assumptions silently shape backtest design — when something is missing, ask one concise clarification or let the Strategy Agent's defaults handle it.

## Universe resolution

Resolve the tradable universe before strategy handoff when needed.

Use direct user-provided symbols when present.

Use `screener_lookup` when the user provides a company name, sector, theme, factor, filter, or non-symbol universe description and a tradable universe is needed.

When calling `screener_lookup`, keep the request simple and stick to the filters the tool actually supports: sector, market cap, share volume, price, beta, limit. Translate the user's descriptive terms into those filter values. Do not invent unsupported filters such as dollar-volume, OR-conditions, or sub-sector qualifiers.

Default to a manageable universe size of 20–30 tickers unless the user explicitly requests more.

If the screener returns no results, ask one concise clarifying question (1–2 sentences) before retrying.

Do not use broad model memory to resolve symbols or candidate universes when a specialist can resolve them.

## Optional pre-strategy context

Gather context from other specialists only when the strategy family materially depends on that domain. Map the strategy family to the right specialist:

- Event-driven, earnings-reaction, catalyst-based → `events_news_analysis`
- Value, quality, fundamental-factor, balance-sheet → `fundamentals_analysis`
- Portfolio overlay, hedging, exposure-aware sizing → `portfolio_analysis`
- Thematic, competitive, structural-research-driven → `research_analysis`
- Options-structure-dependent (e.g., covered call, wheel, vol selling) → `options_analysis`

For purely technical strategy intent (momentum, mean-reversion, breakout, RSI/MACD/SMA-driven), skip pre-strategy context. The Strategy Agent fetches its own technical data.

Engine-vs-family distinction: even though the backtest engine only executes technical signals, fundamental/value/event context still informs how the Strategy Agent shapes the technical rules used as proxies. Gather domain context whenever the strategy family depends on it, regardless of engine capability — the data informs strategy design. Keep the prep concise and avoid long historical data pulls.

Do not add context calls merely to make the Hub look comprehensive.

Do not gather long context that the Strategy Agent can fetch or evaluate directly.

## Handoff arguments

`strategy_analysis` takes three arguments. The runtime assembles the hand-off the Strategy Agent reads, so there is no text template to reproduce and no header to get right.

- `user_request` — the user's wording, preserved verbatim.
- `universe` — the resolved tradable tickers, as a list.
- `context` — Hub-resolved facts, as bullet lines.

Rules for filling them:

- `user_request` is one preserved block. Copy the user's wording. Do not split it into bullets, do not normalize prose into a rules table, and do not rephrase parameters.
- `context` carries only Hub-resolved facts (universe source, inferred objective, gathered specialist findings). It does not restate the user's entry/exit/risk rules, and it never carries `Entry condition`, `Exit condition`, `Risk management`, `Position sizing`, `Order model`, `Session/timezone`, or `Initial capital` bullets — those belong inside `user_request`.
- Useful `context` bullets: the universe source (user or screener), the user objective (marked as inferred when not explicit), the timeframe when the user mentions day trading/scalping/intraday, user-provided constraints, saved user preferences passed through verbatim from the injected USER_PREFERENCES, and summarized findings from other specialists.
- Preserve the user's wording, parameters, and technical details verbatim. Do not generalize specifics, drop technical details, or restructure prose into Hub-authored fields.
- Do not tell `strategy_analysis` to skip backtesting, return design-only output, or short-circuit its workflow. The Strategy Agent's workflow is mandatory; the Hub does not author framings that suggest bypassing it.

Follow-up calls: for status checks or drill-downs on prior output that introduce no new facts (e.g., "is job bt_1a2b3c4d done?", "explain the Sharpe number"), pass the user's wording in `user_request` and leave `universe` and `context` empty. A request naming a stored `bt_<id>` job needs no universe. Reruns, parameter tweaks, or anything that references prior strategy state still need the prior strategy in `context` — the Strategy Agent is stateless and cannot resolve "that" without explicit context.

Intraday and portfolio-mode constraint: when the user mentions day trading, scalping, intraday, or short-term active trading, state the intraday timeframe in `context`. Shared-capital portfolio mode is daily-only — do not author Hub context that implies intraday `allocation_mode: portfolio` is supported. If the user explicitly asks for intraday shared-capital portfolio allocation, preserve that request in `user_request` but do not endorse it in `context`. The Strategy Agent will handle the daily-only constraint.

Specialist-context summarization: when `context` carries findings from other specialists, summarize them into key facts — top candidates with their metrics, outliers, and red flags. Do not paste full raw tool output and do not collapse it into generic descriptions. Context notes are factual data from specialist outputs, not Hub-authored design decisions.

Signal semantics are technical details. Preserve the user's exact condition wording inside `user_request`; do not normalize threshold language into crossover language. Only rewrite to crossover semantics when the user explicitly says "crosses", "crossover", or "from above to below". The same applies in reverse for "rises above" / "above" vs "crosses above".

## Missing-input handling

If the request lacks a concrete tradable universe and the Hub cannot resolve one through an appropriate specialist, ask one concise clarification.

If the request lacks a precise rule set but still clearly asks for strategy design, call `strategy_analysis` with the user's objective and constraints. Do not ask the user about parameters, holding periods, indicator lengths, or risk settings — the Strategy Agent handles defaults.

If the request is ambiguous between ordinary stock analysis and strategy work, use the user's requested deliverable as the deciding factor.

## Output handling

`strategy_analysis` is a terminal author. **Every** non-empty response is relayed by the runtime directly — completed, pending, diagnostic, missing-input, error, and refusal alike. The runtime emits the specialist result and discards any Hub text authored after the tool returns, so never prefix it with routing, tool-error, or retry narration.

### Recognizing output

If the tool result starts with `__TERMINAL_TOOL_OUTPUT__:strategy_analysis:`, the first line is a control marker; the user-facing response is everything after the blank line that follows it.

A **completed** response has these nine sections, in order:

1. Verdict
2. Strategy Summary
3. Backtest Evidence
4. Iteration Summary
5. Engine Compatibility
6. Final Strategy JSON
7. Risk Notes
8. Next Actions
9. Handoff Metadata

A **pending** response has: Status, Job ID, Estimated Time, and Next User Action.

Recognize these shapes to route follow-ups correctly; do not infer completion state from session memory.

### Relay rules

When `strategy_analysis` returns an error, refusal, or missing-input response, the runtime relays it as the terminal output. Anything you author alongside it is discarded, so:

- do not author a substitute strategy, blueprint, or alternative-platform workaround
- do not append Hub-authored portfolio construction, signal definitions, return calculations, or expected-behavior commentary
- do not apply stock-synthesis formatting or coverage gates from other skills
- do not speculate from training data
- do not add a clarifying line — it will not reach the user

The base prompt's evidence-supplier error rule does not apply to strategy errors. There is no "available verified data" to continue with for a terminal author.

When the user follows up on prior strategy output:

- preserve any job identifier and handoff metadata
- route follow-ups back through `strategy_analysis` when the answer depends on strategy state, strategy JSON, backtest results, or job status
- do not reinterpret strategy artifacts in the Hub unless the user only asks for plain-language explanation

When a response mixes strategy output with other specialists, terminal strategy output controls the final structure. Do not append separate Hub synthesis unless the Strategy Agent included it.

## Fallback behavior

If the Hub remains uncertain after loading this skill, prefer the specialist boundary over a Hub-authored strategy answer.

When uncertainty is only about missing details, pass the uncertainty explicitly or ask one concise clarification.
