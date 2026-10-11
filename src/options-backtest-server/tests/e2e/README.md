# E2E golden scenarios (ADR 0002 §11)

Each file under `scenarios/` is one claim, derived by hand from the design, ADR 0002 and the
T0 stubs. It was written against the T0 interface, before `simulator.py` had a body; it was not
committed on its own before the engine, so that order is not auditable from git (ADR 0002 §17
item 51). `test_goldens.py` runs every file as one pytest id (the file stem).
`test_runner_unit.py` tests the runner itself and checks every scenario file against the
mechanical subset of the hand-derivation rules below (listed at their start). Implementers
never edit `[expected]`. A dispute is settled against the design text by someone who wrote
neither side.

```
defaults.toml        every MarketSpec field: zero drift, zero vol, zero rates
strategies/*.json    complete strategy documents (vendored schema + WP1 check_strategy)
scenarios/G*.toml    one scenario each; the stem starts with the id and "_"
runner.py            parse → run → observe → compare
```

## Pipeline

`runner.run_scenario` runs these steps:

1. It reads `strategies/<file>`, applies the scenario's RFC 7396 merge patch and serializes
   the result back to exact JSON.
2. `load_strategy` → `generate(MarketSpec)` → `write_dataset`/`read_dataset` (under `tmp_path`)
   → `models.run.resolve(start, end, manifest_id)` → `engine.simulator.run`.

`observe` reads the artifacts, and `compare` checks them against `[expected]` under §17
item 44.

## Scenario format

The runner reads every TOML file with `tomllib.loads(text, parse_float=Decimal)`. An unknown key,
a missing key or a mistyped value raises. Nothing is ignored.

| Key | Meaning |
|---|---|
| `id` | `"G01"` …; must equal the file-name prefix |
| `proves` | claim ids from the design / §11 table |
| `derived_by`, `checked_by` | author and re-deriving second agent (`""` until checked) |
| `window` | `{ start, end }`, ISO dates, passed to `resolve` |
| `strategy` | `{ file, patch }`: a file name under `strategies/` and an optional merge patch |
| `[market]` | keys that replace those of `defaults.toml` (shallow: a stated key wins whole) |
| `[[market.overrides]]` | one table per override, applied in file order |
| `[expected]` | compared exactly (below) |

### Strategy patch

`patch` is an RFC 7396 merge patch over the JSON file. An object merges key by key. Any other
value replaces the old one. TOML has no `null`, so a patch cannot delete a key. For that reason
the strategy files carry `take_profit`, `stop_loss` and `roll` in their "off" form, and a
scenario turns them on by patching in an object.

The runner converts decimals in patches with `parse_float=Decimal` and writes them back as
`str(Decimal)`, so `0.05` stays `0.05` and never becomes a binary float.

### Market

`defaults.toml` states every `MarketSpec` field (§17 item 17). A scenario's `[market]` repeats only
the keys it changes. `overrides` (default `[]`) is a list of tables. Each table's `kind` is the
override class name in snake_case (§17 item 23), and its other keys are that class's fields,
flat:

| `kind` | Fields |
|---|---|
| `quote_pin` | `contract`, `session`, `slots`, `bid`, `ask`, `bid_size`, `ask_size` |
| `quote_drop` | `contract`, `session`, `slots` |
| `quote_stale` | `contract`, `session`, `slots`, `seconds` |
| `late_availability` | `selector`, `available_at` (offset-aware TOML datetime) |
| `underlying_drop` | `underlying_id`, `field`, `session`, `slots` |
| `settlement_pin` | `series`, `session`, `value` |
| `settlement_drop` | `series`, `session` |
| `rate_drop` | `session`, optional `tenor_days` |
| `activity_pin` | `contract`, `session`, `slot`, `cumulative_volume` |
| `terms_revision` | `contract`, `session`, `deliverable_units` |
| `coverage_status` | `table`, `session`, `status`, `note` |
| `early_close` | `session` |

Values use these forms:

- Contracts: `ROOT:YYYY-MM-DD:P|C:STRIKE`.
- Slots: `DEC`, `F1`, `F2`, `F3`, `CLOSE`, ….
- Enum fields (`field`, `status`): the member value.

