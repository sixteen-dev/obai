# ADR 0002 — Synthetic data foundation, complete R1 simulation, e2e suite, LEAN differential

Status: accepted, 2026-09-25. Scope: WP2-S (synthetic subset of WP2) and WP3 of the design, plus the e2e and LEAN harnesses of §18; branch `feat/options-backtest-wp1`. Builds on ADR 0001 as amended; WP1 changes only where that ADR already announced them.

## Decision

Two proposals ("data-first", "sim-first") were judged. **"sim-first" is the base**: in-memory `FrozenDataset` verified by digest, zero new runtime dependencies (stdlib `NormalDist`, `tomllib`, `hashlib`, `zoneinfo`), `synthetic` never imported by `src`, a result constructor that refuses a headline unless the run is valid and flat, the α abstraction with a trace checker, TOML scenarios parsed with `parse_float=Decimal`, disjoint-file tracks. It fits WP1 as built (pure functions, no I/O, `Journal` the only mutable object) and "synthetic first, no provider" better than an ingestion stack for data that does not exist yet.

**Grafted from "data-first"**: calendars as manifest data; coverage `COMPLETE|GAP|UNKNOWN` with the window rule; `MISSING_VALUATION` as its own code (the TLA canary is named for it); the `CapacityBook` reuse test; a canonical on-disk form, so a frozen manifest is a file; every LEAN path and scale verified before use; one claim per golden scenario; pinned-quote derivation; `expected` untouchable by implementers.

**Rejected**: pyarrow/Parquet, scipy, tzdata and `providers/` now (WP2 adds them with the first real provider; digests are of bytes either way, so the manifest model does not change); `policies.py`; features computed inside `run` (they are §8.2 records, computed at dataset creation through the as-of view, so a feature version change is a new manifest); "sim-first"'s LEAN paths (`/Lean/Launcher/config.json`, index folder `spx`, index prices ×10000 — all three wrong, §12); both proposals' owner questions that are engineering decisions (§16).

## 1. Layout

```
src/options_backtest/
  data/records.py     §8.2 subset, FidelityClass, QuoteStatus, TradingSession, CoveragePartition
  data/manifest.py    DatasetManifest, FrozenDataset (canonical sort, table digests, manifest_id)
  data/store.py       write_dataset / read_dataset: canonical JSONL + manifest (the only I/O)
  data/asof.py        AsOfView
  reference/calendars.py  sessions, DTE, slot clock, schedules — pure over the session table
  reference/products.py   ProductRules SPXW/XSP, terms(), settlement_price()
  reference/rates.py      bill_df, DiscountCurve
  pricing/european.py     black76, greeks, spot_delta          (float64 only)
  pricing/iv.py           implied_vol, parity_forward
  pricing/features.py     atm30_iv, iv_rank/percentile_252, return_20s, close_to_sma_50s
  synthetic/market.py     MarketSpec, Override, generate() -> FrozenDataset
  models/{run,artifacts,result}.py  ResolvedRun + registries; artifact records; SimulationResult
  engine/{clock,orders,fills,selector,campaign,lifecycle,validity,simulator}.py
tests/{unit,conformance,reference,e2e,lean}/
```

Imports flow one way: WP1 → `data.records` → `data.manifest` → {`store`, `asof`} → `reference` → `pricing` → `engine` → `simulator`. `pricing` imports nothing above `money`. `synthetic` imports `pricing`, `reference`, `data`; no `src` module imports `synthetic` (a unit test asserts it). Only `simulator` imports both `selector` and `fills`. WP1 is unchanged except: `book_cash_settlement(..., settlement_ref: str)` (ADR 0001 §11 amendment, test first, all-OTM package included); `ErrorCode` gains `SELECTION_BUDGET_EXCEEDED` and `MISSING_VALUATION`; `errors.SimulationInvariantError` (a job failure, never an invalid run).

## 2. Records, manifest, as-of view (design §8.2–§8.4)

Frozen slotted dataclasses; UTC int ns; `Price`/`Usd`/`Decimal`; every record carries `Provenance(source_id, source_schema_version, raw_object_digest, normalizer_version, revision_id)`. `contract_id = "{root}:{expiry YYYY-MM-DD}:{C|P}:{strike}"` with a normalized decimal strike.

```python
class FidelityClass(StrEnum): SYNTHETIC_FIXTURE, HISTORICAL_SNAPSHOT, HISTORICAL_QUOTE_EVENTS   # ordered; run = min
ContractVersion(version_id, terms: ContractTerms, root, underlying_id, listed_at_ns, last_tradable_at_ns,
                settlement_series, effective_from_ns, effective_to_ns | None, known_from_ns, provenance)
QuoteObservation(observation_id, contract_id, bid: Decimal, ask: Decimal, bid_size: int, ask_size: int,
                 observed_at_ns, available_at_ns, session_date, provenance)
    .status() -> QuoteStatus  # VALID | LOCKED | NO_BID | CROSSED | ZERO_ASK | NEGATIVE ; raw sides are kept
    .quote() -> Quote         # only for VALID/LOCKED/NO_BID, else ValueError
UnderlyingObservation(observation_id, underlying_id, field: INDEX_VALUE | OFFICIAL_CLOSE, value: Price,
                      observed_at_ns, available_at_ns, session_date, provenance)
ActivityObservation(observation_id, contract_id, cumulative_volume: int, measured_through_ns, available_at_ns, provenance)
SettlementObservation(observation_id, settlement_series, session_date, value: Price, available_at_ns,
                      payable_date, final: bool, correction_version: int, provenance)
RateObservation(observation_id, curve_id, tenor_days: int, bey: Decimal, observation_date, available_at_ns, provenance)
FeatureObservation(feature_id, feature_version, session_date, value: Decimal | None, max_input_available_at_ns,
                   warmup_count: int, missing_reason: str | None, input_digest)
TradingSession(session_date, open_ns, close_ns, cutoff_ns, early_close: bool)
CoveragePartition(table, session_date, status: COMPLETE | GAP | UNKNOWN, note)
DatasetManifest(manifest_id, fidelity, limitations, tables: tuple[TableDigest(table, rows, sha256), ...],
                calendar_version, product_rules_version, feature_versions, license_policy_id)
FrozenDataset(manifest, sessions, contracts, quotes, underlying, activity, settlements, rates, features, coverage)
```

`FrozenDataset.freeze(...)` sorts every table by its natural key, serializes rows as canonical JSON (sorted keys, decimal strings, no floats) and hashes; `manifest_id` is the sha256 of the table digests and versions, so shuffled input rows give the same manifest (C34). Synthetic provenance forces `SYNTHETIC_FIXTURE` and `license_policy_id="synthetic_public"`. `store.write_dataset(dataset, dir)` writes one JSONL per table plus `manifest.json`; `read_dataset(dir)` re-freezes and refuses a digest mismatch.

`AsOfView(dataset, at_ns)`: every accessor filters `available_at_ns <= at_ns and observed_at_ns <= at_ns`, latest by `observed_at_ns`, backward only, never across a session. `contract(id)` (known and effective at `at_ns`), `listed(root)`, `quote(id, *, max_age_ns, observed_after_ns=None)`, `index_value(underlying_id, *, max_age_ns)`, `activity(id)`, `settlement(series, session_date)` (final only), `curve(curve_id) -> DiscountCurve | None`, `feature(feature_id, session_date)`, `coverage(table, session_date)`.

## 3. Reference (§10.1, §12.1, §13.1)

`calendars.py` is pure over the `TradingSession` table: `sessions_between`, `next_session`, `dte(session_date, expiry_date)` (calendar days, product-local), `slot_times(session) -> Slots(dec, f1, f2, f3, close, cut)` with `dec = close − 15 min`, `f_k = close − (15 − k) min`; `scheduled(schedule, session, sessions)`: daily every session; weekly the ISO weekday, else the next session of the same ISO week, else none; monthly the Nth session of the month. The generator computes `close_ns` (regular close capped at 16:00 America/New_York; early closes 13:00) and `cutoff_ns` (23:59:59 local) once with `zoneinfo`; the engine never converts zones.

