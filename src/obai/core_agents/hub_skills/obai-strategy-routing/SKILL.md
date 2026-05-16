---
name: obai-strategy-routing
description: Use when the user wants to build, test, backtest, optimize, refine, repair, compare, or follow up on a systematic trading strategy. Covers strategy design, backtesting, optimization, robustness analysis, corpus-grounded named strategy or market-concept lookup before strategy handoff, rule generation, strategy repair/comparison, execution handoff, and strategy job follow-ups. Excludes prediction markets.
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

Before calling `strategy_analysis`, the Hub is responsible for resolving three things — in this order:

1. **Vocabulary gate.** Call `knowledge_base_lookup` before `strategy_analysis`. This is a blocking precondition. Skip only when the user's text passes the self-contained rule test in `## Corpus consultation` — every executable ingredient literally written in the user's wording.
2. A concrete tradable universe (tickers), via direct user input or `screener_lookup`.
3. The user's strategy objective family, inferred from the request when not explicit.

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
- Options-structure-dependent strategy family → `options_analysis`

For purely technical indicator- or rule-driven strategy intent, skip pre-strategy context. The Strategy Agent fetches its own technical data.

Engine-vs-family distinction: even though the backtest engine only executes technical signals, fundamental/value/event context still informs how the Strategy Agent shapes the technical rules used as proxies. Gather domain context whenever the strategy family depends on it, regardless of engine capability — the data informs strategy design. Keep the prep concise and avoid long historical data pulls.

Do not add context calls merely to make the Hub look comprehensive.

Do not gather long context that the Strategy Agent can fetch or evaluate directly.

## Corpus consultation

The corpus is the authority for named strategies and market concepts. Where it has an entry, that entry carries the source-defined signal, engine_fit posture, and approximation notes — substitutes built from training memory drift from the source the moment a corpus-covered name appears.

**Blocking rule.** When the vocabulary gate fires and `knowledge_base_lookup` is available, the call is a precondition to `strategy_analysis`. Do not hand off until one of these is true:

- the lookup matched and a compact seed is in `Strategy context`;
- the lookup returned no match and a no-match note is in `Strategy context`;
- the tool is unsurfaced and an unavailable-tool note is in `Strategy context`.

Optional context specialists do not substitute for this lookup. When both are useful, corpus first.

**Skip test.** Skip only when every executable ingredient is in the user's text: tradable instrument or universe, signal inputs or indicators, numeric parameters or thresholds, entry condition, exit or holding condition. Any ingredient implied by a named term — not literally written — means call the lookup. Familiarity, obviousness, or training-memory confidence is not a reason to skip. A definitional question paired with a strategy ask in the same turn is never self-contained.

**Lookup input.** Pass the shortest salient strategy or concept phrase from the user's wording. No dates, tickers, constraints, or output-format requests unless they are part of the named term itself. Preserving the user query verbatim inside `User request` does not stand in for the lookup — the strategy specialist cannot resolve corpus vocabulary; only the librarian can.

**One attempt per concept.** One lookup per named concept or strategy. If the response surfaces `related_strategies` and you choose to follow up, make a second lookup with the strategy id. Never ask the librarian to chain.

**Retry hook.** A degraded `strategy_analysis` result — zero trades, "could not implement", a reject without concrete rules, or you re-prompting with a fresh framing — means call `knowledge_base_lookup` before the next attempt. The loop is usually the corpus answering a question you guessed at.

**Seed propagation.** After a matched lookup, add a compact note to the existing `Context` bullet in `Strategy context`: entry id plus only the guidance that changes implementation (engine_fit, signal definition, universe constraint, approximation note). No new sections or fields. After a no-match or unavailable result, write the brief note in the same bullet and proceed.

**Coverage realism.** The corpus is partial — many valid queries return no match. A null result is informative; the engine handles strategies the corpus does not cover. Missing entry ≠ invalid strategy.

## Handoff format

Use this exact two-block structure when calling `strategy_analysis`:

```
User request: [original user request, preserved verbatim]
Strategy context:
- Universe: [tickers] (source: user or screener)
- User objective: [strategy objective family — mark as inferred when not explicit]
- Timeframe: [requested timeframe — only when the user mentions day trading, scalping, or intraday]
- Constraints: [user-provided constraints only — leave blank if none]
- Preferences: [saved user preferences — pass through verbatim from the injected USER_PREFERENCES; omit the bullet only if no preferences are loaded]
- Context: [summarized key findings from specialist outputs, if any]
```

