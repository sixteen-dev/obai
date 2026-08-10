---
name: obai
description: "Use the OBaI CLI to answer financial and stock market questions by running `obai query` commands with `--session` for conversation memory. Trigger this skill whenever the user asks about stock prices, earnings, fundamentals, options, market movers, portfolio analysis, portfolio risk, screening, dividends, SEC filings, insider trades, backtesting strategies, commodity or futures prices, company deep dives, competitive positioning, management quality, product sentiment, thematic research, or any financial market question. The agent should autonomously run `obai query \"<question>\" --json --session <id>`, parse the JSON output, and present the answer — without asking the user to run commands themselves. Always use --session to maintain context across related queries. Also trigger when the user asks to check OBaI server health, run evaluations, or compare stocks."
---

# OBaI CLI — Agent Execution Guide

OBaI is a multi-agent AI system for stock market research. When a user asks a financial question, **run the query yourself** using `obai query` and return the answer. Do not tell the user to run commands — execute them directly.

If the OBaI MCP servers (localhost ports 8001-8010) are registered directly with your harness, prefer the `obai-hub` skill suite over this CLI — it skips the CLI round-trip and uses your own model as the orchestrator.

## How to Answer Financial Questions

1. **Always use `--session`** to maintain conversation context. Generate a descriptive session ID for the topic (e.g., `aapl_research`, `portfolio_review`) or a UUID for general queries.
2. **Reuse the same session** for follow-up questions on the same topic — this gives OBaI memory of prior answers.
3. **Create a new session** when the user switches to a completely different topic.
4. Always use `--json` for structured output you can parse.
5. Present the answer to the user in a clear format.

```bash
# First question — create a session
obai query "What is AAPL trading at?" --json --session aapl_research

# Follow-up — reuse the same session (OBaI remembers the context)
obai query "How does that compare to its 52-week high?" --json --session aapl_research

# Different topic — new session
obai query "Show me top gainers today" --json --session market_scan_$(uuidgen | head -c 8)
```

The system routes queries to the right specialist agents automatically — you don't need to pick which agent to use. Just pass the natural language question.

## Core Command: `obai query`

```bash
obai query "<question>" [OPTIONS]
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--json` | `-j` | `false` | Structured JSON output (always use this) |
| `--session` | `-s` | ephemeral | Named session for multi-turn conversation |
| `--model` | `-m` | configured hub model | Override the orchestrator model for this query only. The default resolves `ORCHESTRATOR_MODEL` → `~/.obai/settings.json` → `gpt-5.6-sol` |

### JSON Output Structure

```json
{
  "query": "What is AAPL trading at?",
  "response": "Apple Inc (AAPL) is currently trading at $255.78...",
  "agents_called": ["market_data"],
  "tool_calls": [{"tool": "get_quote", "agent": "market_data"}],
  "elapsed_ms": 2340,
  "session_id": "cli_a1b2c3d4",
  "timestamp": "2026-03-16T10:30:00Z",
  "model": "gpt-5.6-sol",
  "guardrail_rejected": false,
  "faithfulness": {"numeric_accuracy": 0.95, "faithfulness_pass": true},
  "completeness": {"coverage_score": 0.88, "completeness_pass": true}
}
```

Key fields to use:
- `response` — the answer text to present to the user
- `agents_called` — which specialists were used
- `tool_calls` — specific MCP tools invoked
- `guardrail_rejected` — `true` if query was blocked as non-financial
- `error` — present only on failure, contains `type` and `message`

### Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Parse JSON, present `response` |
| `1` | Guardrail rejection | Query was non-financial, tell the user |
| `3` | Infrastructure error | Check `obai status --json`, report which servers are down |

### Reading from stdin

```bash
echo "What are the top gainers today?" | obai query - --json
```

## Session Management

Sessions persist conversation memory to `~/.obai/sessions.db`. The agent must manage sessions intentionally:

**Same topic → same session.** If the user asks follow-ups or related questions, reuse the session so OBaI has context from prior answers.

**New topic → new session.** When the conversation shifts to an unrelated subject, create a fresh session ID to avoid polluting context.

**Session ID naming:** Use descriptive names tied to the research topic. This makes it easy to resume later if the user comes back to the same subject.

```bash
# Deep-dive on a single stock — one session throughout
obai query "Analyze AAPL earnings trend" --json --session aapl_deep_dive
obai query "Now compare with MSFT" --json --session aapl_deep_dive
obai query "Which has better growth prospects?" --json --session aapl_deep_dive

# User switches to options analysis — new session
obai query "Show me SPY options chain for next Friday" --json --session spy_options

# One-off question with no follow-up expected — use a UUID
obai query "Is the market open today?" --json --session q_$(date +%s)
```

**Never omit `--session`.** Without it, each query gets a throwaway ephemeral session and loses all context. Always pass a session ID.

## Server Health Check

Before querying, verify servers are running:

```bash
obai status --json
```

Returns:
```json
{
  "servers": [
    {"name": "Fundamentals", "url": "http://localhost:8001/mcp", "status": "ok", "latency_ms": 125},
    {"name": "Market Data", "url": "http://localhost:8002/mcp", "status": "ok", "latency_ms": 98}
  ],
  "all_healthy": true
}
```

If `all_healthy` is `false`, report which servers are down. Exit code `3` means at least one server is unhealthy.

## What Each Agent Handles

Route awareness helps you understand the response, but you don't need to pick agents — the Hub does that automatically.