`ProductRules(root, underlying_id, family, settlement_series, settlement_divisor: Decimal, settlement_places: int, settlement_rounding, premium_multiplier: Decimal, deliverable_units: Decimal, last_trade_local: time, status)`. `SPXW`: underlying `SPX`, divisor 1, `{SPX: 100}`, multiplier 100. `XSP`: underlying `XSP`, divisor 10, places 2, `ROUND_HALF_UP`, `{XSP: 100}`, multiplier 100; its intraday spot is exactly SPX/10, its settlement `settlement_price(rules, spx_official) -> Price` is rounded. Both are `status="template_unverified"` until WP2 sources Cboe. `terms(strike, right, expires_at_ns) -> ContractTerms` sets AEA `= 100 × K`.

`bill_df(bey, tenor_days) -> float = 1/(1 + y·n/365)`; `DiscountCurve((tenor_days, df), ...).df(t_days) -> float | None`: log-DF linear between brackets, `DF(0) = 1` below the first tenor, `None` beyond the last. Synthetic tenors 28/91/182 days; WP2 qualifies the real mapping.

## 4. Pricing (§13.1–§13.2)

Float64, ACT/365F from exact instants, `statistics.NormalDist`. `black76(F, K, t, df, sigma, right) -> float` per unit; `greeks(F, S, K, t, df, sigma, right, multiplier) -> Greeks` per one long contract in canonical units ($/$, $/$², $/day, per 1.0 vol, per 1.0 rate); `spot_delta(F, S, K, t, df, sigma, right) = df·(F/S)·(N(d1) − [put])`, the normalized long-option spot delta a selector compares after `Decimal(float)`. `implied_vol(price, F, K, t, df, right) -> IvResult(value | None, reason)`: bound checks first, bisection on `[1e-4, 5.0]`, residual `max(1e-8, 1e-8·|price|)`, ≤ 200 iterations, `None` with reason on no root, expired time or vega near zero. `parity_forward(pairs, df, spot) -> ForwardResult(value | None, reason, pairs_used)`: five nearest strikes to spot with both sides `VALID`, at least three, `F_j = K_j + (C_mid − P_mid)/df`, median; `None` if `F ≤ 0` or the inclusive-quantile IQR exceeds 0.5% of F.

`features.feature_series(dataset, rules, versions) -> tuple[FeatureObservation, ...]` computes, per session at its CLOSE snapshot through `AsOfView(close_ns)`: `atm30_iv` (bracketing expiries around 30 calendar days, OTM put below F and OTM call above F, total variance linear in `log(K/F)` to zero, then linear in T), `iv_rank_252` and `iv_percentile_252` over the preceding 252 valid sessions (incomplete window ⇒ `None`, `warmup_count`), `return_20s = close_d/close_{d−20} − 1`, `close_to_sma_50s = close_d/SMA_50 − 1`. `max_input_available_at_ns` is the maximum input `available_at`, so a session's feature is visible only from the next session's decision (C04). A missing input ⇒ `value None` with `missing_reason`, never zero.

## 5. Synthetic generator (§7.4)

```python
MarketSpec(seed, first_session, last_session, holidays, early_closes, index_start: Decimal, daily_drift, daily_vol,
           sigma, rates: tuple[(tenor_days, bey)], roots, weekly_dtes, strike_step, strikes_each_side,
           tick, half_spread_abs, half_spread_rel, bid_size, ask_size, premium_multiplier, deliverable_units,
           overrides: tuple[Override, ...])
generate(spec) -> FrozenDataset    # pure; random.Random(seed) drives the index path only
```

Per session: log-normal index path, INDEX_VALUE at DEC, F1–F3 and CLOSE; a chain per root and listed expiry with Black-76 mids at `sigma`, bid/ask `mid ∓ half spread` on the tick grid, fixed sizes, `available_at = observed_at`; OFFICIAL_CLOSE and the final settlement of each expiring series available at 17:00 local; CMT rates available at the next session's DEC; `COMPLETE` coverage. Overrides apply last, in order: `QuotePin(contract, session, slots, bid, ask, sizes)`, `QuoteDrop`, `QuoteStale(seconds)`, `LateAvailability(selector, available_at)`, `UnderlyingDrop`, `SettlementPin`, `SettlementDrop`, `RateDrop`, `ActivityPin`, `TermsRevision`, `CoverageStatus`, `EarlyClose`.

## 6. Run, result, artifacts (§14.5, §15.2 subset)

`ResolvedRun(strategy: ValidatedStrategy, start_date, end_date, manifest_id, fee_schedule: AssumedFlatFeeSchedule, policy_versions, engine_version)`; `resolve_policies(spec) -> Policies` binds `illustrative_flat_1usd_per_contract_side_v1` ($1.00/$0/$0) and `illustrative_zero_interest_no_borrow_v1`; any other id is `SpecRejected(INVALID_STRATEGY_RULE)` at its pointer.

Artifacts (`models/artifacts.py`): `SimEvent(event_id, at_ns, session_date, slot, phase, seq, kind, campaign_id, input_refs, summary)`, `summary` = cash, receivable, payable, reserve, held, order, status; `AccountPoint(session_date, market_valuation_at_ns, ledger_cutoff_at_ns, cash, receivable, payable, encumbrance, headroom, mid_nlv | None, natural_nlv | None)`; `PositionRow`; `CampaignRecord(campaign_id, generations, start_session, end_session, basis, realized_pnl, fees, rolls, outcome: CLOSED | SETTLED | OPEN | INCOMPLETE, exit_trigger)`, P&L `= −ΣREALIZED_PNL − ΣFEES` over its generations; `CandidateDecision` (gate inputs; per expiry F, DF, skip reason; every package with quote ids, IV, delta, error, D, score, verdict; choice; `cap_bound`; `candidate_set_digest`); `QualityFinding`; `ArtifactBundle(result, events, journal, positions, account_curve, campaigns, candidate_decisions, quality, digests)`, each digest the sha256 of a table's canonical JSON.

`SimulationResult` carries only what WP3 can defend: `calculation_status`, `data_fidelity` + limitations, `execution_basis="synthetic_natural_package"`, `calibration_status="uncalibrated"`, `cost_basis="assumed_schedule"`, `assignment_basis="not_applicable"`, windows, `valuation_clock`, `initial_equity_usd`, `final_equity_usd | None`, open/unsettled summaries, `end_policy`, `warnings: tuple[Warning(code: WarningCode, session_date, message, refs)]`, `invalid_reasons: tuple[Issue]`, `headline_eligible`, provenance. WP4/5 fields are absent, not zero. The constructor refuses `headline_eligible` unless valid and flat, a non-null final equity unless valid, and a `SYNTHETIC_FIXTURE` result without `SYNTHETIC_FIXTURE_NOT_HISTORICAL`.

## 7. Event loop and R1Campaign refinement (§10.1–§10.2, §21)

`run(resolved, dataset) -> ArtifactBundle` asserts `resolved.manifest_id == dataset.manifest.manifest_id`, books the deposit, then iterates the window's sessions, then at most 5 further sessions that only settle dues; exceeding that raises `SimulationInvariantError`. Events are keyed `(at_ns, phase, seq)` with id `{date}:{slot}:{phase}:{seq}`; a test asserts the key is strictly increasing.

| Slot | Phases |
|---|---|
| OPEN | 1 `book_settle_due(through=d)`; a held contract whose `contract(id)` at OPEN has a new `version_id` ⇒ invalid `UNSUPPORTED_CORPORATE_ACTION` |
| DEC = close−15m | 2 `AsOfView(dec)`; 3 natural marks of held legs (for P&L rules), assert `funding_headroom ≥ 0`; 5 held: FINAL on the final session under `liquidate_at_final_session` (no quote needed), else EXIT if trigger ∧ valid package quote, else ROLL_CLOSE if ¬trigger ∧ roll due ∧ valid, else hold (`EXIT_DEFERRED`); flat: `roll_open_due` ⇒ ROLL_OPEN if openable ∧ ¬CampaignCap else the campaign ends; otherwise ENTRY if scheduled ∧ conditions ∧ not the final session ∧ a package selects and sizes. Orders only |
| F1–F3 | 4 `try_fill`, commit on success; a nonfill at F3 cancels (a cancelled ROLL_OPEN ends the campaign) |
| CLOSE | 3 mid and natural marks of held unexpired legs; missing or invalid ⇒ invalid `MISSING_VALUATION`; package mid outside `[0, W·m·n]` ⇒ `MARK_OUT_OF_RANGE` finding, never clamped |
| CUT | 6 expiring held package: final settlement available ⇒ `book_cash_settlement(contract_ids, {underlying: settlement_price}, lifecycle_fees(CASH_SETTLEMENT, Σ|q|), settles_on=next session, settlement_ref=observation_id)`, else invalid `MISSING_SETTLEMENT`; 7 `AccountPoint` |

