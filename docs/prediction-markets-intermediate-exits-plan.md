# Prediction-markets backtest — intermediate exits plan

Status: draft for review
Owner: prediction-markets-server
Target tool: `backtest_prediction_rule`

## Goal

Let users backtest a Polymarket rule with stop-loss, take-profit, and max-hold exits in addition to the current hold-to-resolution path. Same tool, same agent surface, additive response shape.

## Non-goals

- New MCP tool. The existing `backtest_prediction_rule` keeps its name; only the `ExitRule` discriminated union grows.
- NO-side support. Schema stays `side: Literal["YES"]`. NO-side stops/TPs land in a follow-up if the V1 release is healthy.
- Forward backtesting on currently-open markets. We require `pm_markets.resolution_status = '''resolved'''` so terminal payoff is ground truth.
- Slippage / market-impact modeling. Exit prices come from `pm_price_history.price` (mid or last from CLOB). Documented as a limitation; not solved here.

## Schema (engine/rules.py)

Add `StopTakeProfitExit` as a second variant of the existing `ExitRule` discriminated union.

```python
class StopTakeProfitExit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["stop_take_profit"]
    stop_price: float | None = Field(default=None, gt=0.0, lt=1.0)
    take_profit_price: float | None = Field(default=None, gt=0.0, lt=1.0)
    max_hold_days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _at_least_one_trigger(self) -> StopTakeProfitExit:
        if (self.stop_price is None
                and self.take_profit_price is None
                and self.max_hold_days is None):
            raise ValueError(
                "stop_take_profit needs at least one of stop_price / "
                "take_profit_price / max_hold_days; use hold_to_resolution "
                "instead."
            )
        if (self.stop_price is not None
                and self.take_profit_price is not None
                and self.stop_price >= self.take_profit_price):
            raise ValueError("stop_price must be strictly below take_profit_price.")
        return self
```

`ExitRule = HoldToResolutionExit | StopTakeProfitExit`, discriminator field `type`. Hold-to-resolution stays the default; existing rules and tests are unaffected.

## Engine (engine/backtest.py)

`simulate_rule` dispatches on `rule.exit.type`. New helper `_simulate_with_exits(market, rule, *, out_skipped)`:

1. **Entry** — reuse `observations.select_earliest_eligible_observation(market.yes_token_rows, rule.entry)`. Same as today.
2. **Walk** — iterate rows of `market.yes_token_rows` strictly after `entry_ts` in time order.
3. **Trigger order per row** — check in this order; first match wins:
   - `stop` if `stop_price is not None and row.price <= stop_price`
   - `take_profit` if `take_profit_price is not None and row.price >= take_profit_price`
   - `expiry` if `max_hold_days is not None and (row.ts - entry_ts).days >= max_hold_days`
   - Tie-break when one sample sits past both stop and TP: stop wins (worst case for trader, conservative).
4. **Fallthrough** — if the loop ends without a trigger, use the existing terminal-resolution payoff path: `winning_outcome` decides; exit_price = 1.0 or 0.0; exit_ts = market.end_date; exit_reason = "resolution".

### Trade dataclass additions

```python
exit_reason: Literal["stop", "take_profit", "expiry", "resolution"]
time_to_exit_days: float  # (exit_ts - entry_ts).total_seconds() / 86400.0
```

`entry_ts`, `entry_price`, `exit_ts`, `exit_price`, `realized_win`, `return_on_cost` already exist.

### Payoff

- `stop` / `take_profit` / `expiry`:
  - `pnl_per_contract = exit_price - entry_price`
  - `return_on_cost = pnl_per_contract / entry_price`
  - `realized_win = pnl_per_contract > 0` (booked-PnL win, not outcome win)
- `resolution`: unchanged.
  - `realized_win = (winning_outcome == "YES")` for YES-side rules.
  - `pnl_per_contract = 1.0 - entry_price` (win) or `-entry_price` (lose).

### Skip-reason counter

