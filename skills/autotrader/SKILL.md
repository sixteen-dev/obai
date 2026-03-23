---
name: autotrader
description: "Autonomous paper trading bot that manages a stock portfolio on Alpaca's paper trading platform. Use this skill when the user asks to: run the daily trading routine, check the portfolio or account status, execute or close trades, evaluate strategy signals, screen stocks, get technical analysis, review trading performance, deploy a new strategy, run portfolio risk analysis, or validate strategy robustness. Trigger on mentions of paper trading, Alpaca, autotrader, buy/sell stocks, portfolio check, market hours, risk limits, strategy signals, or daily routine."
---

# AutoTrader Skill — OpenClaw Execution Guide

You have two capabilities: **analysis** (via OBaI CLI) and **execution** (via Python scripts). Analysis is read-only. Execution mutates your portfolio. Never mix them up.

All commands run from `src/skills/autotrader/`.

---

## Analysis: OBaI CLI

OBaI is a multi-agent AI system for stock market research. Use `obai query` to ask any financial question — it routes to the right specialist automatically.

```bash
obai query "{your question}" --json --session autotrader_{date}
```

**Rules:**
- Always use `--json` for parseable output
- Always use `--session autotrader_{date}` (e.g., `autotrader_2026-03-21`) for context across queries
- The `response` field in JSON output contains the analysis text
- Check `guardrail_rejected` and `error` fields for failures
- OBaI is **read-only** — never ask it to place trades or manage positions

**JSON output structure:**
```json
{
  "query": "...",
  "response": "Apple Inc (AAPL) is currently trading at...",
  "agents_called": ["market_data", "fundamentals"],
  "elapsed_ms": 2340,
  "guardrail_rejected": false
}
```

The `response` field format depends on the query type:
- **General queries** (analysis, news, screening, technicals, commodity prices): prose text or numbers
- **Portfolio risk/allocation queries**: structured risk metrics (Sharpe, Sortino, beta, drawdown, VaR) or sector/asset class allocation breakdown
- **Options analytics queries**: Greeks computation, scenario P&L grids, position risk profiles with breakevens
- **Strategy/backtesting queries**: multi-section document where the "Final Strategy JSON" section contains a raw JSON object following the backtest-server's strategy schema (`name`, `universe`, `indicators`, `entry_rules`, `exit_rules`, `position_sizing`, `risk_management`). Extract and parse this JSON when deploying a strategy to `memory/strategies/`. The engine supports daily and intraday timeframes (5min, 15min, 1hour), shared-capital portfolio mode (daily only), and walk-forward validation for robustness testing. Ask OBaI for valid indicator types, operators, or timeframe options if needed — it knows the full schema.

**Exit codes:** 0 = success, 1 = guardrail rejection, 3 = infrastructure error.

**Health check:**
```bash
obai status --json
```

---

## Execution: Trading Scripts

All scripts call alpaca-py directly. They output JSON to stdout. Non-zero exit = failure (check stderr).

### Check Market Hours

```bash
uv run python -m scripts.market_hours
```
```json
{"is_open": true, "timestamp": "...", "next_open": "...", "next_close": "..."}
```

Always check this first. If market is closed, skip trading steps.

### Get Portfolio (Account + Positions + Risk)

```bash
uv run python -m scripts.get_portfolio
```
```json
{
  "account": {"equity": 98450.0, "buying_power": 23200.0, "cash": 23200.0, "daily_pnl": -150.0, ...},
  "positions": [
    {"symbol": "AAPL", "qty": 25.0, "avg_entry_price": 195.20, "current_price": 205.80, "unrealized_pl": 265.0, "unrealized_pl_pct": 5.42, ...}
  ],
  "position_count": 3,
  "risk": {"daily_trades_used": 2, "daily_trades_limit": 20, "daily_pnl_pct": -0.15, "current_exposure_pct": 76.5, ...}
}
```

### Execute a Trade

```bash
# Market order
uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 --order-type market

# Limit order
uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 --order-type limit --limit-price 195.00
```
```json
{"order_id": "abc-123", "symbol": "AAPL", "side": "buy", "qty": 10.0, "status": "accepted", ...}
```

**Important:** For market buy orders on new positions (stocks you don't already hold), always pass `--limit-price` with the approximate current price. The risk checker needs a price estimate to calculate position size. For existing positions, it uses the current price automatically.

**Risk check is automatic.** If the order violates risk limits, you get:
```json
{"error": "Risk rejected: Position would be 15.2% of equity (max 10%)", "allowed": false}
```

### Close a Position

```bash
uv run python -m scripts.close_position --symbol AAPL
```
```json
{"order_id": "def-456", "symbol": "AAPL", "side": "sell", "qty": 25.0, "status": "accepted", ...}
```

---

## Strategy Signal Evaluation

You evaluate strategy signals yourself — no script needed. The process:

1. Read strategy rules from `memory/strategies/*.json` (entry/exit conditions, indicators, universe)
2. Ask OBaI for the current values of the indicators defined in the strategy for its universe symbols — request just the numbers
3. Compare each indicator value against the strategy's entry/exit rule thresholds and operators in your own reasoning
4. For each symbol, determine: entry signal, exit signal, or no signal

Ask OBaI for exactly the indicators your strategy defines — don't request indicators the strategy doesn't use. If you need to know what indicators or operators the engine supports, ask OBaI directly — it can list them.

You can also ask OBaI to analyze your current portfolio's risk or sector concentration at any time — useful before making entry decisions or during the daily journal.

---

## Environment Variables Required

```bash
ALPACA_API_KEY=your_key       # Required for all trading scripts
ALPACA_SECRET_KEY=your_secret # Required for all trading scripts
OPENAI_API_KEY=sk-proj-...    # Required for OBaI analysis
```

Optional risk limit overrides (defaults in parentheses):
```bash
MAX_POSITION_PCT=10.0         # Max single position as % of equity (10%)
MAX_DAILY_TRADES=20           # Max trades per day (20)
MAX_DAILY_LOSS_PCT=3.0        # Stop trading if daily loss exceeds (3%)
MAX_EXPOSURE_PCT=90.0         # Max invested capital as % of equity (90%)
```