Triggers: `TimeExit = dte ≤ exit_dte ∨ held_sessions ≥ max_holding_sessions` (fill session = 1); `LiquidationPnL = realized_prior − entry_cash_incl_fees − natural close debit − estimated exit fees`; `TakeProfit: pnl ≥ fraction × basis`, `StopLoss: pnl ≤ −multiple × basis`, basis = |package premium before fees| at entry, exact `Decimal`; `CampaignCap = campaign_sessions ≥ max_campaign_sessions`; `ExitTrigger = TimeExit ∨ TP ∨ SL ∨ CampaignCap ∨ (RollTrigger ∧ rolls ≥ max_rolls)`; `RollDue = RollTrigger ∧ rolls < max_rolls`. After the last CUT: `liquidate_at_final_session` still held ⇒ `incomplete`; `mark_open_positions` ⇒ valid with the exposure listed. Invalid stops the loop, keeps the diagnostic curve, nulls final equity.

**α (TLA → implementation):** `kind` → `premium_direction`; `quote.ok` → every leg VALID/LOCKED/NO_BID as its side allows, ≤ 120 s old and, at a fill, observed after submission; `cash − pay` → CASH + ΣPAYABLE (TLA pays fees from cash, WP1 posts a payable; invariants use the aggregate); `recv` → ΣRECEIVABLE; `reserve` → Σ`campaign_encumbrances`; `pos` → held lots; `gen` → ledger `campaign_id` `c{n}.g{k}` (one generation per package); `settledGens` → `retired`; `order.market` → `limit_usd is None`; `refused` → price-eligible `INSUFFICIENT_CAPITAL`; `calc/done/headline` → status, loop exit, `headline_eligible`; `AdvanceSession` → OPEN.

| TLA | Enforced by | Scenario |
|---|---|---|
| FullyFunded, ReserveMatchesPosition | assert after every commit (`SimulationInvariantError`) | G06 |
| BasisPositive | `PREMIUM_DIRECTION` gate at fill | G07 |
| OneCampaign, OrdersExpireInSession, RollsWithinCaps | `campaign.decide_*`, cancel at F3 | G08, G09 |
| NoPositionPastExpiry, SettledOnce | WP1 `apply_entry` | G02, G03 |
| FillsOnlyAfterSubmission | `fills` observed_after check | G13 |
| FillNeverRaisesNLV | assert at fill, mid of the fill observation | journal property |
| HeadlineOnlyIfValid, OpenAtEndIsNotValid, CalcMonotone | `validity.RunStatus` (valid → invalid/incomplete only), result constructor | G14–G17 |
| RunTerminates | bounded loop | all |

`tests/conformance/r1_trace.py` rebuilds α from `events` and `account_curve`, checks `Safety` on every state and the action properties on every step, asserts the golden suite reaches every canary, and (Hypothesis, `derandomize=True`) feeds it TLA-scale random markets: multiplier 1, W ∈ {2, 3}, cash at the funding boundary, per-slot quotes from `Quotes ∪ {Missing}`, credit and debit, rolls on and off. Declared outside the model: n > 1, 1- and 4-leg structures, `mark_open_positions`.

## 8. Selector (§9.2)

1. **Gate.** Flat, scheduled, not the final session; every condition holds on the prior session's `FeatureObservation` (unknown ⇒ false, recorded). Roll replacements skip the schedule and keep the conditions (§16).
2. **Expiries.** Listed at DEC for the allowed roots; family and settlement from `ProductRules`; `min_dte ≤ dte ≤ max_dte`; `dte > exit_dte` and `> trigger_dte` when rolling; `last_tradable_at_ns ≥ f3`. Sort `(|dte − target|, expires_at_ns, root, version_id)`; search in order, pruning an expiry once its error exceeds the best score's.
3. **Inputs per expiry.** Spot = INDEX_VALUE ≤ 60 s old; DF at the exact t; F from `parity_forward`; IV from mid. Any missing ⇒ skip with `PRICING_INPUT_UNAVAILABLE`.
4. **Legs.** A candidate needs a quote ≤ 120 s old whose status the leg's side allows (`require_positive_bid_for_entry` rejects NO_BID everywhere), a spread passing the absolute gate or the relative gate `(ask − bid)/mid` (rejected only when both fail, §8.4), and `cumulative_volume ≥ min` when that minimum is positive. Error = `|spot_delta − target|` or `|K/S − target|` lifted with `Decimal(float)`, within tolerance; `strike_offset` legs are exact; `same_as` expiries exact. Sort `(error, spread $, strike, contract_id)`.
5. **Search.** Depth-first over `leg_order`, the whole package judged before an anchor is accepted: distinct contracts, structure strike order, `Q·D > 0` at the decision naturals, a size ≥ 1. Score `(expiry_error, Σerror, Σspread $, ordered ids)`, minimum wins. The 10,000th package evaluation ⇒ `SELECTION_BUDGET_EXCEEDED`, no order, warning.
6. **Sizing.** `fixed_contracts` fits or skips. `risk_budget` counts from `min(max_contracts, max_contracts_per_order)` down to 1: `−expiry_bounds(entry_cash = −D(n) − fees(n)).min_value + exit provision ≤ max_campaign_risk_fraction × mid NLV` (exact `Decimal` product, no `Usd`), preview headroom ≥ 0, `n ≤` decision capacity. Limit `= D(n) + price_allowance_usd`.

## 9. Fill model (§10.3, §11.2)

`try_fill(order, view, capacity: CapacityBook, state, ctx) -> Fill | Nonfill`; checks in order, the first failure recorded:

1. `view.quote(leg, max_age_ns=120 s, observed_after_ns=submitted_at)` for every leg, else `NO_OBSERVATION` (the decision observation itself never fills, C05).
2. Status allowed and the side used has price > 0 and size > 0, else `QUOTE_INVALID`/`NO_SIDE`; buys at ask, sells at bid.
3. `D = Σ premium_usd(natural_i, q_i·n)`; an opening order needs `Q·D > 0`, else `PREMIUM_DIRECTION`.
4. Except FINAL: `D ≤ limit`, else `LIMIT`; a favourable move fills at the later natural price.
5. `capacity = ⌊min_i (remaining_i / |ratio_i|) × participation⌋`, remaining per `(observation_id, side)` net of earlier consumption; `n > capacity` ⇒ `CAPACITY` (all-or-none); a fill consumes `n·|ratio_i|`. FINAL orders are also capacity-bound (§16).
6. `trade_fees` → `book_option_trade(settles_on = next session)` → `apply_entry` preview → `funding_headroom ≥ 0`, else `INSUFFICIENT_CAPITAL` (price-eligible, exposure kept). This one check is the opening rule, §11.2's closing rule and `FillFunded`. Then `Journal.commit`.

## 10. Gaps and validity (§8.4)

| Condition | Outcome |
|---|---|
| Candidate quote missing/stale/crossed/zero-ask; no eligible package | skipped with reason; valid |
| Scheduled session whose chain partition is `GAP` | missed opportunity, `DATA_COVERAGE_GAP` warning; valid |
| Any window session with an `UNKNOWN` or absent partition | invalid `DATA_COVERAGE_GAP` |
| Curve, forward or feature unavailable | expiry skipped / condition false; warning |
| Invalid exit quote; nonfill after F3 | `EXIT_DEFERRED` / `EXIT_UNFILLED`; exposure kept; valid |
| Held leg without a valid CLOSE mark | invalid `MISSING_VALUATION` |
| Settlement not final by CUT (missing or late) | invalid `MISSING_SETTLEMENT` |
| Held contract re-versioned | invalid `UNSUPPORTED_CORPORATE_ACTION` |
| Final liquidation unfilled and unsettled | `incomplete` |
| Headroom < 0 after a commit, ledger invariant | `SimulationInvariantError`; job fails |