### Expected

Every stated value is asserted exactly, and no other value is checked.

- **Required**: `derivation`, `calculation_status`, `warning_codes`.
- **Optional scalars**: `headline_eligible`, `final_equity_usd`, `open_positions`
  (`[[contract, contracts], …]`) and `retired_contracts` (sorted; read by replaying the journal).
- **`[[expected.fills]]`**:
  - `session`, `slot`, `purpose`, `packages`, `campaign_id`.
  - `legs = [[contract, signed contracts, price], …]` in leg order.
  - `net_debit`, `fees`, `limit`.
- **`[[expected.nonfills]]`**: `session`, `slot`, `purpose`, `reason`, `contract` (the leg a
  quote check named) and `net_debit`.
- **`[[expected.settlements]]`**:
  - `session`, `series`, `observation_id`, `value`.
  - `contract_ids`, `net_cash`, `fees`.
  - `entry_input_refs` (the journal entry's `input_refs`, carrying `settlement_ref`).
- **`[[expected.campaigns]]`**:
  - `campaign_id`, `generations`, `start_session`, `end_session`.
  - `basis`, `realized_pnl`, `fees`, `rolls`.
  - `outcome`, `exit_trigger`.
- **`[expected.account."YYYY-MM-DD"]`**: `cash`, `receivable`, `payable` (positive),
  `encumbrance`, `headroom`, `mid_nlv`, `natural_nlv`. These are the CUT-phase `AccountPoint`
  (§17 item 32), with NLVs taken at the CLOSE quotes after that session's settlement.

Comparison rules (§17 item 44):

- **Numbers** compare as `Decimal` values, so `10088` equals `10088.00`. Money and prices are
  written as TOML strings, for example `"-90.00"`.
- **A row** asserts only the keys it states.
- **`fills`, `nonfills` and `settlements` are exhaustive**, in event order. An omitted list means
  no rows, so a scenario with no nonfills simply leaves the list out.
- **`campaigns`** is exhaustive when stated and unchecked when omitted.
- **`warning_codes`** is the whole warning sequence, in emission order.
- **Account rows** are checked only for the dates listed. A listed date with no point is a
  mismatch.

## Hand-derivation rules

These come from §11 and §17 items 40 and 44–46. `test_runner_unit.py` enforces only these, on
every scenario file: zero drift, vol and rates; multiplier 100; no pin, drop or stale on the five
strikes nearest the spot; pinned bids and asks on the tick grid; `n ≤ 3`; a nonempty
`derivation`. The rest (every quote the engine reads is pinned, settlement pins, `strikes_each_side`
coverage, the fee schedule) is held by the author and the re-deriving agent, not mechanically.

**Selection needs no model.**
- The index never moves: zero drift and vol keep spot at 5000 (item 17; G19's XSP spot is
  4512.37 / 10 = 451.237, item 48). Zero rates give DF 1, so F = spot within one tick
  (item 45).
- Every leg uses `moneyness` (with tolerance 0.0005 for step 5 and spot 5000, item 46) or
  `strike_offset`. Exactly one strike lies within tolerance, and each decision has exactly one
  eligible expiry (`weekly_dtes` is chosen for that). G21 is the one exception: a `delta` leg
  over two tied expiries, its pinned candidates' deltas checked against QuantLib with the
  margin of item 46.

**Every quote the engine reads is pinned (item 45).** For every leg, that means:
- the DEC quote of each selection session (spread gate, `Q·D > 0`, limit, capacity, risk);
- the DEC quote of every held session where a take-profit, stop-loss or liquidation P&L must
  be known;
- the quote at the fill slot;
- the CLOSE quote of every session whose `mid_nlv`, `natural_nlv` or `final_equity_usd` is
  stated with a position held.

Generated quotes decide only that a quote exists (`quote_ok`, marks), the parity forward, and
two margins stated in their derivations: G21's unpinned strikes lie at least 0.02 outside the
delta tolerance, and G22's features, whose only use is `percentile gte 0` (item 47).

**Pins stay clear of the spot.**
- Pins, drops and stales stay off the five strikes nearest spot (4990–5010; G19 449–453,
  G22 4950–5050), so the parity forward is read from generated quotes.
- `strikes_each_side` covers every traded strike (22 by default; G19 10, G22 4).
- Pinned prices sit on the market's tick grid (0.05 by default; G24 uses 0.01).

**A position settles only by defeating its close (item 40).**
- On the expiry session, TIME_EXIT holds (`dte 0 ≤ exit_dte`). A settling scenario defeats
  that close with a pinned NO_BID (`bid = 0`) on a sell side, or with a refused close. It
  then lists the warning the defeat causes: EXIT_DEFERRED, or INSUFFICIENT_CAPITAL then
  EXIT_UNFILLED.
- Deep out-of-the-money generated quotes are never used as evidence.
- Every settlement value is a `settlement_pin`, except G19's generated XSP value, which is what
  exercises the divisor and rounding (item 48).

**Fixed conventions.**
- Multiplier 100; $1.00 fee per contract side (trade), $0 settlement fee; zero-interest
  funding.
- Premiums and fees settle T+1 (the next table session); `n ≤ 3`.

**Every number carries its formula** in `derivation`. A second agent re-derives each one and
signs `checked_by` before commit. Every scenario was re-derived independently (`checked_by =
"Ccheck"`) from the design, ADR 0002 §7–§11, §16, §17, WP1 and the T0 interface, before its
`[expected]` was read; a correction is noted in the derivation as `[Ccheck: …]`.

## Conventions of this suite

**Calendar.**
- The windows sit inside 2024-03-04 (a Monday) to 2024-03-28, which has no US market holiday
  (Good Friday is 2024-03-29); G22 alone runs 2024-02-21 to 2024-02-26 over a table starting
  2023-03-06, for its 252-session warmup. The final window session is flat, so FINAL never
  interferes, except in the scenarios about FINAL itself (G14's shorter run, G17a, G17c).
- The default expiry is SPXW 2024-03-15 (`weekly_dtes = [11]`). Scenarios needing other expiries
  set `weekly_dtes` explicitly.

**Strategies.**
- `spxw_put_credit_vertical.json`: sell a 0.98-moneyness put (4900), buy 5 below (4895).
- `spxw_call_debit_vertical.json`: buy a 1.02-moneyness call (5100), sell 5 above (5105).
- Both files share these settings:
  - A weekly Monday entry, one contract, $10,000 initial cash, and fractions 0.10/0.20.
  - Participation 0.10, three attempts at allowance 0.00, and all-or-none fills.
  - `exit_dte` 0 (put) or 14 (call), `max_holding_sessions` 252, and roll disabled.
  - `liquidate_at_final_session`.
- Variants of the put vertical: `_risk_budget` (risk_budget, max 3, fractions 0.20/0.20),
  `_delta` (a -0.30 delta short put, target_dte 10, `max_holding_sessions` 2), `_iv_gate` (daily,
  gated on `options.atm30_iv_percentile_252s`, 0.985 moneyness, 25 wide, fractions 0.25) and
  `xsp_put_credit_vertical.json` (XSP, 1.0083 moneyness, 7 wide).
- Pinned sizes are 50 unless the scenario needs capacity to bind.

**Sign conventions** (ADR 0002 and WP1, restated here, not new):
- `D` is the package net debit, `Σ 100·signed contracts·price`. It is negative for a credit.
- `limit = D + allowance`.
- Headroom is `cash − payable − Σ(max(0, −min payoff) + fee provision)`.
- `realized_pnl = −ΣREALIZED_PNL`, fees excluded.
- `LiquidationPnL = realized_prior − (D_entry + entry fees) − D_close − close fees` against
  `basis = |D_entry|`.

**Derivation text** names contracts by short aliases, such as `P4900 = SPXW:2024-03-15:P:4900`.
It walks the sessions in order through DEC, F1–F3, CLOSE and CUT, then derives the campaign
totals and the warning sequence.

**Why a pin exists.** A comment above each pin says why it is there whenever the reason is more
than "a traded or decided quote".
