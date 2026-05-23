# Prediction-markets backtest — intermediate exits plan

Status: draft for review
Owner: prediction-markets-server
Target tool: `backtest_prediction_rule`

## Goal

Let users backtest a Polymarket rule with stop-loss, take-profit, and max-hold exits in addition to the current hold-to-resolution path. Same tool, same agent surface, additive response shape.

## Non-goals

- New MCP tool. The existing `backtest_prediction_rule` keeps its name; only the `ExitRule` discriminated union grows.
- NO-side support. Schema stays `side: Literal["YES"]`. NO-side stops/TPs land in a follow-up if the V1 release is healthy.
- Forward backtesting on currently-open markets. We require `pm_markets.resolution_status = 'resolved'` so terminal payoff is ground truth.
- Slippage / market-impact modeling. Exit prices come from `pm_price_history.price` (mid or last from CLOB). Documented as a limitation; not solved here.

## Schema (engine/rules.py)

Add `StopTakeProfitExit` as a second variant of the existing `ExitRule` discriminated union. Today `ExitRule` is a single `BaseModel` with `type: Literal["hold_to_resolution"]` (`engine/rules.py:53`) and `SUPPORTED_EXIT_TYPES = frozenset({"hold_to_resolution"})` (`engine/rules.py:26`). Both widen here.

```python
class HoldToResolutionExit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hold_to_resolution"]


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


ExitRule = Annotated[
    HoldToResolutionExit | StopTakeProfitExit,
    Field(discriminator="type"),
]

SUPPORTED_EXIT_TYPES: Final[frozenset[Literal["hold_to_resolution", "stop_take_profit"]]] = (
    frozenset({"hold_to_resolution", "stop_take_profit"})
)
```

Also add a cross-field validator on `PredictionRule` that rejects rules whose entry band overlaps an exit trigger — this is how the plan disambiguates "entry sample already past a trigger" without silently shrinking the universe (Blocker 2):

```python
class PredictionRule(BaseModel):
    ...

    @model_validator(mode="after")
    def _check_entry_exit_disjoint(self) -> PredictionRule:
        if not isinstance(self.exit, StopTakeProfitExit):
            return self
        if (self.exit.stop_price is not None
                and self.exit.stop_price >= self.entry.price_min):
            raise ValueError(
                "stop_price must be strictly below entry.price_min; otherwise "
                "the entry sample itself satisfies the stop and exit semantics "
                "are ambiguous."
            )
        if (self.exit.take_profit_price is not None
                and self.exit.take_profit_price <= self.entry.price_max):
            raise ValueError(
                "take_profit_price must be strictly above entry.price_max; "
                "otherwise the entry sample itself satisfies the take-profit "
                "and exit semantics are ambiguous."
            )
        return self
```

Hold-to-resolution stays the default; existing rules and tests are unaffected. Export `StopTakeProfitExit` (and the renamed `HoldToResolutionExit`) through `engine/__init__.py`'s `__all__` so downstream callers — including tests and the tool layer — can name the variant explicitly.

## Engine (engine/backtest.py)

`simulate_rule` dispatches on `rule.exit.type`. New helper `_simulate_with_exits(market, rule, *, out_skipped)`:

1. **Entry** — reuse `observations.select_earliest_eligible_observation(market.yes_token_rows, rule.entry)`. Same as today. The schema validator already guarantees the entry band sits strictly between `stop_price` and `take_profit_price`, so a freshly entered position cannot already satisfy an exit trigger.
2. **Walk** — iterate rows of `market.yes_token_rows` strictly after `entry_ts` in time order.
3. **Trigger order per row** — check in this order; first match wins:
   - `expiry` if `max_hold_days is not None and row.timestamp >= entry_ts + timedelta(days=max_hold_days)`. Expiry wins over price triggers on or after the boundary because the rule promised not to hold beyond that point.
   - `stop` if `stop_price is not None and row.price <= stop_price`
   - `take_profit` if `take_profit_price is not None and row.price >= take_profit_price`
   - A single sampled price cannot satisfy both stop and TP at once because the schema enforces `stop_price < take_profit_price`. The residual ambiguity is intra-bucket path (the unobserved sub-fidelity trajectory between samples N and N+1); see the `fidelity_${N}min_undercounts_triggers` limitation.
4. **Fallthrough** — if the walk ends with no trigger fired:
   - If `max_hold_days is None` **or** `market.end_date <= entry_ts + timedelta(days=max_hold_days)`: terminal-resolution payoff path. `winning_outcome` decides; `exit_price = 1.0 or 0.0`; `exit_ts = market.end_date`; `exit_reason = "resolution"`.
   - Else (`max_hold_days` set, resolution falls *past* the max-hold boundary, and no sampled row exists at-or-after the boundary): skip the trade with new counter `no_exit_price_for_max_hold`. Refusing to invent an expiry price keeps the response truthful when the price history has a gap across the boundary.