Existing `out_skipped` already counts entry-stage misses (`no_eligible_entry`, `ttr_min_unmet`, etc.). The new exits don'''t add skip reasons — they all produce a `Trade`. The breakdown surfaces on the summary side via `exit_breakdown` (see Output format).

## Output format

Additive. Existing fields kept verbatim. Two changes:

### 1. Per-trade `examples` gain two fields

```jsonc
{
  "condition_id": "0x...",
  "event_slug": "will-the-democratic-party-control-the-senate-after-the-2026-midterm-elections",
  "side": "YES",
  "entry_ts": "2026-03-12T14:00:00Z",
  "entry_price": 0.10,
  "exit_ts": "2026-03-19T18:00:00Z",
  "exit_price": 0.20,
  "exit_reason": "take_profit",   // new
  "time_to_exit_days": 7.17,      // new
  "realized_win": true,
  "return_on_cost": 1.00
}
```

### 2. New `exit_breakdown` block

```jsonc
"exit_breakdown": {
  "stop": {
    "count": 22, "share": 0.30,
    "mean_return_on_cost": -1.00,
    "median_time_to_exit_days": 4.2
  },
  "take_profit": {
    "count": 15, "share": 0.21,
    "mean_return_on_cost": 1.00,
    "median_time_to_exit_days": 8.5
  },
  "expiry": {
    "count": 6, "share": 0.08,
    "mean_return_on_cost": -0.35,
    "median_time_to_exit_days": 30.0
  },
  "resolution": {
    "count": 30, "share": 0.41,
    "mean_return_on_cost": 0.42,
    "win_rate_at_resolution": 0.43,
    "median_time_to_exit_days": 21.3
  }
}
```

Shape rationale:

- One key per exit reason — agent renders as a four-row table without parsing tags.
- `share` is `count / total_trades`, sums to 1.0. Pre-computed so the agent doesn'''t have to derive percentages.
- `mean_return_on_cost` per reason answers "is my stop saving me money or just realizing losses faster?"
- `median_time_to_exit_days` answers capital-efficiency ("how long is my capital tied up?").
- `win_rate_at_resolution` is the only sub-metric specific to one reason — it'''s the question users actually ask when comparing TP vs. let-it-ride.

### Existing top-line summary stays

`win_rate`, `mean_return_on_cost`, `median_return_on_cost`, `p5_return_on_cost`, `p95_return_on_cost`, `profit_factor` — same definitions, now computed across mixed exit reasons. No breaking change.

## Limitations text (`_limitations()`)

When `rule.exit.type == "stop_take_profit"`, append these three lines verbatim. The agent prompt already requires quoting `limitations` so they propagate:

1. `fidelity_${N}min_undercounts_triggers` — stops/TPs triggered by spikes between samples are missed; counts under-state real triggers, resolution share over-states.
2. `mid_or_last_price_only` — exit price assumes the trigger level was filled cleanly; real fills slip on spread + depth.
3. `zero_market_impact` — results assume no market impact regardless of position size.

Format mirrors the existing `_limitations()` string style for consistency.

## Phasing / acceptance criteria

| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 | Schema + engine + unit tests | `simulate_rule` dispatches on `exit.type`. Hold-to-resolution behavior byte-identical to current. New tests cover stop-only, TP-only, expiry-only, stop+TP, stop+TP+expiry, tie-break, no-trigger fallthrough to resolution. |
| 2 | Tool response shape | `backtest_prediction_rule_tool` emits `exit_breakdown` and per-trade `exit_reason` + `time_to_exit_days`. `_limitations()` carries the three fidelity caveats when `exit.type == "stop_take_profit"`. |
| 3 | PM agent prompt update | One bullet in `prompts/prediction_markets.md` documenting the new exit type'''s available fields and the fidelity caveat. No inline example. |
| 4 | Regression coverage | Two new cases in `.claude/skills/obai-e2e-regression/cases/cases.yaml`: one stop-only on politics longshots, one stop+TP+expiry. Both assert `exit_breakdown` present, `exit_reason` counts sum to total trades, and the fidelity caveat appears in the agent'''s response. |

## Open semantic questions

Decide these before Phase 1 lands; document the choice in `_limitations()`.

1. **Trigger-price semantics.** When the 60-min sample is at 0.04 and `stop_price` is 0.05, do we record `exit_price = 0.05` (limit-order semantics) or 0.04 (market-order semantics)? Default proposal: **0.05 (limit semantics)** — closer to how retail traders wire resting stops. Document either way.
2. **NO-side support.** Defer or include in V1? Mirror semantics: `stop_price` above entry, `take_profit_price` below. Default proposal: **defer** to keep V1 surface tight; raise `not_implemented` for `side="NO" + stop_take_profit`.
3. **`max_hold_days` exit boundary.** Trigger at the first sample at-or-past `entry_ts + max_hold_days` (faithful to fidelity) or interpolate to the exact boundary (what users mean)? Default proposal: **next sample at-or-past the boundary** — never claim a price we didn'''t sample.

## Testing strategy

- **Unit (engine):** synthetic `PriceRow` sequences exercising each branch. Assertions on `exit_reason`, `exit_price`, `time_to_exit_days`.
- **Edge:** stop and TP triggered by the same sample → stop wins; entry sample itself past a trigger → reject as `no_eligible_entry`; trigger on the very last row before `end_date` → triggers, not resolution.
- **Property:** for `exit.type == "hold_to_resolution"`, trade list and metrics are byte-identical to pre-change baseline (use a recorded fixture from a current run).
- **Integration:** `backtest_prediction_rule_tool` returns `exit_breakdown` with all four reasons summing to total trades; per-trade examples carry `exit_reason`.
- **Regression:** existing P5 / P6 / P11 cases pass unchanged. New cases assert the new fields.

## Downstream consumers to update

Per `src/prediction-markets-server/CLAUDE.md`:

- `prompts/prediction_markets.md` — Phase 3.
- `hub_skills/obai-prediction-market-routing/SKILL.md` — only if intent vocabulary changes (it doesn'''t; same tool name).
- `src/obai/evaluation/test_cases/suite.yaml` — add a stop/TP eval case alongside the regression case.
- `src/obai/core_agents/tests/test_prediction_markets_agent.py` — no change unless we assert on the exit-type vocabulary in the prompt.