These are the ONLY two top-level headers allowed. Do not invent additional sections — not for preferences, constraints, routing, or design notes. Any other content goes inside one of the two existing blocks (or it does not belong in the handoff).

Follow-up shorthand: for status checks or drill-downs on prior output that introduce no new facts, you may emit the `Strategy context:` header with no bullets beneath it. The `User request:` block must still carry the user's wording verbatim. Reruns, parameter tweaks, or anything that references prior strategy state need full bullets including the prior strategy in `Context:` — the Strategy Agent is stateless and cannot resolve shorthand without explicit context.

Rules for filling the template:

- Both `User request:` and `Strategy context:` headers appear on every call. The runtime gate rejects calls missing either header.
- `User request` is one preserved block. Copy the user's wording. Do not split it into bullets, do not normalize prose into a rules table, and do not rephrase parameters.
- `Strategy context` carries only Hub-resolved facts (universe source, inferred objective, gathered specialist findings). It does not restate the user's entry/exit/risk rules.
- Do not add fields that are not in the template. In particular, do not invent `Entry condition`, `Exit condition`, `Risk management`, `Position sizing`, `Order model`, `Session/timezone`, or `Initial capital` bullets — those belong inside the preserved `User request` block.
- Preserve the user's wording, parameters, and technical details verbatim. Do not generalize specifics, drop technical details, or restructure prose into Hub-authored fields. The Strategy Agent owns implementation details.
- Do not tell `strategy_analysis` to skip backtesting, return design-only output, or short-circuit its workflow. The Strategy Agent's workflow is mandatory; the Hub does not author framings that suggest bypassing it.

Intraday and portfolio-mode constraint: when the user mentions day trading, scalping, intraday, or short-term active trading, fill `Timeframe` with the appropriate intraday value. Shared-capital portfolio mode is daily-only — do not author Hub context that implies intraday `allocation_mode: portfolio` is supported. If the user explicitly asks for intraday shared-capital portfolio allocation, preserve that request inside the `User request` block but do not endorse it in `Strategy context`. The Strategy Agent will handle the daily-only constraint.

Specialist-context summarization: when the `Context` line carries findings from other specialists, summarize them into key facts — top candidates with their metrics, outliers, and red flags. Do not paste full raw tool output and do not collapse it into generic descriptions. Context notes are factual data from specialist outputs, not Hub-authored design decisions.

Signal semantics are technical details. Preserve the user's exact condition wording inside `User request`; do not normalize threshold language into crossover language. Only rewrite to crossover semantics when the user explicitly says "crosses", "crossover", or "from above to below". The same applies in reverse for "rises above" / "above" vs "crosses above".

## Missing-input handling

If the request lacks a concrete tradable universe and the Hub cannot resolve one through an appropriate specialist, ask one concise clarification.

If the request lacks a precise rule set but still clearly asks for strategy design, call `strategy_analysis` with the user's objective and constraints. Do not ask the user about parameters, holding periods, indicator lengths, or risk settings — the Strategy Agent handles defaults.

If the request is ambiguous between ordinary stock analysis and strategy work, use the user's requested deliverable as the deciding factor.

## Output handling

`strategy_analysis` is a terminal author. The Hub relays its output.

### Recognizing terminal output

If the tool result starts with the literal prefix `__TERMINAL_TOOL_OUTPUT__:strategy_analysis:`, treat the first line as a control marker. Strip that line and the blank line after it — never relay either to the user. Everything that follows is the verbatim user-facing response; return it unchanged.

A **completed** strategy response contains these nine sections, in order:

1. Verdict
2. Strategy Summary
3. Backtest Evidence
4. Iteration Summary
5. Engine Compatibility
6. Final Strategy JSON
7. Risk Notes
8. Next Actions
9. Handoff Metadata

When you see this nine-section shape, it is a finished deliverable — relay verbatim.

A **pending** strategy response contains: Status, Job ID, Estimated Time, and Next User Action. Relay it verbatim.

### Relay rules

When `strategy_analysis` returns completed or pending output:

- return the full response unchanged
- preserve all sections, tables, JSON, metadata, risk notes, and job identifiers
- preserve any pending status language, job identifier, and next-action instruction exactly
- do not summarize, restructure, or rename sections
- do not append a separate Hub conclusion
- do not apply stock synthesis formatting or coverage gates from other skills
- do not infer completion state from session memory

These relay rules override later analysis-formatting and output-style instructions from any other skill or prompt.

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