| Agent | Handles |
|-------|---------|
| **Fundamentals** | Financials, ratios, SEC filings, insider trades, revenue segments |
| **Market Data** | Prices, quotes, candles, movers, market status, commodity/futures prices |
| **Events/News** | Earnings calendar, dividends, news search |
| **Options** | Options chains, Greeks, IV, open interest, pricing analytics, scenario analysis, position risk profiles |
| **Screening** | Stock screening with filters |
| **Portfolio** | Portfolio analysis, ETF holdings, risk metrics, sector/asset class allocation, treasury rates |
| **Strategy** | Backtest design and iteration (daily and intraday), walk-forward robustness validation, shared-capital portfolio backtesting |
| **Research** | Company deep dives, business model analysis, management quality, product sentiment, competitive dynamics, industry structure, thematic research via Exa semantic web search |
| **Prediction Markets** | Polymarket discovery, event odds, executable YES/NO pricing and depth, trade flow, holder/trader analysis, trade memos, calibration and rule backtests |
| **Crypto** | Coinbase spot data (products, OHLCV, order books, quotes), crypto strategy backtests, paper-ledger strategy artifacts |

Multi-domain queries (e.g., "Compare AAPL earnings with options flow") automatically dispatch to multiple agents in parallel.

## Evaluation

Run evaluations to assess agent quality:

```bash
# From src/obai/

# Single query evaluation with scoring
uv run python -m evaluation evaluate "What is AAPL trading at?" --json

# Full test suite
uv run python -m evaluation evaluate --suite --report results.md

# Fast mode (skip LLM judge)
uv run python -m evaluation evaluate --suite --no-builtin

# Filter by category
uv run python -m evaluation evaluate --suite --category A
```

| Flag | Short | Description |
|------|-------|-------------|
| `--suite` | `-s` | Run full test suite |
| `--model` | `-m` | Model label for the trace (default: `ORCHESTRATOR_MODEL`) |
| `--judge` | `-j` | Model for LLM scorers (default: `anthropic/claude-sonnet-4-5-20250929`) |
| `--no-builtin` | | Skip Opik built-in scorers (faster) |
| `--category` | `-c` | Filter: A-G (A=single-agent, B=multi-agent, C=guardrails, D=errors, E=strategy/backtest, G=new capabilities) |
| `--file` | `-f` | Custom YAML test file |
| `--report` | `-r` | Export markdown report |
| `--export` | `-e` | Export raw JSON results |

## Error Handling

**Guardrail rejection** (`exit 1`): The query was blocked as non-financial. The JSON will have `"guardrail_rejected": true` with an error message explaining why.

**Auth error** (`exit 3`): Usually means `OPENAI_API_KEY` is not set. Check the environment.

**MCP connection error** (`exit 3`): One or more servers are down. Run `obai status --json` to diagnose, then suggest `./setup.sh` or `docker compose up -d`.

## Configuration (Environment Variables)

| Variable | Default | What it does |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | OpenAI API key for all agents |
| `ORCHESTRATOR_MODEL` | `gpt-5.6-sol` | Central Hub model. Wins over `~/.obai/settings.json` (see below) |
| `ORCHESTRATOR_REASONING_EFFORT` | `medium` | Hub reasoning effort: `none`, `low`, `medium`, `high`, `xhigh`, `max`. Wins over `~/.obai/settings.json` |
| `SPECIALIST_MODEL` | `gpt-5.6-luna` | Default specialist model |
| `STRATEGY_MODEL` | `gpt-5.6-terra` | Strategy agent model |
| `EXA_API_KEY` | optional | Exa API key for research server |
| `ENABLE_GUARDRAILS` | `true` | Block non-financial queries |
| `RESEARCH_MODEL` | `gpt-5.6-luna` | Research agent model |
| `ENABLE_INLINE_SCORING` | `true` | Score every query for faithfulness |
| `MCP_TIMEOUT` | `30` | Request timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

Specialist effort tiers use the same values via `SPECIALIST_REASONING_EFFORT`, `STRATEGY_REASONING_EFFORT`, `CRYPTO_REASONING_EFFORT`, and `PREDICTION_MARKETS_REASONING_EFFORT`. `minimal` is not a valid tier — every `gpt-5.6` model rejects it.

## Hub Settings File (`~/.obai/settings.json`)

The hub model and reasoning effort are user-settable and persist across sessions:

```json
{
  "hub_model": "gpt-5.6-sol",
  "hub_reasoning_effort": "medium"
}
```

`hub_model` is `gpt-5.6-sol` or `gpt-5.6-terra`. `hub_reasoning_effort` is `medium`, `high`, `xhigh`, or `max`. Specialist models and efforts are not settable here.

```bash
obai config set-model gpt-5.6-terra   # write hub model
obai config set-effort high           # write hub reasoning effort
obai config show                      # current values and where they came from
```

The web UI settings modal writes the same file.

Three things to tell a user who asks why a change did not take effect:

1. **Env wins.** Resolution is `ORCHESTRATOR_MODEL` / `ORCHESTRATOR_REASONING_EFFORT` → `~/.obai/settings.json` → shipped default. An export in the user's shell, or a leftover line in `~/.obai/.env` (the CLI loads it into the environment at startup), silently outranks the file. Check with `obai config show` before assuming the write failed.
2. **A restart is required.** Nothing hot-swaps a running agent. `obai restart` after any change; `--model` on `obai query` is the only per-query override.
3. **A missing file is normal.** Absent or empty means shipped defaults, on a fresh install and after an upgrade alike. Only a file that exists and does not parse or validate is an error — it is reported, never silently ignored, so fix or delete it.
