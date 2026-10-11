---
name: autotrader
description: "Set up or run authorized Alpaca paper-trading routines, account/order reconciliation, strategy signal checks, and trading-job reviews using standalone scripts and OBaI MCP skills. Use for Alpaca paper accounts or AutoTrader automation. General financial research belongs to obai-hub; reading this skill does not authorize orders or live trading."
---

# AutoTrader Skill — OpenClaw Execution Guide

Use **analysis** via `obai-hub` and its direct MCP specialist skills, and **paper execution** via the bundled Python scripts. Follow the user's authorized scope: research alone does not authorize orders or jobs. Once paper automation is authorized with a saved execution plan, routine in-scope paper orders need no repeated approval. The client enforces `paper=True`.

All commands run from this installed skill's directory (`skills/autotrader/` in the checkout). Read [context.md](context.md) for a trading run. For installation/job setup, read [../obai-hub/setup.md](../obai-hub/setup.md) and [setup-prompt.md](setup-prompt.md).

---

## Analysis: Direct MCP

Use your own model as the hub. Read `obai-hub`, then only the specialists required for the task. Call their registered MCP tools; no `obai` binary or OBaI model runtime is required.

- Use `obai-market-data` for verified prices/indicator snapshots, `obai-events-news` for catalysts, and `obai-strategy` for tested strategy JSON. Other domains follow the hub routing table.
- Keep the exact tested JSON, verdict, job IDs, data/execution assumptions and warnings together in versioned strategy state. Pending, rejected or `needs_more_research` candidates cannot place orders.
- Backtest indicator support is broader than the live technical endpoint. A backtest verdict alone does not establish execution compatibility; apply the signal/deployment gate below.
- Inspect each tool's structured result and errors. Probe MCP connectivity and required tools from the scheduled runtime; a saved server URL is not a health check.
- Persist state and provenance across isolated jobs. Do not rely on this conversation or reuse stale market/account data.

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

Check before any new order. A closed market skips submissions but still permits reconciliation and reporting. Use the clock/calendar for holidays and early closes.

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
  "open_orders": [
    {"order_id": "abc-123", "client_order_id": "obai-...", "symbol": "MSFT", "side": "sell", "qty": 40.0, "filled_qty": 0.0, "status": "new", ...}
  ],
  "recent_orders": [...],
  "open_orders_may_be_truncated": false,
  "recent_orders_may_be_truncated": false,
  "risk": {"daily_trades_used": 2, "daily_trades_limit": 20, "daily_pnl_pct": -0.15, "current_exposure_pct": 76.5, "max_positions": 10, ...}
}
```

Either truncation flag means the order list is one page, not a complete ledger;
recover older orders by client order ID.

### Execute a Trade

```bash
# Market order: use a fresh verified price estimate and a saved stable intent ID
uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 --order-type market --limit-price 200 --client-order-id "$INTENT_ID"

# Limit order
uv run python -m scripts.execute_trade --symbol AAPL --side buy --qty 10 --order-type limit --limit-price 195.00 --client-order-id "$INTENT_ID"
```
```json
{"order_id": "abc-123", "symbol": "AAPL", "side": "buy", "qty": 10.0, "status": "accepted", ...}
```

**Important:** For market buy orders on new positions (stocks you don't already hold), always pass `--limit-price` with the approximate current price. The risk checker needs a price estimate to calculate position size. For existing positions, it uses the current price automatically.

Pass `--reduce-only` on every strategy exit or partial reduction: it rejects
any order that would grow or reverse the position, so a stale quantity cannot
flip a long into a short. `close_position` applies it implicitly.

For market orders this is only a risk estimate, not a price cap. Scheduled orders must supply `--client-order-id`: derive a stable ID of at most 48 characters from account, strategy version, symbol, completed signal bar and action (for example `obai-` plus a 40-character hash). Save the exact request before running it. One-off commands without an ID generate one and return it; repeating such a command is a new intent.

Both submit and close scripts share a process lock and durable intents under `AUTOTRADER_STATE_DIR` (default `memory/execution/`). All jobs for an account must use the same directory on one host. An uncertain submission is reconciled by client ID; an unresolved intent is blocked even when a later lookup returns 404. Do not delete its state or change IDs to force a retry.

`accepted` is not a fill. Reconcile `open_orders`, `recent_orders`, `filled_qty` and `filled_avg_price` from `get_portfolio` before changing holdings/P&L. Recent history is bounded; recover older orders by client ID when needed. Pending same-symbol orders block further submissions; other pending orders reserve remaining notional conservatively. New exposure requires finite balances/prices and available cash, subject to position, exposure and daily limits. Pure reductions remain eligible after entry limits, with duplicate-order checks intact.

**Risk check is automatic.** If the order violates risk limits, you get:
```json
{"error": "Risk rejected: Position would be 15.2% of equity (max 10%)", "submission_state": "not_submitted", "client_order_id": "..."}
```

Both order scripts print this payload on **stdout** and exit 1, so a caller
reading one stream never misses `submission_state`. `not_submitted` means no
order exists, including when the broker itself refused the order. `unknown`
means an order may exist and needs reconciliation by its client order ID — it is
not a rejection and not an unfilled order.

### Close a Position

```bash
uv run python -m scripts.close_position --symbol AAPL --client-order-id "$EXIT_INTENT_ID"
```
```json
{"order_id": "def-456", "symbol": "AAPL", "side": "sell", "qty": 25.0, "status": "accepted", ...}
```

---

## Strategy Signal Evaluation

Read the frozen strategy, then obtain dated previous/current completed daily-bar values via `obai-market-data`. The live endpoint supports RSI, SMA, EMA, WMA, DEMA, TEMA and ADX; it does not expose the backtest engine's full catalog or intraday indicator timeframes. Request sufficient history to identify both bars and verify warm-up, source/price basis and parameter equivalence.

Use `scripts.evaluate_signals` for deterministic threshold/crossover comparisons; see [signal-input.md](signal-input.md) for its input contract. Missing/stale/non-finite values, incomplete bars, and unsupported indicators/operands block evaluation. Do not infer a crossover from one current value or calculate signals from prose.

This helper only evaluates rule predicates. Before deployment, verify an execution adapter for the candidate's sizing, stops/targets, trailing rules, holding limits, cooldowns, position ownership and fill timing. Unsupported mechanics remain research-only until implemented and tested against backtest fixtures. Never silently substitute a simpler strategy. A scheduled loss check is not a broker-held stop, and a 09:35 fill is not a modeled next-open fill.

---

## Environment Variables Required

```bash
ALPACA_API_KEY=your_key       # Required for all trading scripts
ALPACA_SECRET_KEY=your_secret # Required for all trading scripts
```

The host supplies its own model credentials. MCP provider keys belong in the servers, not these scripts; ordinary direct-MCP analysis does not require an OBaI OpenAI key. Use Alpaca **paper** credentials, supplied through the host's secret store/environment and never printed or placed in prompts.

Optional risk limit overrides (defaults in parentheses):
```bash
MAX_POSITION_PCT=10.0         # Max single position as % of equity (10%)
MAX_DAILY_TRADES=20           # Max trades per day (20)
MAX_DAILY_LOSS_PCT=3.0        # Block new exposure at this daily loss (3%)
MAX_EXPOSURE_PCT=90.0         # Max invested capital as % of equity (90%)
MAX_POSITIONS=10             # Held + pending symbols (10)
AUTOTRADER_STATE_DIR=/absolute/persistent/path # Shared by every execution job
```