## 11. E2E suite (`tests/e2e/`)

One TOML per scenario, read with `tomllib.loads(text, parse_float=Decimal)` over `defaults.toml`; the runner does strategy bytes → `load_strategy` → `generate` → `write_dataset`/`read_dataset` → `run` → compare. Every stated value is asserted exactly; `fills`, `nonfills` and `settlements` are exhaustive lists; account rows only for listed dates.

```toml
id = "G01"
proves = ["C10"]
derived_by = "…"
checked_by = "…"
window = { start = "2024-03-04", end = "2024-03-15" }
strategy = { file = "spxw_put_credit_vertical.json", patch = { sizing = { method = "fixed_contracts", contracts = 1 } } }
[market]
seed = 1
index_start = "5000"
sigma = "0.18"
strike_step = "5"
[[market.overrides]]
kind = "quote_pin"
contract = "SPXW:2024-04-19:P:4900"
session = "2024-03-04"
slots = ["DEC", "F1"]
quote = { bid = "2.00", ask = "2.20", bid_size = 50, ask_size = 50 }
[expected]
calculation_status = "valid"
warning_codes = ["SYNTHETIC_FIXTURE_NOT_HISTORICAL"]
derivation = "cash = 10000 + 100·(2.00 − 1.10) − 2·1.00; headroom = cash − (100·5 + 2·1.00)"
[[expected.fills]]
session = "2024-03-04"
slot = "F1"
purpose = "entry"
legs = [["SPXW:2024-04-19:P:4900", -1, "2.00"], ["SPXW:2024-04-19:P:4895", 1, "1.10"]]
net_debit = "-90.00"
fees = "2.00"
[expected.account."2024-03-05"]
cash = "10088.00"
headroom = "9586.00"
```

Hand-derivation rules: every traded quote, index print and settlement is a pin on the tick grid; strategies use `moneyness` or `strike_offset` selectors with one strike in tolerance, so selection needs no model (only G21 uses `delta`, its candidate deltas verified against QuantLib by the author); multiplier 100, $1.00 per contract side, zero interest, T+1, `n ≤ 3`; every number carries its formula; a second agent re-derives before commit. The author works from the design, this ADR and the T0 stubs, and commits before `simulator.py` exists; implementers never edit `expected`; a dispute is settled against the design text by someone who wrote neither side.

| ID | Scenario | Proves |
|---|---|---|
| G01 | credit put vertical, TP exit at naturals: 10,088 → 10,006 | C10; LEAN L1 |
| G02 | same, held to settlement 4,897 between strikes: 9,788, one entry | C13; L2 |
| G03 | expires all OTM: zero flow, `settlement_ref`, contracts retired | settlement_ref |
| G04 | debit call vertical, stop-loss, then `exit_dte` | debit basis |
| G05 | exit limit missed F1–F3, cancelled; fills next session | failed exit; L3 |
| G06 | close ask above width, tight cash: refused, kept, settles | C37 |
| G07 | debit package at zero net debit | C37 |
| G08a/b | roll trigger after `max_rolls` ⇒ exit; cap with replacement pending ⇒ flat | C37 |
| G09 | sequential roll: close, replacement next decision, realized linked | rolls |
| G10 | crossed at exit DEC deferred; zero ask; zero bid on a sell leg | C08 |
| G11 | 121 s stale vs 120 s | C08 |
| G12 | one leg missing at F1; capacity below n; `CapacityBook` reuse (unit) | C09 |
| G13 | F1 observed at submission time: no fill; F2 fills | C05 |
| G14 | held CLOSE mark missing ⇒ invalid; shorter rerun valid | C35 |
| G15a/b | settlement missing; published after CUT | C21 |
| G16 | held terms revision | C21 |
| G17a/b/c | final quote missing ⇒ incomplete; `mark_open_positions` ⇒ valid open; refused final, settled by CUT ⇒ valid | C36 |
| G18 | 13:00 early close: 12:45/12:46…; no post-close quote used | C06 |
| G19 | XSP: SPX 4,512.37 ⇒ 451.24; multiplier 100 | divisor, rounding |
| G20 | `risk_budget` from cap down; capacity binds | sizing |
| G21 | delta selection, QuantLib-verified single candidate; tie-break | §9.2 |
| G22 | a 252-window IV feature (`iv_percentile_252`, item 47) at 251 vs 252 sessions; same-session feature invisible | warmup; C04 |
| G23 | `GAP` partition ⇒ missed opportunity; `UNKNOWN` ⇒ invalid | coverage |
| G24 | weekly schedule over a holiday; no entry on the final session | §9.1 item 10 |

Suite-wide: **C03** mutate every record with `observed_at` or `available_at` after `t_k` ⇒ events up to `t_k` byte-identical; **C04** same-session volume and official close invisible at DEC; **C34** shuffled rows ⇒ same `manifest_id`, same artifact digests, `replay(journal) == final state`; **determinism** a rerun and a subprocess with a different `PYTHONHASHSEED` give identical digests.

## 12. LEAN differential harness (`tests/lean/`, marker `lean`, excluded by `-m "not lean"`)