### Trade dataclass additions

```python
exit_reason: Literal["stop", "take_profit", "expiry", "resolution"]
time_to_exit_days: float  # (exit_ts - entry_ts).total_seconds() / 86400.0
```

`entry_ts`, `entry_price`, `exit_ts`, `exit_price`, `realized_win`, `return_on_cost` already exist.

### Payoff

For `stop` / `take_profit` / `expiry`, **`exit_price = row.price` of the triggering sampled row** — the observed price, not the trigger level. Recording the trigger level would imply idealized limit-fill semantics (a resting order always fills at exactly the trigger price), which we cannot back up with mid-or-last price data; quoting the observed sample is the conservative, falsifiable choice.

- `stop` / `take_profit` / `expiry`:
  - `pnl_per_contract = exit_price - entry_price`
  - `return_on_cost = pnl_per_contract / entry_price`
  - `realized_win = pnl_per_contract > 0` (booked-PnL win, not outcome win)
- `resolution`: unchanged.
  - `realized_win = (winning_outcome == "YES")` for YES-side rules.
  - `pnl_per_contract = 1.0 - entry_price` (win) or `-entry_price` (lose).

### Skip-reason counter

Existing `out_skipped` already counts entry-stage misses (`no_eligible_entry`, `ttr_min_unmet`, etc.). Stop/TP/expiry exits all produce a `Trade`, so they don't add skip reasons. The only new skip reason is `no_exit_price_for_max_hold`, fired when `max_hold_days` is set, resolution sits past the boundary, and the price history has no sample at-or-after `entry_ts + max_hold_days` (see step 4 above). The exit-reason breakdown surfaces on the summary side via `exit_breakdown` (see Output format).

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

Field names match the existing top-line shape (`avg_*`, not `mean_*`) so the agent can reuse the same vocabulary across the response.

```jsonc
"exit_breakdown": {
  "stop": {
    "count": 22, "share": 0.30,
    "avg_return_on_cost": -1.00,
    "median_time_to_exit_days": 4.2
  },
  "take_profit": {
    "count": 15, "share": 0.21,
    "avg_return_on_cost": 1.00,
    "median_time_to_exit_days": 8.5
  },
  "expiry": {
    "count": 6, "share": 0.08,
    "avg_return_on_cost": -0.35,
    "median_time_to_exit_days": 30.0
  },
  "resolution": {
    "count": 30, "share": 0.41,
    "avg_return_on_cost": 0.42,
    "win_rate_at_resolution": 0.43,
    "median_time_to_exit_days": 21.3
  }
}
```

Shape rationale:

- One key per exit reason — agent renders as a four-row table without parsing tags.
- `share` is `count / total_trades`, sums to 1.0. Pre-computed so the agent doesn't have to derive percentages.
- `avg_return_on_cost` per reason answers "is my stop saving me money or just realizing losses faster?"
- `median_time_to_exit_days` answers capital-efficiency ("how long is my capital tied up?").
- `win_rate_at_resolution` is the only sub-metric specific to one reason — it's the question users actually ask when comparing TP vs. let-it-ride.

### Existing top-line summary stays

`summarize_trades` emits `sample_size`, `win_count`, `loss_count`, `win_rate`, `avg_return_on_cost`, `median_return_on_cost`, `return_p10`, `return_p50`, `return_p90`, `avg_pnl_per_contract`, `best_trade`, `worst_trade` (`engine/backtest.py:159`). Same definitions, now computed across mixed exit reasons. No breaking change, no new top-line fields (no `profit_factor`, no `p5`/`p95` — those names don't exist today and we're not introducing them).

## Limitations text (`_limitations()`)

When `rule.exit.type == "stop_take_profit"`, append these three lines verbatim. The agent prompt already requires quoting `limitations` so they propagate:

1. `fidelity_${N}min_undercounts_triggers` — intra-bucket price paths between samples are unobserved, so a stop or TP that fires and reverts inside a single sampling interval is missed; trigger counts under-state real triggers, resolution share over-states.
2. `exit_at_observed_sample_price` — exit_price is the sampled row price at trigger, not the trigger level; results approximate a market-order fill at the sample, not a clean limit fill at the trigger price.
3. `zero_market_impact_and_slippage` — results ignore spread, depth, and market impact regardless of position size.

Format mirrors the existing `_limitations()` string style for consistency.

## Phasing / acceptance criteria

| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 | Schema + engine + unit tests | `SUPPORTED_EXIT_TYPES` includes both values; `engine/__init__.py` exports the new model; `simulate_rule` dispatches on `exit.type`. Hold-to-resolution return math, trade ordering, and `monte_carlo_input.returns` stay identical to current; response examples may gain additive exit fields. New tests cover stop-only, TP-only, expiry-only, stop+TP, stop+TP+expiry, expiry taking precedence at/after the max-hold boundary, no-trigger fallthrough to resolution, no-row-past-boundary fallthrough to `no_exit_price_for_max_hold` skip, and schema rejection of rules with entry band overlapping `stop_price` / `take_profit_price`. |
| 2 | Tool response shape + docstring | `backtest_prediction_rule_tool` emits `exit_breakdown` and per-trade `exit_reason` + `time_to_exit_days`. `_limitations()` carries the three caveats when `exit.type == "stop_take_profit"`. The tool docstring at `src/prediction-markets-server/src/server.py:817` is updated to describe the `stop_take_profit` rule shape — agent reads the docstring through the tool input schema, so stale text breaks routing. |
| 3 | PM agent prompt update | One bullet in `src/obai/core_agents/prompts/prediction_markets.md` documenting the new exit type's available fields and the fidelity caveat. No inline example. |
| 4 | Regression coverage | Two new cases in `.agents/skills/obai-e2e-regression/cases/cases.yaml` (active skill) and the `.claude/skills/obai-e2e-regression/cases/cases.yaml` mirror: one stop-only on politics longshots, one stop+TP+expiry. Both assert `exit_breakdown` present, `exit_breakdown` counts sum to `sample_size`, the new skip reason appears in `data_coverage.skipped_reasons` when applicable, and the fidelity caveat appears in the agent's response. |

## Resolved semantic decisions

These three were open in earlier drafts; the implementation depends on each, so they are settled in-spec rather than punted to code review.

1. **Trigger-price semantics → observed sampled row price.** `exit_price = row.price` of the triggering sample. The earlier "use the trigger level" option assumed idealized limit-fill semantics, which the mid-or-last data cannot back up. Recorded in `_limitations()` as `exit_at_observed_sample_price`.
2. **NO-side support → not applicable in V1.** `engine/rules.py:103` constrains `side: Literal["YES"]`, so no `side="NO" + stop_take_profit` case can reach the engine. Revisit when (and only when) NO-side entry math lands.
3. **`max_hold_days` boundary → first sampled row at-or-past `entry_ts + max_hold_days`.** Never invent a price. If no such row exists *and* resolution falls past the boundary, skip with `no_exit_price_for_max_hold` rather than fall back to terminal payoff (which would silently violate the max-hold constraint). Codified in step 4 of the engine walk above.

## Testing strategy

- **Unit (engine):** synthetic `PriceRow` sequences exercising each branch. Assertions on `exit_reason`, `exit_price` (= observed sampled price), `time_to_exit_days`.
- **Edge:** trigger on the very last row before `end_date` → triggers, not resolution; price history with a gap straddling `entry_ts + max_hold_days` → skip with `no_exit_price_for_max_hold`; resolution arriving before `entry_ts + max_hold_days` and no other trigger firing → `resolution` exit even with `max_hold_days` set.
- **Schema:** rules with `stop_price >= entry.price_min` or `take_profit_price <= entry.price_max` reject at `validate_rule`; rule with no stop / TP / max-hold rejects; rule with `stop_price >= take_profit_price` rejects.
- **Property:** for `exit.type == "hold_to_resolution"`, return math, trade ordering, metrics, and `monte_carlo_input.returns` are identical to the pre-change baseline. Response examples may carry additive exit fields.
- **Integration:** `backtest_prediction_rule_tool` returns `exit_breakdown`; `exit_breakdown` counts sum to `sample_size`; per-trade examples carry `exit_reason`; `no_exit_price_for_max_hold` shows up in the existing skipped-reasons counter, not in `exit_breakdown`.
- **Regression:** existing P5 / P6 / P11 cases pass unchanged. New cases assert the new fields.

## Downstream consumers to update

Per `src/prediction-markets-server/CLAUDE.md` (the schema ↔ prompt dependency table):

- `src/obai/core_agents/prompts/prediction_markets.md` — Phase 3.
- `src/obai/core_agents/hub_skills/obai-prediction-market-routing/SKILL.md` — only if intent vocabulary changes (it doesn't; same tool name).
- `src/prediction-markets-server/src/server.py` — Phase 2 docstring update on `backtest_prediction_rule_tool` (line 814 ff.).
- `.agents/skills/obai-e2e-regression/cases/cases.yaml` — active Phase 4 regression cases.
- `.claude/skills/obai-e2e-regression/cases/cases.yaml` — mirror Phase 4 regression cases if the Claude-side copy stays in use.
- `src/obai/evaluation/test_cases/suite.yaml` — add a stop/TP eval case alongside the regression case.
- `src/obai/core_agents/tests/test_prediction_markets_agent.py` — no change unless we assert on the exit-type vocabulary in the prompt.