Steps 1, 4 and 5 and M11 below describe the Docker image and are **superseded by §16 owner decision 3** (native build, C# replay algorithm); the verified data paths, scales and engine behaviours still hold.

Verified 2026-09-25 against the LEAN repository, the `lean-cli` runner and PyPI: the image `COPY`s `/Lean/Data/` (market-hours and symbol-properties databases ship), `WORKDIR /Lean/Launcher/bin/Debug`, `ENTRYPOINT dotnet QuantConnect.Lean.Launcher.dll`, config read from `/Lean/Launcher/bin/Debug/config.json`; index bars `index/usa/minute/spx/{yyyyMMdd}_trade.zip` → `{yyyyMMdd}_spx_minute_trade.csv`, `ms,o,h,l,c,volume` **unscaled**; option bars `indexoption/usa/minute/spxw/{yyyyMMdd}_quote_european.zip` → `{yyyyMMdd}_spxw_minute_quote_european_{put|call}_{strike×10000}_{yyyyMMdd}.csv`, `ms, bid OHLC, bid size, ask OHLC, ask size`, prices ×10000; supported index-option tickers SPX/NDX/VIX/RUT + SPXW/RUTW/VIXW/NDXP/NQX (no XSP); `QuoteBar.Close = (Bid.Close + Ask.Close)/2`; market and combo-market orders fill in the submitting slice, other types from the next; `ComboLimitFill` is strict; `Option.IsAutoExercised` needs intrinsic ≥ 0.01; option defaults `ImmediateSettlementModel`, `OptionMarginModel`, `DefaultOptionAssignmentModel` (hourly, 5% ITM). The image is not yet pulled; its digest is recorded at run time.

Scenarios L1 = G01, L2 = G02, L3 = G05 on SPXW over real 2024 non-holiday weeks.

1. **Preflight.** `docker image inspect` digest; `docker create` + `docker cp` of the image's `config.json`.
2. **Export** (`export.py`, pure from `FrozenDataset`): an observation at `t` is a bar `[t − 1 min, t)` with O=H=L=C; only observed slots produce bars; on an expiry day the 16:00 index bar closes at the official settlement (M3). Paths and scales in one table, `LEAN_FORMAT`.
3. **Probes**, run first, any mismatch fails the suite: echo (every fill and 16:00 mark LEAN reports equals the export); expiry (when LEAN exercises a cash-settled SPXW package, at what price and cash); combo-limit (strict versus inclusive at an exact limit).
4. **Replay** (`replay.py`, LEAN's Python, imports nothing of ours, reads `/LeanCLI/replay_input.json`): `add_index("SPX", Resolution.MINUTE)`; `Symbol.create_option(spx, "SPXW", Market.USA, OptionStyle.EUROPEAN, right, strike, expiry)` + `add_index_option_contract(symbol, Resolution.MINUTE, fill_forward=False)`; the security initializer sets a $1 × |quantity| fee model ($0 on `OPTION_EXERCISE`), `BuyingPowerModel.NULL`, `NullOptionAssignmentModel()`, default immediate settlement; our fills become `combo_market_order` at our fill instants; L3 also replays our DEC limit as `combo_limit_order` cancelled after F3 (diagnostic); order events and each 16:00 `cash`, `unsettled_cash`, `total_portfolio_value` and holdings go to `/Results/replay.jsonl`.
5. **Run.** `docker run --rm --network none -v exp/index:/Lean/Data/index:ro -v exp/indexoption:/Lean/Data/indexoption:ro -v cfg.json:/Lean/Launcher/bin/Debug/config.json:ro -v algo:/LeanCLI:ro -v out:/Results quantconnect/lean@<digest>`; config patched only for `environment`, `algorithm-type-name`, `algorithm-language: Python`, `algorithm-location: /LeanCLI/replay.py`, `data-folder`, `results-destination-folder`, `close-automatically`; timeout 600 s, `docker rm -f` in `finally`. No docker or image ⇒ `pytest.skip` with the reason.
6. **Reconcile** exactly: fill price, quantity, fee; trade-date total cash (`CASH + ΣRECEIVABLE + ΣPAYABLE` against `cash + unsettled_cash`) after each fill and at CLOSE; mid NLV against `total_portfolio_value`; settlement cash at the next session's close. Any unexplained difference fails; nothing is widened. Report `docs/lean-differential.md`: digests, probes, per-scenario tables, mismatches.

Known mismatches: **M1** fills replayed at our instants, so timing, limits, retries, participation and all-or-none are ours. **M2** LEAN settles immediately, we post T+1: trade-date total cash compared, our lag asserted separately. **M3** LEAN exercises at its last underlying price; the export equates the expiry-day 16:00 bar to the official value. **M4** LEAN auto-exercises only at intrinsic ≥ 0.01; scenarios keep intrinsic 0 or ≥ 0.01. **M5** funding not cross-checked; `INSUFFICIENT_CAPITAL` unreproducible. **M6** `ComboLimit` is strict, we fill at `D == limit`. **M7** `total_portfolio_value` equals our mid NLV only when every held leg has a 16:00 bar (true in L1–L3). **M8** LEAN's market-hours database governs; still labeled synthetic. **M9** no XSP. **M10** simulated assignment disabled. **M11** LEAN's Python (3.11) runs the algorithm.

## 13. Dependencies

Runtime: none new. Dev: `QuantLib>=1.43` (cp39-abi3 wheel; resolves on the service's Python 3.13.2, `uv pip install --dry-run` 2026-09-25), imported only under `tests/reference/` as the independent oracle for price, Greeks (`thetaPerDay`, vega and rho per 1.0) and IV at C25 tolerances. pytest gains `markers = ["lean: opt-in LEAN differential"]` and `-m "not lean"` in `addopts`. The docker CLI is shelled out to, no SDK. Deferred: pyarrow/Parquet, DuckDB, Polars, NumPy/SciPy, `exchange_calendars`, tzdata (all WP2 or later, with real data).

## 14. Excluded

Real providers and ingestion; Parquet storage; metrics beyond the account curve and campaign P&L (WP4); jobs, control DB, MCP server, logging (WP5); hub integration (WP6); American exercise, physical settlement, stock, dividends (WP7); financing interest beyond the zero-interest policy; price-improvement and §10.3 sensitivity runs; `warmup_spot_proxy`; BXM/PUT; a LEAN-backed production runtime.

## 15. Tracks

At most three agents at once; owned files are disjoint within a step; shared files change only in T0 and T4.

| Step | Track | Owns | Depends on |
|---|---|---|---|
| T0 | serial | this ADR; `errors.py` codes + `SimulationInvariantError`; `settlement_ref` amendment (test first); stubs of every signature in §2–§9 (`raise NotImplementedError`) under mypy strict; `pyproject.toml`/`uv.lock` (QuantLib, marker) | — |
| T1 | A | `data/`, `reference/`, `synthetic/`, `tests/unit/test_{data,reference,synthetic}_*.py` | T0 |
| T1 | B | `pricing/`, `tests/unit/test_pricing_*.py` (passes C's references) | T0 |
| T1 | C (independent author) | `tests/e2e/**`, `tests/reference/**` (QuantLib references, G21 deltas), `tests/conformance/{r1_trace.py,test_r1_trace.py}` | T0 only; design + ADR + stubs |
| T2 | D1 | `engine/{clock,orders,fills,validity,lifecycle}.py`, their unit tests | T1 |
| T2 | D2 | `engine/{selector,campaign}.py`, `models/{run,artifacts,result}.py`, their unit tests | T1 |
| T2 | E | `tests/lean/{export.py,probes/**,runner.py}`, `tests/lean/test_export.py` | T1-A |
| T3 | D3 | `engine/simulator.py`, `tests/unit/test_simulator.py`; may edit any `src/**` and `tests/unit/**` to make e2e and trace pass; never `tests/e2e/**` expectations | T2-D1, T2-D2 |
| T3 | E | `tests/lean/{replay.py,reconcile.py,test_lean_differential.py}` | T2-E |
| T4 | serial | gates: `ruff check`, `ruff format --check`, `mypy --strict src`, `pytest` (coverage ≥ 90), both repo scripts; then `pytest -m lean`; `docs/lean-differential.md`; README | T3 |

## 16. Engineering decisions recorded

Spread gates: a candidate is rejected only when it fails both the absolute and the relative gate (§8.4's small-price rule). FINAL orders are capacity-bound like every order: displayed size is the only depth evidence; "market-style" waives the limit, not the size. A `GAP` partition on a flat scheduled session is a disclosed missed opportunity; an `UNKNOWN` one invalidates the window. `MISSING_VALUATION` is a new code; a missing settlement stays `MISSING_SETTLEMENT`. Roll replacements ignore the entry schedule (the trigger timed them) and honour the conditions (owner decision 1 below). XSP rounding is a labeled template until WP2 sources it. Determinism is per platform: `Decimal(float)` errors differ across libms only at ties the scenarios exclude by margin. Synthetic markets may sit on real dates; fidelity, warning and license id keep them from reading as history.

Owner decisions (2026-09-25):

1. A roll replacement skips the entry schedule and must pass the entry conditions; if they fail, the campaign ends flat.
2. FINAL orders are capped by `participation_fraction` × displayed size like every order. An unfilled remainder leaves the run `incomplete` unless settlement extinguishes it by that session's CUT.
3. **LEAN runs natively, not in Docker** (the Docker VM disk is full and the owner declined a 15 GB image). This amends §12 steps 1, 4 and 5:
   - The toolchain is a user-space .NET SDK and a LEAN source checkout, built at a pinned commit outside the repo. Tests read its location from `LEAN_ROOT`; the .NET SDK comes from `DOTNET_ROOT` or `PATH`. Either missing ⇒ `pytest.skip` with the reason.
   - The replay algorithm is **C#** (`tests/lean/replay/ReplayAlgorithm.cs` plus a small `.csproj` referencing the built LEAN assemblies). It is compiled by the harness, and LEAN loads it with `algorithm-language: CSharp` and `algorithm-location: <dll>`. Semantics are unchanged: the same order replay, a $1 × |quantity| fee model with $0 on `OPTION_EXERCISE`, `BuyingPowerModel.NULL` and `NullOptionAssignmentModel`, and the same `replay.jsonl` output. M11 becomes "LEAN's C# API; no Python interop".
   - The run is `dotnet QuantConnect.Lean.Launcher.dll --config config.json` with cwd `$LEAN_ROOT/Launcher/bin/Debug`, the checkout's `config.json` unmodified (it is JSONC). The seven keys are passed as LEAN's own command-line overrides (`LeanArgumentParser`: `--environment backtesting --algorithm-type-name … --algorithm-language CSharp --algorithm-location <dll> --data-folder <tmp> --results-destination-folder <tmp> --close-automatically true`). The data folder is a temp copy of the checkout's `Data/market-hours` and `Data/symbol-properties` plus our export. Timeout 600 s; the process group is killed in `finally`. Verified 2026-09-25 at LEAN `b1337938` with .NET SDK 10.0.401: `BasicTemplateSPXWeeklyIndexOptionsAlgorithm` ran natively (5 orders, exit 0).
   - Preflight records the LEAN commit, the .NET SDK version and the export digests instead of an image digest. The `lean` marker and opt-in behaviour are unchanged.

## 17. Interface notes and amendments

Recorded at T0 (2026-09-25); binding for every track exactly like the sections they refine. The T0 stubs' docstrings state the same rules; if a stub and this list disagree, this list wins and the stub is corrected. "design §n" is `options-backtesting-system-v3.md`.

**Layout, names, imports**

1. §1: `data.asof` imports `reference.rates` (a stdlib-only leaf) for `curve()`'s return type, and `reference` imports only WP1 and `data.records`, so the graph stays acyclic; `tests/unit/test_import_graph.py` asserts acyclicity, no `synthetic` import from `src`, the kernel rule of item 2 and that only `engine.simulator` imports both `engine.selector` and `engine.fills`.
2. §1, §4: `pricing.european` and `pricing.iv` import nothing from the package but `money`, `errors` and (for `iv`) `pricing.european`; they take the option right as `right: str` in {"call", "put"}, which `OptionType` members satisfy, and spell F, S, K as `forward, spot, strike` in the ADR's positional order.
3. §1, §6: shared vocabularies sit below their users: `Slot`, `Slots`, `FILL_SLOTS`, `QUOTE_SLOTS` in `reference.calendars`; `Phase` (1 SETTLE_DUE, 2 PUBLISH, 3 MARK, 4 FILL, 5 DECIDE, 6 LIFECYCLE, 7 SNAPSHOT), `QUOTE_MAX_AGE_NS` = 120 s, `SPOT_MAX_AGE_NS` = 60 s, `MAX_SETTLEMENT_SESSIONS` = 5 in `engine.clock`; `OrderPurpose`, `ExitTrigger`, `NonfillReason`, `OrderLeg`, `Order`, `package_debit`, `usable_quote` in `engine.orders`; `CalculationStatus`, `WarningCode` in `models.result`; `models.artifacts` imports only `engine.clock` and `engine.orders` from WP3's engine.
4. §6: `Warning` is named `RunWarning` (it would shadow Python's builtin); `SimulationResult` carries `window_requested`, `window_simulated`, `open_positions` as sorted (contract_id, quantity) and `unsettled_cash` as (ISO date, signed balance); `SimEvent` gains `detail` (`FillDetail`, `NonfillDetail`, `SettlementDetail`, `DecisionDetail` or the invalidating `Issue`), from which the goldens' fills, nonfills and settlements are read.
5. §3: `ProductRules.terms(strike, right, expires_at_ns, *, expiry)` gains keyword `expiry` for the contract id (the engine never converts zones to recover the local date).

**Identifiers and enums**

6. §2: contract `{root}:{YYYY-MM-DD}:{C|P}:{strike}` with the strike `format(value.normalize(), "f")` ("4900", "451.5"); version `{contract_id}@v{n}` (1 at listing, +1 per revision); quote `q:{contract_id}:{session}:{slot}`; underlying `u:{underlying_id}:{field}:{session}:{slot}` (the official close at slot CLOSE); settlement `s:{series}:{session}:c{correction_version}`; rate `r:{curve_id}:{tenor_days}:{observation_date}`; activity `a:{contract_id}:{session}:{slot}`; feature `{underlying_id}:{name}`; order `o:{session}:{purpose}`; event `{session}:{slot}:{phase}:{seq}`, `seq` from 1 within one (session, slot, phase), also the `event_id` of the ledger entry the event books; campaign `c{n}`, generation `c{n}.g{k}`.
7. §2, §6: state and kind enums have lower-case values (`synthetic_fixture`, `valid`, `entry`, `index_value`, `gap`, `filled` ...); `Slot` values are `OPEN DEC F1 F2 F3 CLOSE CUT`; code enums (`WarningCode`, `NonfillReason`, `DecisionReason`, `CandidateRejection`, `ExpirySkip`, `PackageVerdict`, `QualityCode`, `IvReason`, `ForwardReason`) are upper-case; `FidelityClass.rank` orders synthetic 0 < snapshot 1 < quote events 2.

**Records, manifest, store, as-of view**

8. §2: `QuoteObservation.status()` checks in order: a negative price or size NEGATIVE, ask 0 ZERO_ASK, bid > ask CROSSED, bid = ask LOCKED, bid 0 NO_BID, else VALID.
9. §2: canonical JSON is `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` over dataclass fields, `str(Decimal)` (representation kept), ISO dates and aware datetimes, enum values; floats raise. `table_digest` is the sha256 of each row's canonical JSON plus `\n` (the table's JSONL bytes), also used for the artifact digests; tables sort by id (sessions by date, features by (id, version, date), coverage by (table, date)); `manifest_id` is the sha256 of the canonical manifest without `manifest_id`.
10. §2: `freeze` refuses (`ValueError`) a duplicate id, and a record with `synthetic` provenance unless fidelity is SYNTHETIC_FIXTURE and the license `synthetic_public`; synthetic `limitations` are ().
11. §2: an `AsOfView` belongs to the table session with `open_ns ≤ at_ns ≤ cutoff_ns` (else `ValueError`); "never across a session" means quotes and index values of that `session_date`, activity measured within it (`measured_through_ns` is its observed instant), rates published within it (`available_at_ns ≥ open_ns`); settlements, features and coverage are asked for by date.
12. §2: the latest visible observation is the maximum `(observed_at_ns, available_at_ns, observation_id)` and is returned whatever its status (never an older valid one); `quote` and `index_value` return None when its age `at_ns − observed_at_ns` exceeds `max_age_ns` (the bound is inclusive: 120 s passes, 121 s fails) or, with `observed_after_ns`, unless `observed_at_ns > observed_after_ns` (strict: an observation at the submission instant never fills, C05).
13. §2: `contract(id)` is the known (`known_from_ns ≤ at`) and effective (`effective_from_ns ≤ at < effective_to_ns`) version; `listed(root)` adds `listed_at_ns ≤ at_ns ≤ terms.expires_at_ns`, sorted by id; `settlement(series, d)` is final, available and of the highest correction; `curve(id)` takes the latest observation per tenor through `bill_df`, None when empty; `feature(id, d)` is visible once `max_input_available_at_ns ≤ at_ns`; `coverage` is metadata, always visible.

**Calendar, products, rates**

14. §3: sessions are the weekdays in `[first_session, last_session]` not in `holidays`; open 09:30, close 16:00 (13:00 on `early_closes`), cutoff 23:59:59 America/New_York; `slot_instant(OPEN) = open_ns`; a weekly schedule picks the first table session of the ISO week with ISO weekday ≥ `weekday`; a monthly one the `session_ordinal`-th table session of the calendar month (a table starting mid-month counts from its first session).
15. §3: SPXW: underlying `SPX`, series `SPX_PM`, divisor 1; XSP: underlying `XSP`, series `XSP_PM`, divisor 10; both 2 places `ROUND_HALF_UP`, family `us_european_pm_cash_index`, multiplier 100, units 100, last trade 16:00, `template_unverified`, `PRODUCT_RULES_VERSION = "cboe_template_unverified_v1"`. Terms are EUROPEAN, CASH, deliverable `{underlying_id}:{units}` of `units` index units, AEA = units × strike; engine code reads multiplier and deliverable only from `ContractTerms`, so a TLA-scale market may replace the template's multiplier and units.
16. §3: curve id `UST_CMT`; `DiscountCurve.df(t_days)` is linear in log DF with (0, 1.0) as the bracket below the first tenor, a tenor's own DF at that tenor, None beyond the last tenor, `ValueError` for t < 0; `bill_df(0, n)` is exactly 1.0.

**Synthetic generator (§5)**

17. Every `MarketSpec` field is required (e2e defaults live in `defaults.toml`); types and constraints are the stub's; `index_start` has at most 2 decimals; `daily_drift = daily_vol = 0` gives `S_i = index_start` on every session and zero rates give DF = 1.
18. Index: `S_i = Decimal(float(index_start)·exp(L_i))` quantized to 0.01 half-even, `L_0 = 0`, `L_i = L_{i−1} + drift + vol·z_i`, `z_i = random.Random(seed).gauss(0, 1)` drawn once per session i ≥ 1; constant within a session; INDEX_VALUE at DEC, F1, F2, F3 and CLOSE (observed = available = the slot); OFFICIAL_CLOSE = `S_i` observed at CLOSE, available 17:00 local; the XSP value is `S_i/10` exactly; one series per underlying of `roots`.
19. Expiries: `weekly_dtes` are calendar-day offsets from `first_session`, each moved back to the nearest weekday not in `holidays`; every root lists every expiry from the first session's `open_ns` (`@v1`, known and effective then); `expires_at_ns = last_tradable_at_ns` = the expiry date's close (16:00; 13:00 on an `early_closes` session); each needs `dte(first_session, expiry) <` the last rate tenor.
20. Strikes: per root, centre `strike_step · round_half_up(index_start / divisor / strike_step)` in the root's points and `2·strikes_each_side + 1` strikes, calls and puts, fixed for the whole dataset so a held contract never leaves the chain.
21. Quotes at DEC, F1, F2, F3 and CLOSE strictly before `expires_at_ns` (none at the expiry session's CLOSE): Black-76 at `sigma`, `t` ACT/365F from the slot, DF from the spec's curve, `F = S/DF`; half spread `max(half_spread_abs, half_spread_rel·mid)`; bid rounded down and ask up to the tick (never locked or crossed); a bid ≤ 0 becomes 0 with size 0 (NO_BID); fixed sizes; observed = available = the slot.
22. Settlements per root and expiry inside the table: `settlement_price(rules, S_e)`, available 17:00 local, `payable_date` the next weekday not in `holidays`, final, correction 0. Rates per table session S and tenor: dated the previous table session (S − 1 day for the first) and available at S's DEC, so the first session already sees a curve. Coverage `("quotes", d, COMPLETE, "")` per session; activity: none unless pinned.
23. Overrides apply in spec order after generation, features are computed after them from the overridden tables, and an override whose target does not exist (also when an earlier override removed it) is `ValueError`; an e2e TOML `kind` is the snake_case class name.
24. `QuotePin(contract, session, slots, bid, ask, bid_size, ask_size)` replaces prices and sizes (raw, any status); `QuoteDrop(contract, session, slots)` removes; `QuoteStale(contract, session, slots, seconds)` subtracts from `observed_at` only; `LateAvailability(selector, available_at)` sets the `available_at` of the record whose `observation_id` is `selector` to a later aware datetime; `UnderlyingDrop(underlying_id, field, session, slots)` removes; `SettlementPin(series, session, value)` replaces the value as is; `SettlementDrop(series, session)` removes; `RateDrop(session, tenor_days | None)` removes what becomes available at that session's DEC; `ActivityPin(contract, session, slot, cumulative_volume)` adds `a:…` measured through and available at the slot.
25. `TermsRevision(contract, session, deliverable_units)` ends the current version at the session's `open_ns` and adds `@v{n+1}` from then with the same id, expiry, AEA and multiplier and the new units (F05-style); `CoverageStatus(table, session, status, note)` replaces an existing partition; `EarlyClose(session)` changes only the session table (13:00 close) and leaves that session's regular-hours data after its close (phantom post-close data, design §7.3 item 4), whereas `early_closes` generates a genuine early session.

**Pricing and features (§4)**

26. `implied_vol` returns EXPIRED, BELOW_LOWER_BOUND, ABOVE_UPPER_BOUND (at or above), NO_ROOT, VEGA_TOO_SMALL (vega per unit < 1e-10) in that order; `parity_forward` receives every VALID pair and uses the five nearest spot (ties: lower strike), needs three, takes `statistics.median` and the IQR of `quantiles(n=4, method="inclusive")`; `greeks` are BSM Greeks with flat `r = −ln(df)/t` and `q = r − ln(F/S)/t`, theta per calendar day.
27. Features: `{underlying}:{name}` for `options.atm30_iv`, `options.atm30_iv_rank_252s`, `options.atm30_iv_percentile_252s`, `underlying.return_20s` and `underlying.close_to_sma_50s`, all version "1", float64 with `Decimal(float)` values; the close is the CLOSE INDEX_VALUE (the 17:00 official close is not visible at close); windows count table sessions; rank and percentile need the 252 preceding sessions all valid (251 ⇒ None, `warmup`); visibility starts at `max_input_available_at_ns` (the session's CLOSE), so the entry gate reads the previous table session's value.

**Run, loop, artifacts (§6, §7)**

28. `models.run.resolve(strategy, *, start_date, end_date, manifest_id)` binds `FEE_SCHEDULES` (`illustrative_flat_1usd_per_contract_side_v1`: $1.00 per contract per traded side, $0 exercise and settlement) and `FUNDING_POLICIES` (`illustrative_zero_interest_no_borrow_v1`), sets `policy_versions = (("fee_schedule", id), ("funding_policy", id))` and `ENGINE_VERSION = "0.1.0"`, and rejects a root whose rules' underlying is not `product.underlying_symbol` (UNSUPPORTED_PRODUCT at `/product/allowed_option_roots/{i}`), so a strategy trades one underlying and its features are that underlying's.
29. `run` requires `start_date` and `end_date` to be table sessions and at least one table session after `end_date`; the DEPOSIT event `{start}:OPEN:1:1` books `initial_cash_usd` at the first `open_ns`; SYNTHETIC_FIXTURE_NOT_HISTORICAL is the first warning; `settles_on` is always the next table session.
30. The slot program is `simulator.run`'s docstring and the event kinds `SimEventKind`'s; an INVALIDATED event (UNSUPPORTED_CORPORATE_ACTION at OPEN, DATA_COVERAGE_GAP at DEC 2, MISSING_VALUATION at CLOSE for an unexpired held leg without a VALID/LOCKED/NO_BID quote at most 120 s old, MISSING_SETTLEMENT at CUT) stops the loop at once, without a SNAPSHOT that session; its `Issue` has `json_pointer` "" and `affected_interval` the session's ISO date.
31. After the final CUT, `end_status` applies; then, unless invalid, the settle-only sessions of `RunCalendar.after` run while a RECEIVABLE or PAYABLE is outstanding, each OPEN 1 SETTLE_DUE and CUT 7 SNAPSHOT (NLVs None if a position is held); dues left after them raise `SimulationInvariantError`.
32. `AccountPoint`: `market_valuation_at_ns` = CLOSE, `ledger_cutoff_at_ns` = CUT, cash = CASH, receivable = ΣRECEIVABLE, payable = −ΣPAYABLE, encumbrance = Σ(settlement + fee provision), headroom = `funding_headroom`, NLVs from `value_account` at the CLOSE quotes after the CUT settlement; `final_equity_usd` is the final window session's mid NLV when valid; `headline_eligible` iff valid and no option lot is held.
33. `EventSummary` is the α state after the event: cash, receivable, payable (positive), reserve, held (sorted), the live order (None after a fill or a cancel) and status; `input_refs` are quote ids in leg order for orders and fills, the settlement id for SETTLED, sorted ids for marks.
34. Campaigns: numbers count filled entries (an opening that never fills consumes none) and generations count filled openings; an ENTRY fill sets `basis = |D|` of the whole order before fees; a ROLL_CLOSE fill adds the generation's P&L (fees included) to `realized_prior`; an EXIT, FINAL or settlement, or a replacement not opened, ends the campaign; `CampaignRecord.realized_pnl` = −ΣREALIZED_PNL and `fees` = ΣFEES of its generations' entries.
35. Held decisions (DEC 5) use DEC `usable_quote`s of every leg (at most 120 s old; buys VALID/LOCKED/NO_BID, sells VALID/LOCKED); `held_sessions` and `campaign_sessions` count table sessions with the fill session as 1; `LiquidationPnL = realized_prior − (D_entry + entry fees) − D_close − Σ trade fees of the close`; TP `pnl ≥ fraction·basis`, SL `pnl ≤ −multiple·basis` as exact Decimals; an EXIT carries the first true of TIME_EXIT, TAKE_PROFIT, STOP_LOSS, CAMPAIGN_CAP, ROLL_CAP; closing legs keep the opening leg order with negated ratios; the limit is the DEC close `D` plus `price_allowance_usd` once, None for FINAL.
36. Flat decisions: a due replacement ends the campaign (CAMPAIGN_ENDED) on the final session, at the campaign cap, on a GAP session, when the selector finds nothing, or when the ROLL_OPEN is cancelled at F3; a scheduled entry on the final session is ENTRY_SKIPPED(FINAL_SESSION), on a GAP session ENTRY_SKIPPED(DATA_COVERAGE_GAP) with a warning; unscheduled sessions emit nothing; a GAP does not change held-position decisions.
37. Selector (`selector.select`'s docstring): "when rolling" means `roll.mode == "sequential"` for every opening; the expiry sort drops `version_id` because (root, expiry) is unique; spot, DF and F are required for every expiry, IV and delta only for `delta` legs; the moneyness error is `|Decimal(float(K)/float(S)) − target|` exactly; the relative spread gate compares `ask − bid ≤ max_relative_spread · mid` exactly; `strike_offset` legs pass the same quote filters; reaching 10,000 evaluations is SELECTION_BUDGET_EXCEEDED; the risk test's mid NLV is CASH + ΣRECEIVABLE + ΣPAYABLE (openings happen flat); `max_total_risk_fraction` never binds with one campaign, as it is ≥ the campaign fraction; `price_allowance_usd` is added once per order.
38. Fills: the `try_fill` order, every leg through check 1 before check 2; quote age measured from the fill slot; observed strictly after the DEC submission; capacity `floor(min_i(remaining_i/|ratio_i|) · participation_fraction)` exactly, consumed only by a committed fill; a nonfill at F1 or F2 leaves the order live; an F3 nonfill adds ORDER_CANCELLED.
39. Warnings follow `WarningCode`'s emission rules (once per session, decision or order as stated there), in emission order, with refs: synthetic → manifest id; coverage → none; pricing input → `{root}:{expiry}` of each skipped expiry; feature → feature ids; budget → decision id; EXIT_DEFERRED → contracts without a usable quote; EXIT_UNFILLED and INSUFFICIENT_CAPITAL → order id. `QualityCode` has one value, MARK_OUT_OF_RANGE: the held package's CLOSE mid value outside the [min, max] of `expiry_bounds(holdings, 0, ZERO_USD)` (the ADR's [0, W·m·n] for any R1 structure), reported and never clamped.

**Loop details the goldens and the trace checker read (§7, §11)**

Recorded at the T0 derivability audit (2026-09-25), binding as the items above.

40. §7, §11: a position reaches cash settlement only through a close that fails. On its expiry session `TimeExit` holds (`dte = 0 ≤ exit_dte`), and on the final window session under `liquidate_at_final_session` FINAL is submitted whatever the quotes, so every settling golden (G02, G03, G06, G15a/b, G17c, G19) defeats that close and lists the warning it causes: no `usable_quote` for a closing side at DEC ⇒ EXIT_DEFERRED (not on the final session under `liquidate_at_final_session`, where FINAL needs no quote); NO_OBSERVATION, QUOTE_INVALID, NO_SIDE, LIMIT or CAPACITY at each of F1–F3 ⇒ ORDER_CANCELLED and EXIT_UNFILLED; a funding refusal ⇒ INSUFFICIENT_CAPITAL, then EXIT_UNFILLED at F3. Generated deep-out-of-the-money quotes are not evidence of any of these: the golden pins (`bid = 0` is NO_BID, unusable for a sell) or drops the quotes that defeat the close.
41. §7 α: DEC 3 emits MARKED only while a position is held and every held leg has a `usable_quote` for its closing side at DEC, which is `decide_held`'s `quote_ok`; otherwise DEC 3 emits nothing and DEC 5 defers (a missing DEC quote is never MISSING_VALUATION, which is CLOSE's). The MARKED events are α's `quote.ok` witnesses at DEC and CLOSE; at F1–F3 `quote.ok` is a FILLED event or a NOT_FILLED whose reason is not NO_OBSERVATION, QUOTE_INVALID or NO_SIDE. Further α: `Fee` → Σ trade fees of one fill (`trade_per_contract · n · Σ|ratio_i|`); `HeldReserve` → the campaign's `Encumbrance.settlement + fee_provision`; `entryDebit` → `HeldPosition.entry_debit_incl_fees`; `basis`, `realized`, `cstart`, `rolls` → `CampaignState.basis`, `realized_prior`, `start_session`, `rolls`; `pos.expiry` → the date in each held contract id.
42. §7: within one (session, slot, phase), `seq` follows emission order: OPEN 1 SETTLE_DUE (when due) before INVALIDATED; F3 4 NOT_FILLED, ORDER_CANCELLED, then CAMPAIGN_ENDED for a cancelled ROLL_OPEN; DEC 5 emits at most one event. A FINAL order's ORDER_SUBMITTED `input_refs` are the held legs' DEC observation ids that exist, in leg order, possibly (). An EXIT_DEFERRED event's `DecisionDetail` is `(EXIT, DECISION_QUOTE_INVALID, the exit trigger)` when an exit trigger holds, else `(ROLL_CLOSE, DECISION_QUOTE_INVALID, None)`. A `CandidateDecision` is recorded once per `select` call, so never for a skip decided before selection (final session, campaign cap, GAP); an expiry PRUNED by score records no inputs (spot, DF and F None) and no leg candidates. SYNTHETIC_FIXTURE_NOT_HISTORICAL carries `session_date = start_date`.
43. §7, §10: `end_status`'s INCOMPLETE issue is `Issue(UNSUPPORTED_ACCOUNT_STATE, message beginning "incomplete_liquidation", json_pointer "", affected_interval = the final session's ISO date, remediation = "extend the window or set end_policy to mark_open_positions")`, no new code per §1; it is the run's only `invalid_reasons` entry, and the held campaign's record is INCOMPLETE with `end_session` and `exit_trigger` None.
44. §11 runner: numbers compare by `Decimal` value (`10088` equals `10088.00`); a row asserts only the keys it states; `fills`, `nonfills` and `settlements` are exhaustive; `warning_codes` is the whole warning sequence in emission order.
45. §11 pins: the engine reads, and a golden therefore pins (or drops), for every leg: the DEC quote of each selection session (quote filters including the spread gate, `Q·D > 0`, the limit `D + allowance`, the capacity `floor(min_i size_i/|ratio_i| · participation) ≥ n` and the risk test — also under `fixed_contracts`), the DEC quote of every held session on which a take-profit, stop-loss or liquidation P&L must be known (with both rules null a generated DEC quote decides only `quote_ok`), the quote at the fill slot, and the CLOSE quote of every session whose `mid_nlv`, `natural_nlv` or `final_equity_usd` is stated (G17b); a generated CLOSE quote otherwise decides only that the mark exists. The parity forward is read for every searched expiry from generated quotes: with zero rates each quoted mid lies within half a tick of its Black-76 mid (bid rounded down, ask up), so `F` lies within one tick of the spot; pins and drops stay off the five strikes nearest the spot, and `strikes_each_side` covers those and every traded strike.
46. §8: `Decimal(float(K)/float(S))` is not the target's decimal even when `K/S` is (0.98 gives an error near 2e-17), and `error ≤ tolerance` passes, so a moneyness tolerance sits with margin between the intended strike's error and its neighbours' (`≈ strike_step/S`): 0.0005 for target 0.98, step 5, spot 5000. A `delta` golden (G21) leaves the same margin against `F`'s one-tick uncertainty (item 45) and pins the candidates' DEC quotes so their mids, IVs and deltas are QuantLib-checkable.
47. §4, §5 (G22): a 252-window feature needs 252 table sessions before the first session whose feature has a value (0-based index 252), `weekly_dtes` spanning `last_session + 30` days so every session has bracketing expiries, and a last rate tenor above the largest expiry offset (a zero rate still gives DF 1). The golden's condition is on `options.atm30_iv_percentile_252s`, which lies in [0, 100] whenever defined (so `gte 0` holds), because `iv_rank_252s` is None on a zero range, which hand derivation cannot exclude. The gate of session index 252 reads index 251's None (CONDITIONS_FALSE, FEATURE_UNAVAILABLE); that of index 253 reads index 252's value.
48. §5 (G19): XSP goldens set `index_start` to the SPX value (4512.37) with zero drift and vol, so the XSP INDEX_VALUE is exactly 451.237, the generated settlement is `settlement_price` = 451.24 and the divisor and rounding are exercised; a `SettlementPin` value is used as is and tests neither.
