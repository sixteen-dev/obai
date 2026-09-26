# ADR 0001 — WP1 architecture: domain, strict ingestion, exact ledger

Status: accepted, 2026-09-24. Scope: WP1 of `docs/design/options-backtesting-system-v3.md` (the design), branch `feat/options-backtest-wp1`. Companions: `strategy.schema.json`, `example-strategy.json`, `ledger-fixtures.json`, `tla/R1Campaign.tla`.

## Decision

Two proposals ("ledger", "types") were judged. Both share the skeleton — two halves that never import each other, exact `Decimal`, one pure transition `apply_entry`, reserves derived from lots — and both reproduce F01–F05 exactly (recomputed: 10006/+6, 9199/−801, 10299/+1299, 9788/−212, 1000/1000). **"ledger" is the base**: one `Lot` with non-negative unit cost (relief is `n × unit_cost`; no inexact partial-lot division), `AccountKey(kind, ref)` sub-ledgers instead of a special-cased `settles_on` column, one `QuantityEvent` record, entries carrying their own FIFO reliefs, no netting of receivables against payables across entries (exactly `R1Campaign.Funded`; "types" per-date netting is looser than the model), and the design's §17.1 file names. **Grafted from "types"**: sequence-bound entries, the proof-carrying `ValidatedStrategy`, `Tag("oneOf:…")` discriminators for unambiguous pointers, `MissingMarkError` raised rather than a union return, the mypy misuse test, coverage 90. **Rejected**: `Contracts`/`Shares` newtypes, `OptionLot`/`StockLot` split, a five-class `QuantityEvent` union, `Usd.times(Decimal)` before a consumer exists, per-date netting, `pydantic>=2.13.4` as floor (no sibling lock has it), and both proposals' "owner questions" that are engineering decisions (§11).

## 1. Layout

```
src/options-backtest-server/
  pyproject.toml  uv.lock  VERSION (0.1.0)  README.md  docs/adr/0001-wp1-architecture.md
  src/options_backtest/
    errors.py             ErrorCode, Issue, SpecRejected, LedgerInvariantError, UnsupportedLifecycle, MissingMarkError
    money.py              Usd, Price, EXACT
    strict_json.py        bytes -> immutable tree; DUPLICATE_KEY, NONFINITE_NUMBER, limits
    ingest.py             load_strategy(raw) -> ValidatedStrategy; pointer mapping
    models/strategy.py    strict frozen Pydantic StrategySpec (schema mirror)
    models/strategy_checks.py  §9.1 static checks -> CheckResult
    models/market.py      OptionType, ExerciseStyle, SettlementType, Deliverable, ContractTerms, Quote
    models/ledger.py      AccountKind, AccountKey, Posting, Lot, LotRelief, QuantityEvent, FeeLine,
                          LegFill, LedgerEntry, LedgerState
    engine/positions.py   fifo_relief, apply_quantity_events
    engine/journal.py     apply_entry, Journal, replay
    engine/trades.py      book_deposit, book_option_trade
    engine/settlement.py  book_settle_due, book_cash_settlement
    engine/exercise.py    book_physical_exercise
    engine/adjustments.py book_deliverable_adjustment
    engine/fees.py        AssumedFlatFeeSchedule, trade_fees, lifecycle_fees
    engine/valuation.py   value_account, net_pnl, reconcile
    engine/funding.py     expiry_bounds, campaign_encumbrances, funding_headroom
  tests/unit/  tests/conformance/  tests/contracts/ (vendored schema, example, fixtures, SHA256SUMS)
```

Imports (`←` = imported by; no cycles): `errors ← money ← models.market ← models.ledger ← positions ← {trades, settlement, exercise, adjustments}`; `models.ledger ← fees ← funding`; `positions ← journal`; `models.ledger ← valuation`. Spec side: `money ← models.strategy ← models.strategy_checks ← ingest`, `strict_json ← ingest`. The sides meet only in WP3. `trades.py` is the one file absent from §17.1 (`fills.py` is WP3's execution model; booking a fill is ledger work). No `logging_config.py`, no structlog: WP1 has no I/O; every function returns or raises. Logging arrives with `server.py` (WP5).

Repo wiring: append `"src/options-backtest-server"` to `scripts/run-all-tests.sh` and `"src/options-backtest-server:src"` to `scripts/run-all-typechecks.sh`. `uv lock`/`uv sync` only inside the service directory.

## 2. Numeric policy and money types (§8.1)

- `money.EXACT = Context(prec=50, rounding=ROUND_HALF_EVEN, traps=[Inexact, InvalidOperation, Overflow, DivisionByZero])`, a module constant never installed globally; every operator runs under `with localcontext(EXACT)`. The thread default (prec 28, silent rounding) is never used; anything that would round raises `Inexact`.
- `Usd(amount: Decimal)` — frozen, slotted, ordered. Guards: type exactly `Decimal`, finite, ≤ 9 dp, |x| < 1e19 (DECIMAL(28,9)), `-0` normalized. Ops: `+`, `-`, unary `-`, `scaled_by(n: int)`, `is_cents()`. No `Usd * Decimal` in WP1 (fraction × money arrives with WP3 risk caps and must declare its rounding direction then).
- `Price(value: Decimal)` — ≥ 0, ≤ 9 dp, < 1e15 (DECIMAL(24,9)). `Price.mid(bid, ask)`; a half-cent mid is exact and stays in views.
- The only conversions from price to money are `ContractTerms.premium_usd(price, contracts)` = `contracts × premium_multiplier × price` and `Deliverable.value_usd(prices)` = `Σ units × price + cash`. No `Price * Decimal`, no `Price + Usd`: mypy `[operator]` rejects them, which puts invariant 2 in the type checker.
- Divisions are exactly three, each exact or raising: mid; breakpoint `(AEA − cash)/units`; assigned-stock unit cost `AEA/units` (inexact ⇒ `UnsupportedLifecycle`; WP7 defines allocation).
- Cents: postings to CASH, RECEIVABLE, PAYABLE must be whole cents or `apply_entry` raises. WP1 never rounds. Sub-cent producers round before posting under their own policy: XSP settlement (SPX/10, WP2 `ProductRules`), historical fee minimums (WP3). Views (mid NLV, bounds) keep full precision.
- No floats: JSON parses with `parse_float=Decimal`; schema `number` fields are exact `Decimal`; WP2/3 lift float64 Greeks with `Decimal(x)` before comparing.
- Time: UTC integer nanoseconds (`at_ns`, `expires_at_ns`); settlement dates are `datetime.date`.

## 3. Contract terms (`models/market.py`; frozen, slotted dataclasses)

```python
class OptionType(StrEnum): CALL = "call"; PUT = "put"        # e = +1 / -1
class ExerciseStyle(StrEnum): EUROPEAN; AMERICAN
class SettlementType(StrEnum): CASH; PHYSICAL
DeliverableComponent(asset_id: str, units: Decimal > 0)
Deliverable(deliverable_id: str, components: tuple[DeliverableComponent, ...], cash: Usd)
    value_usd(prices: Mapping[str, Price]) -> Usd
ContractTerms(contract_id: str, option_type, strike: Price, exercise_style, settlement_type,
              premium_multiplier: Decimal > 0, deliverable: Deliverable,
              aggregate_exercise_amount: Usd >= 0, expires_at_ns: int)
    premium_usd(price: Price, contracts: int) -> Usd
    intrinsic_usd(prices: Mapping[str, Price]) -> Usd    # max(e·(deliverable.value_usd − AEA), 0)
Quote(bid: Price, ask: Price)                             # 0 <= bid <= ask, ask > 0
```

The multiplier appears only in premiums and marks; payoff and settlement use deliverable and AEA. No `AEA == units × strike` assertion (F05: units 50, AEA 6000, multiplier 100). SPXW: `{SPX: 100}`, AEA = 100·K. WP2's `ContractVersion` wraps `ContractTerms` with provenance, underlying ID and effective/knowledge intervals.

## 4. Ingestion — `load_strategy(raw: bytes) -> ValidatedStrategy`, raises `SpecRejected(issues)`

`Issue(code: ErrorCode, message: str, json_pointer: str, retriable: bool = False, missing_capability: str | None = None, affected_interval: str | None = None, remediation: str | None = None)` is the §15.2 shape. All issues are reported, sorted by (pointer, code).

1. **Limits/encoding.** > 64 KiB ⇒ `RESOURCE_LIMIT`. Invalid strict UTF-8 or a BOM ⇒ `MALFORMED_JSON`.
2. **Parse.** `json.loads(text, parse_float=Decimal, parse_constant=<sentinel>, object_pairs_hook=list)`. `JSONDecodeError` ⇒ `MALFORMED_JSON` with line/column; `RecursionError` ⇒ `RESOURCE_LIMIT` (`raise … from e`).
3. **Tree walk** (`strict_json.freeze`, depth ≤ 16 else `RESOURCE_LIMIT`): repeated key ⇒ `DUPLICATE_KEY` at its RFC 6901 pointer; sentinel ⇒ `NONFINITE_NUMBER`; arrays become tuples; `-0` ⇒ `0`.
4. **Version gate.** `schema_version` must be `int` and `== 1` (not `True`, `1.0`, `"1"`); otherwise one `UNSUPPORTED_SCHEMA_VERSION` at `/schema_version` and nothing else.
5. **`StrategySpec.model_validate(tree)`**, `ConfigDict(strict=True, extra="forbid", frozen=True)`, one field per schema field. Verified on pydantic 2.12.5: strict `Literal[1]` accepts `True`, `1.0`, `Decimal("1.0")`, so every integer constant (`schema_version`, `ratio`, `max_concurrent_campaigns`, `fill_attempts`) is `Annotated[int, Field(ge=k, le=k)]`; strict `Decimal` rejects `int`, so `number` fields use a `BeforeValidator` accepting `int | Decimal` only; decimal strings must `fullmatch` the schema regex, then become `Usd` (`initial_cash_usd`, `price_allowance_usd`), `Price` (`max_absolute_spread_price_units`) or signed `Decimal` (`offset_price_units`). `oneOf` groups use a callable `Discriminator` on `method`/`frequency`/`mode` with `Tag("oneOf:<value>")`. `uniqueItems` and `target_delta != 0` are field validators.
6. **Pointers.** Walk the error `loc`, drop `oneOf:*` segments, escape per RFC 6901. `extra_forbidden` ⇒ `UNKNOWN_FIELD`; `literal_error` at `/structure`, `/product/family`, `/account/policy` ⇒ `UNSUPPORTED_STRUCTURE`, `UNSUPPORTED_PRODUCT`, `UNSUPPORTED_ACCOUNT_STATE`; tag errors point at `…/method`; else `SCHEMA_VIOLATION`.
7. **Semantic checks.** `check_strategy(spec) -> CheckResult(issues, leg_order, premium_direction)`. `ValidatedStrategy(spec, leg_order: tuple[str, ...], premium_direction: PremiumDirection | None)` re-runs the checks in `__post_init__`, so an unchecked spec cannot exist as this type.

Codes added beyond §15.2's non-exhaustive list: `MALFORMED_JSON`, `DUPLICATE_KEY`, `NONFINITE_NUMBER`, `UNKNOWN_FIELD`, `SCHEMA_VIOLATION`, `UNSUPPORTED_SCHEMA_VERSION`, `INVALID_STRATEGY_RULE`.

| §9.1 | Static in WP1 (code) | Deferred (needs reference/market data) |
|---|---|---|
| 1 | roots ⊆ {SPXW, XSP}; `SPX` named AM-settled (`UNSUPPORTED_PRODUCT`); exactly one `target_dte` leg, all others `same_as`-chained to it (`INVALID_SELECTOR`) | family/settlement from `ProductRules`, root→underlying (WP2) |
| 2 | put delta < 0, call delta > 0, no absolute-value conversion (`INVALID_SELECTOR`) | dollar-delta normalization (WP2/3) |
| 3 | unique leg ids; anchors exist, not self; **leg-level** anchor graph acyclic (a leg is resolved whole per §9.2 step 4) ⇒ `leg_order`; opposite-side same-type legs at net offset 0 (`INVALID_SELECTOR`) | two legs resolving to one contract (WP3) |
| 4 | leg count/sides/types per structure; strike order wherever an offset chain fixes it (condor lp<sp<sc<lc, strangle put<call); straddle requires a zero-offset anchor (`UNSUPPORTED_STRUCTURE`) | independently selected strike order; offset strike existence, item 6 (WP3) |
| 5, 9 | `min_dte ≤ target_dte ≤ max_dte`; `max_dte > exit_dte` and, if rolling, `> trigger_dte` — a window that can never qualify is `INVALID_STRATEGY_RULE`; a partly qualifying one stays per-candidate | last tradable time, product-local DTE (WP2/3) |
| 7 | premium direction fixed for `single_long` (debit), `iron_condor` (credit), straddle/strangle (debit); vertical from offset sign, else from same-method selectors (larger \|delta\| or pricier moneyness marks the expensive leg), else from exit bases; both exit bases must equal it; undeterminable ⇒ `INVALID_STRATEGY_RULE` (§10.3 needs a declared direction) | fill direction (WP3, C37) |
| 8, 9 | `initial_cash_usd > 0`; campaign fraction ≤ total fraction; `fixed_contracts.contracts ≤ max_contracts_per_order`; `max_absolute_spread_price_units > 0` (`INVALID_STRATEGY_RULE`). `risk_budget.max_contracts` above the order cap is not an error: WP3 takes the minimum | capital vs package risk (WP3) |
| 10 | `min_open_interest == 0` (`DATA_ENTITLEMENT_MISSING`); sequential roll ⇒ `trigger_dte > exit_dte` (`INVALID_STRATEGY_RULE`) | session counting, final-session entry ban (WP3) |

Policy IDs are pattern-checked only; WP3's registries bind them. `StrategySpec` (Pydantic) is also the internal read-only value — a deliberate exception to "Pydantic only at boundaries": a dataclass copy would duplicate ~200 lines for no safety.

## 5. Ledger (`models/ledger.py`, `engine/*`)

**Records.** `AccountKind(StrEnum)`: CASH, CAPITAL, RECEIVABLE, PAYABLE, OPTION_COST, STOCK_COST, REALIZED_PNL, FEES. `AccountKey(kind, ref: str)`: ref is `""` for CASH/CAPITAL, ISO settle date for RECEIVABLE/PAYABLE, `contract_id` for OPTION_COST, `asset_id` for STOCK_COST, instrument id for REALIZED_PNL, component id for FEES. Financing, dividend and revaluation kinds are added with their first poster.

```python
Posting(account: AccountKey, amount: Usd)                   # debit positive; each entry sums to 0
Lot(lot_id: str, instrument_id: str, quantity: int, unit_cost: Usd >= 0, campaign_id: str | None, opened_at_ns: int)
LotRelief(lot_id: str, quantity: int, cost: Usd)             # cost = |quantity| × unit_cost, exact
QuantityEvent(instrument_id: str, kind: QuantityKind, delta: int, opened: Lot | None, reliefs: tuple[LotRelief, ...])
    # QuantityKind: DEPOSIT OPEN CLOSE EXERCISE ASSIGNMENT DELIVERY EXPIRATION ADJUST_OUT ADJUST_IN
FeeLine(component_id: str, event: FeeEvent, contracts: int, rate: Usd, amount: Usd)
LegFill(terms: ContractTerms, contracts: int, price: Price)  # contracts signed: + buy, − sell
LedgerEntry(event_id: str, sequence: int, kind: EntryKind, at_ns: int, campaign_id: str | None,
            postings: tuple[Posting, ...], quantity_events: tuple[QuantityEvent, ...],
            contracts: tuple[ContractTerms, ...], fee_lines: tuple[FeeLine, ...], input_refs: tuple[str, ...])
LedgerState(entry_count: int, last_at_ns: int, balances: Mapping[AccountKey, Usd],
            lots: Mapping[str, tuple[Lot, ...]], contracts: Mapping[str, ContractTerms], retired: frozenset[str])
AssumedFlatFeeSchedule(schedule_id: str, trade_per_contract: Usd, exercise_assignment_per_contract: Usd,
                       cash_settlement_per_contract: Usd)   # whole cents >= 0; cost_basis == "assumed_schedule"
```

Postings are merged per account and sorted (canonical form for WP5 hashing). Balance signs: CASH, RECEIVABLE, OPTION_COST, STOCK_COST positive when held (a short lot's OPTION_COST is `quantity × unit_cost < 0`); PAYABLE ≤ 0; CAPITAL ≤ 0; REALIZED_PNL < 0 for gains; FEES ≥ 0. Views negate the equity side: `capital = −bal(CAPITAL)`, `realized = −bal(REALIZED_PNL)`.

**The one transition.** `apply_entry(state, entry) -> LedgerState` (pure) raises `LedgerInvariantError` unless: `sequence == entry_count + 1`; `at_ns ≥ last_at_ns`; every posting nonzero, ≤ 9 dp, < 1e19, whole cents on CASH/RECEIVABLE/PAYABLE; postings sum to exactly zero; RECEIVABLE ≥ 0 and PAYABLE ≤ 0 afterwards; a registered `contract_id` carries equal terms; no event touches a retired contract (`SettledOnce`); every `reliefs` equals a recomputed `fifo_relief(lots, delta)`; no lot crosses zero; `Δbal(OPTION_COST[c]) == Δ Σ quantity × unit_cost` over c's lots (same for STOCK_COST), so the monetary and quantity journals cannot disagree. `Journal` (the only mutable object; state in an instance list) does `commit(entry)`: event_id unique, `apply_entry`, append. `replay(entries) -> LedgerState` folds the same function from `LedgerState.empty()`.

**Posting functions** (pure; `state` supplies sequence, registered terms and lots):

```python
book_deposit(*, event_id, at_ns, cash: Usd, stock: tuple[Lot, ...] = ()) -> LedgerEntry     # sequence 1 only
book_option_trade(state, *, event_id, at_ns, campaign_id: str, legs: tuple[LegFill, ...],
                  fees: tuple[FeeLine, ...], settles_on: date) -> LedgerEntry
book_settle_due(state, *, event_id, at_ns, through: date) -> LedgerEntry | None
book_cash_settlement(state, *, event_id, at_ns, contract_id: str, settlement: Mapping[str, Price],
                     fees: tuple[FeeLine, ...], settles_on: date) -> LedgerEntry
book_physical_exercise(state, *, event_id, at_ns, contract_id: str, contracts: int > 0,
                       fees: tuple[FeeLine, ...], settles_on: date) -> LedgerEntry
book_deliverable_adjustment(state, *, event_id, at_ns, old_contract_id: str, new_terms: ContractTerms,
                            action_ref: str) -> LedgerEntry
trade_fees(schedule, legs) -> tuple[FeeLine, ...];  lifecycle_fees(schedule, event: FeeEvent, contracts: int) -> tuple[FeeLine, ...]
```

Zero amounts post nothing. Per leg or lot: cash side (`−t` on RECEIVABLE/PAYABLE), cost side (`−relieved signed cost`, `+opened cost` on OPTION_COST/STOCK_COST), REALIZED_PNL as the balancing amount.

- **Deposit:** CASH +c / CAPITAL −c; stock lots at their stated unit cost (F03: STOCK_COST +9000 / CAPITAL −9000).
- **Trade:** `t_i = premium_usd(price_i, contracts_i)`; the package nets to one RECEIVABLE (Σt < 0) or PAYABLE per entry (`PostDebit` in TLA+); fees are separate FEES / PAYABLE[settles_on] lines. Closing quantity relieves FIFO within the contract; the remainder opens a lot at `unit_cost = multiplier × price`. F01 entry: RECEIVABLE +90, OPTION_COST[P100] −200, OPTION_COST[P95] +110, FEES +2, PAYABLE −2. F01 exit: PAYABLE −80, OPTION_COST[P100] +200, REALIZED_PNL[P100] −80, OPTION_COST[P95] −110, REALIZED_PNL[P95] +70, FEES +2, PAYABLE −2.
- **Settle due:** every RECEIVABLE/PAYABLE dated ≤ `through` moves to CASH; `None` when nothing is due. Fixture cash values are observed after this.
- **Cash settlement:** requires `SettlementType.CASH`; one contract per entry; each lot posts `quantity × intrinsic_usd(settlement)` to RECEIVABLE/PAYABLE[settles_on], relieves its cost, then the contract is retired. F04: PAYABLE −300 / OPTION_COST +200 / REALIZED_PNL +100 (short); OPTION_COST −110 / REALIZED_PNL +110 (long).
- **Physical exercise/assignment** (ledger level only; *when* is WP7): s = position sign, e = ±1, n contracts. Shares change `s·e·units·n`, cash `−s·e·AEA·n`, reproducing every row of the §12.2 table. Delivered shares relieve held STOCK lots FIFO; delivering more than held raises `UnsupportedLifecycle(UNSUPPORTED_ACCOUNT_STATE)` (short stock needs a borrow policy, §12.3). Acquired stock opens a lot at `AEA/units`. The option's cost is realized separately (F02: PAYABLE −10000, STOCK_COST +10000, OPTION_COST +200, REALIZED_PNL −200). WP1 supports single-component, zero-cash, integral-share deliverables only.
- **Deliverable adjustment:** `new_terms` keeps multiplier, option type, style, settlement and expiry and uses a new `contract_id`, else `UnsupportedLifecycle(UNSUPPORTED_CORPORATE_ACTION)`. Lots move one-for-one keeping unit cost, campaign and open time (`ADJUST_OUT`/`ADJUST_IN`); OPTION_COST moves with them; the old contract is retired. F05: intrinsic 1000 before and after; `q × m × mark` 1000 before and after.

**Views** (pure):

```python
value_account(state, quotes: Mapping[str, Quote], stock_prices: Mapping[str, Price], basis: MarkBasis) -> Valuation
    # MID | NATURAL (bid for longs, ask for shorts). Missing mark ⇒ MissingMarkError(instrument_ids), never zero.
    # nlv = CASH + ΣRECEIVABLE + ΣPAYABLE + Σ quantity × multiplier × mark + Σ shares × price. Reserves never enter.
net_pnl(valuation, state) -> Usd            # nlv − capital
reconcile(valuation, state) -> Usd          # nlv − (capital + realized − fees + unrealized); must be exactly 0
expiry_bounds(holdings: Sequence[tuple[ContractTerms, int]], stock_shares: int, entry_cash: Usd) -> ExpiryBounds
    # ExpiryBounds(min_value: Usd | None, max_value: Usd | None, upper_slope: Decimal, breakpoints: tuple[Price, ...])
campaign_encumbrances(state, schedule) -> Mapping[str, Encumbrance]    # Encumbrance(settlement: Usd, fee_provision: Usd)
funding_headroom(state, schedule) -> Usd    # CASH − Σ|PAYABLE| − Σ encumbrances; receivables never count
```

`expiry_bounds` requires one expiry, one `deliverable_id`, single-component deliverables; payoff `h·S + Σ q_i × intrinsic_i(S)` with `intrinsic` from deliverable/AEA (the design's `m_i × max(e(S−K), 0)` is the standard-contract case; F05 shows why the deliverable form is required); breakpoints `{0} ∪ {(AEA_i − cash_i)/units_i}`; `upper_slope = h + Σ_calls q_i × units_i`; negative slope ⇒ `min_value None`, positive ⇒ `max_value None`; `entry_cash` is the signed net cash received at entry (F01: −500 + 90 ⇒ max loss 410 before fees). Reserve per campaign = `max(0, −min payoff)` over that campaign's option lots with `entry_cash = 0` (credit never subtracted twice; F01 500; F02 physical short put 10000 = full AEA, §11.2) plus `trade_per_contract × Σ|q|` exit-fee provision; an unbounded campaign raises `UnsupportedLifecycle(UNSUPPORTED_ACCOUNT_STATE)`. Stock cover and stock encumbrance are WP7. Reserves are computed, never stored (`ReserveMatchesPosition` by construction). No netting between campaigns; no netting of receivables against payables across entries (the same-cycle netting §11.2 permits is a WP3 policy option that needs a TLA+ change first).

**Funding rule.** A fill is funded iff `funding_headroom(apply_entry(state, entry), schedule) >= 0`. On the post-fill state this is at once the opening rule, §11.2's closing rule (`close debit + fees <= cash − other payables − other encumbrances`; the released reserve is simply absent post-fill) and `R1Campaign.FillFunded`.

| `R1Campaign` | WP1 |
|---|---|
| cash / recv / pay | CASH / ΣRECEIVABLE / −ΣPAYABLE |
| reserve = W + Fee | Σ `campaign_encumbrances` |
| Funded(c, p, r) | `funding_headroom >= 0` |
| settledGens | `retired` |
| NLV, FillNeverRaisesNLV | `value_account(MID)`, journal property test |
| gen / basis | `campaign_id` on lots / `premium_direction` on `ValidatedStrategy` |

## 6. Tests (each written failing first, in import order)

`tests/contracts/` holds byte-exact copies of `strategy.schema.json` (sha256 `385ee006…7b5e`), `example-strategy.json` (`72983b66…8ae1`), `ledger-fixtures.json` (`3482c8d6…b351`) and `SHA256SUMS`. `test_contracts_digests` fails on any edit and, when `docs/design/…` exists locally, on drift from it (skipped otherwise: `docs/design/` is gitignored by owner decision, so the vendored copies are the versioned contract).

| ID | File | Asserts |
|---|---|---|
| C01 | `conformance/test_c01_ingestion_rejects.py` | nested duplicate-key pointer; NaN/±Infinity; unknown field; `true`/`1.0` for `ratio`, `contracts`, version; number for decimal string; version 2/`"1"`/`true`/missing; 64 KiB, depth 16, BOM, bad UTF-8; example strategy loads |
| C02, §9.1 | `unit/test_strategy_checks.py` | one test per table row and code: put +0.30, a↔b cycle, both-sell vertical, misordered condor, straddle without zero offset, `min_open_interest 1`, undetermined vertical; exact pointers |
| parity | `conformance/test_schema_parity.py` | mutated corpus: `jsonschema` 2020-12 verdict equals ours except the whitelisted `1.0`-integer delta |
| C10 | `conformance/test_c10_f01_credit_vertical.py` | 90 / 2 / 10088 / −105 / 9983 / 500 / 410 / 80 / 2 / 10006 / 10006 / 6; receivable absent from headroom before settlement |
| C11 | `conformance/test_c11_f02_assigned_put.py` | 10199; NLV 9999 at entry price; encumbrance 10000 not in NLV; −10000; 100 shares; 199; 9000; 0 options; 9199; −801 |
| C12 | `conformance/test_c12_f03_covered_call.py` | 9000 / 299 / +10000 / 0 shares / 0 options / 10299 / 10299 / 1299; no negative stock lot; over-delivery raises |
| C13 | `conformance/test_c13_f04_cash_settlement.py` | 10088 / −300 / 0 / 0 / 9788 / 9788 / −212; second settlement raises; no standalone "expiry P&L" posting |
| C14 | `conformance/test_c14_expiry_bounds.py` | naked call `min None` where strike-only evaluation is finite; covered call bounded; stock + short put minimum at S=0; condor; mixed expiry raises |
| C16 | `conformance/test_c16_f05_deliverable_transformation.py` | 5000 / 5000 / 1000 / 1000 / 1000 / 1000; multiplier unchanged; expiry change raises |
| C33 | `conformance/test_c33_cost_monotonicity.py` | Hypothesis (`derandomize=True, database=None`): same fill sequence with buys ≥ / sells ≤ baseline or fees ≥ baseline ⇒ `net_pnl <=` baseline at identical marks |
| journal | `conformance/test_journal_properties.py` | Hypothesis entry sequences: every entry balances; trial balance 0; `replay(prefix) == incremental` at every prefix; `reconcile == 0`; money/quantity agreement; `FillNeverRaisesNLV` at the fill's own quotes; stale sequence, unbalanced, sub-cent cash and retired contract each raise |
| TLA | `conformance/test_r1_funding_traces.py` | `pass-refusal` constants (multiplier 1, W 2, cash 4, fee 1): close ask 4 ⇒ headroom < 0 (refuse), later settlement keeps ≥ 0; the `fail-unfunded-close` twin goes negative without the gate |
| units | `unit/test_money.py`, `test_money_type_safety.py`, `test_market.py`, `test_strict_json.py`, `test_positions.py`, `test_fees.py` | constructor guards, `Inexact` on 1/3, `-0`; `mypy.api` on `type_misuse_cases.py` expects `[operator]`/`[arg-type]`; intrinsic per §12.2 rows; FIFO determinism |

## 7. WP3 seams (named now, not built)

- `load_strategy(raw) -> ValidatedStrategy`: `leg_order` feeds `ContractSelector`; `premium_direction` feeds the fill-direction gate (`BasisPositive`).
- `ExecutionModel.try_fill(...) -> Fill(legs: tuple[LegFill, ...]) | Nonfill` → `trade_fees` → `book_option_trade` → `apply_entry` preview → `funding_headroom` (WP3 subtracts pending-order encumbrances) → `Journal.commit`, or `Nonfill(INSUFFICIENT_CAPITAL)`.
- `Lifecycle.apply(event, state, policy)` dispatches to `book_settle_due`/`book_cash_settlement` (WP3) and `book_physical_exercise`/`book_deliverable_adjustment` (WP7); each already returns the §17.1 shape.
- `value_account(NATURAL)` for exit-rule liquidation P&L and snapshots; `MissingMarkError` ⇒ run `invalid`.
- `expiry_bounds` for `risk_budget` sizing and mark-range findings; `campaign_encumbrances` for `AccountPolicy.assess` (`fully_funded_v1`).
- `replay` for C34; canonical `LedgerEntry` postings for `run_digest` (WP5).
- WP2 supplies `ContractVersion`, a `Quote` per valid observation, settlement dates and values, `ProductRules` rounding.

Nothing in WP1 owns a clock, calendar, provider, id generator or campaign state machine; all are arguments.

## 8. Dependencies and tooling

`requires-python = ">=3.12"`. Runtime: `pydantic>=2.12.5,<3` only. `[dependency-groups] dev`: `pytest>=9.0.3`, `pytest-cov>=7.0.0`, `hypothesis>=6.160` (new to the repo, dev-only), `jsonschema>=4.26.0` (parity oracle), `mypy>=1.19.1`, `ruff>=0.15.7`. `[tool.uv] exclude-newer = "7 days"`. Exact versions live in `uv.lock`, the reproducibility record (§17.1); at writing the resolver lands on pydantic 2.13.5, hypothesis 6.168.0, mypy 2.3.x, ruff 0.16.x, while siblings are locked on pydantic 2.12.5 — C01 pins the strict-mode traps so a resolver bump cannot change behavior silently. Ruff and mypy blocks copied from `backtest-server` (`strict`, pydantic plugin, `warn_unused_ignores`; add `mypy_path = "src"`, `explicit_package_bases = true`); hatch `packages = ["src/options_backtest"]`; pytest `testpaths = ["tests"]`, `filterwarnings = ["error"]`, `addopts = "--cov=options_backtest --cov-branch --cov-fail-under=90"`. Completion gate inside the service: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run pytest`, then both repo scripts.

## 9. Scope and procurement status (design §20 step 2)

- **Product:** roots SPXW and XSP on the SPX index; family `us_european_pm_cash_index`; European PM cash settlement on Cboe's official SPX close (XSP one tenth, stored rounding rule); structures `single_long`, `vertical`, `iron_condor`, `long_straddle`, `long_strangle`; `scheduled_daily_v1`; one campaign; DTE ≥ 7; `SPX` root rejected.
- **Data:** Massive only — Options Advanced (Individual, $199/mo) and Indices Starter (Individual, $49/mo); free Cboe/Treasury/NY Fed publications; one-time Cboe DataShop sample as cross-check; fidelity starts `historical_snapshot`; R1 window ≈ 2023-03-09 onward.
- **License:** Individual, owner-only; local single-user deployment only; hosted/multi-tenant reported unavailable.
- **Account and costs:** `fully_funded_v1`; `cost_basis = assumed_schedule` (illustrative flat $1.00 per contract side, $0 exercise/assignment/settlement); zero-interest financing named as a scenario.
- **Unresolved owner items** (none blocks WP1; all block WP0/WP2): (1) upgrade the Massive key to Options Advanced + Indices Starter before any WP0 probe; (2) obtain Massive's written confirmation that §5(d) permits personal historical backtesting under the Individual license; (3) approve the one-time DataShop quote-interval sample purchase.

## 10. Deliberately excluded from WP1

MCP server/tools; `config.py`; `logging_config.py`/structlog; Dockerfile/compose; providers, data, manifests, as-of views; `ProductRules`, calendars; selection; order state machine; simulator; exercise/assignment *timing*; stock encumbrance and short stock; dividends; financing; metrics; research; control DB; run/result/experiment models; `Usd × Decimal` and rounding helpers; cross-entry receivable/payable netting; multi-component or cash deliverables; stock bid/ask marks.

## 11. Engineering decisions recorded (not owner questions)

Vendored contracts replace the design's §2 gitignore exception (owner: "gitignore is intentional"). `1.0` is rejected for integers (stricter than JSON Schema; whitelisted in parity). Vertical direction is derived or the spec is rejected. Leg-level acyclicity. Straddle needs a zero offset. Never-qualifying DTE windows are rejected; partly qualifying ones are per-candidate. `risk_budget` cap is `min(max_contracts, max_contracts_per_order)` in WP3. Policy IDs pattern-only until WP3. Sub-cent cash raises; rounding is a WP2/WP3 policy. Assigned or delivered stock carries AEA as unit cost with the option premium realized separately — NLV is identical under a basis-adjusting convention; the attribution difference goes to the financial reviewer at WP6 with the R1 evidence bundle.

Amendment 2026-09-24 (WP1 impl): `book_cash_settlement` settles a package in one entry — `contract_id: str` becomes `contract_ids: tuple[str, ...]`, which must be exactly the held `SettlementType.CASH` contracts of one campaign at one `expires_at_ns` (non-empty, distinct, registered, unretired; a partial, mixed-expiry or cross-campaign tuple raises `LedgerInvariantError`); the entry nets Σ quantity × `intrinsic_usd(settlement)` over every lot into one RECEIVABLE or PAYABLE[settles_on] (`PostDebit`, zero posts nothing), relieves each lot's cost with REALIZED_PNL per contract, carries one EXPIRATION `QuantityEvent` per contract, posts the caller's fees (computed over the package's Σ|quantity|) as FEES / PAYABLE[settles_on] lines, and retires every contract in the tuple; F04 is one entry, PAYABLE −300 / OPTION_COST[P100] +200 / REALIZED_PNL[P100] +100 / OPTION_COST[P95] −110 / REALIZED_PNL[P95] +110, a leg's settlement cash flow is −(ΔOPTION_COST + ΔREALIZED_PNL) of its contract, so every fixture number stands; the §6 TLA row now asserts headroom ≥ 0 after settlement at every level, deep in the money included — reason: `R1Campaign.Settle` extinguishes the whole position in one step and posts one net v ∈ 0..W, so per-contract entries with gross flows and uncounted receivables do not refine it: `funding_headroom` goes negative between the first leg's entry and the T+1 transfer whenever the long leg is in the money, and a long-leg-first order leaves a lone short reserving its full AEA, which breaks `FullyFunded` (design §11.2, "holds after every event") on the ledger while it holds in the model; netting inside one entry is the trade rule already adopted and needs no TLA+ change, netting across entries stays rejected.

Amendment 2026-09-24 (WP1 review): `apply_entry` checks expiry order on every contract an entry touches (its quantity events and `contracts`, as registered), whoever built the entry: a TRADE entry comes strictly before each contract's `expires_at_ns`, a CASH_SETTLEMENT entry at or after it, and a PHYSICAL_EXERCISE entry touching a European contract at or after it; otherwise `LedgerInvariantError` (`R1Campaign.NoPositionPastExpiry`) — reason: no posting function compared `at_ns` with the terms, so early settlement, early European exercise and trades in expired contracts (re-opening a flat same-expiry contract after its package settled) were accepted and then marked and reserved as live. WP3 still owns the stricter last-tradable time and the settlement cutoff (a late settlement is its invalid-run rule), and WP7 the at-expiry exercise and assignment processing; flat contracts are not retired, so `retired` keeps its §5 meaning. Test timelines settle on the contracts' synthetic expiry day; no fixture number changes.

Amendment 2026-09-24 (WP1 review): `apply_entry` adds four checks to the §5 list: the FEES postings equal the entry's `fee_lines` merged per component (fee lines are the §11.5 cost disclosure and part of WP5's entry hash, so they cannot contradict the postings); every lot an entry opens carries the entry's `campaign_id`, except by ADJUST_IN, which keeps its lot's campaign; an opened lot's `lot_id` is not already held in its instrument, so `LotRelief` evidence stays unambiguous; and after the entry no instrument other than a registered contract is held short (design §12.3), so neither an OPEN in an unregistered id nor overselling deposited stock is accepted. That a lot's `unit_cost` is `multiplier × fill price` stays the posting functions' job. `replay(entries)` commits through a fresh `Journal` instead of folding `apply_entry` from `LedgerState.empty()`, so a replayed journal is one `Journal.commit` would accept (a repeated `event_id` raises); the states it returns are unchanged.

Amendment 2026-09-24 (WP1 review): §4 step 7 reads: `ValidatedStrategy` re-runs `check_strategy` in `__post_init__`, and stages 1-6 hold because `StrategySpec` values come only from `model_validate`: every spec model's `model_copy`, and so `copy.replace`, refuses `update`, which pydantic applies without validation; `model_construct`, pydantic's trusted-data escape hatch, is never used on specs.

Amendment 2026-09-24 (WP1 review): §4 rows 8, 9 read `initial_cash_usd > 0` and whole cents: sub-cent initial cash is a request error (`INVALID_STRATEGY_RULE` at `/account/initial_cash_usd`, design §15.2), not a WP3 rounding policy, because the opening deposit posts it to CASH (§2). No other `Usd` spec field is posted to CASH.

Amendment 2026-09-24 (WP1 review): §4 step 5 reporting: a `minItems`/`maxItems` violation is reported at the array itself and suppresses that array's item errors (the count is judged on the input, before the items), and a `uniqueItems` violation is reported only when every item is valid; every other issue is still reported. JSON Schema 2020-12 reports both kinds together; C01 pins this behaviour.

Amendment 2026-09-24 (WP1 review): §7 WP3 seam: when `Lifecycle.apply` wires cash settlement, `book_cash_settlement` gains a keyword-only `settlement_ref: str` through its own amendment, naming the `SettlementObservation` (id and correction version) or the manifest entry that supplied each value in `settlement`; the entry stores it as `input_refs=(settlement_ref,)` and rejects a non-str or empty ref with `ValueError`, as `action_ref` is, and the test written first includes an all-out-of-the-money package, whose postings do not reveal the settlement value (design §12.1, §11.1). Replay is unaffected: postings are self-contained. `book_physical_exercise` gains no ref: the exercise entry uses no outside value, and exercise-decision provenance is WP7's to design.

Amendment 2026-09-24 (WP1 review): §1 layout as built: the import graph adds `models.ledger ← {fees, valuation} ← funding` (funding values stock through `valuation.stock_value`, which converts through `Deliverable.value_usd`, so §2 holds); `models/ledger.py` also holds the posting helpers `merge_postings`, `due_cash` (`PostDebit`), `leg_amounts` and `fee_amounts` (fees never netted), so the four booking modules share one implementation; `ingest.parse_spec` is the public entry for the syntactic stages 1-6, which the schema-parity oracle uses.

Amendment 2026-09-24 (WP1 review): §5's exit-fee provision reads `Σ_i |q_i| × max(trade_per_contract, lifecycle_i)` over a campaign's option lots, where `lifecycle_i` is `cash_settlement_per_contract` for a `SettlementType.CASH` contract and `exercise_assignment_per_contract` for a PHYSICAL one; `Encumbrance.fee_provision` is that sum — reason: design §11.2 reserves the maximum future contractual outflow and states that `FullyFunded` holds after every event, while `book_cash_settlement` and `book_physical_exercise` post the caller's lifecycle fees as an unreserved PAYABLE, so any schedule whose lifecycle rate exceeds its trade rate, which `AssumedFlatFeeSchedule` accepts and real broker schedules have, let a settlement drive headroom and then settled CASH negative (review probe: the pass-refusal constants with a $1.50 settlement fee end at CASH −1.00). A position ends by a trade close, by its lifecycle event, or partly by each, so the per-contract maximum bounds the fee of every path without counting both; `R1Campaign` has no settlement fee (`Settle` posts v alone), so the refinement table and every fixture stand under R1's $0 lifecycle schedule (§9) and no TLA+ change is needed, the ledger reserving at least what the model does. Capping the schedule at the trade rate was rejected as a product limit on realistic schedules, and deferring was rejected because the invariant is the ledger's, not WP3's; this is an engineering correction of the ADR's formula, not a change to the design's policy.

Amendment 2026-09-24 (WP1 review): §4 row 4 keeps strike order fixed only by selector targets (moneyness for any option type, |delta| for the same type) in the Deferred column, beside row 3's identical selectors: WP3 selection judges the resolved package (design §9.2 steps 3-4) and rejects an inverted or equal order there, so no such spec ever fills — reason: a target with `tolerance` fixes an interval, not a strike, and §9.2 step 4 searches the whole package within every leg's tolerance, so inverted targets with overlapping intervals can still resolve to a valid order and a strict target rejection would be a false rejection; only an offset chain fixes a relation exactly, which is why row 4's Static column stops there. Row 7 is not a precedent: it reads the spec's declared premium direction from same-method targets, and §10.3 enforces that declaration at fill (`BasisPositive`), whereas row 4's order is a market outcome. No identical-selector, interval-based or same-type-delta rule is added in WP1.

Amendment 2026-09-24 (WP1 review): §4 step 7 reads `ValidatedStrategy(spec, leg_order: tuple[str, ...], premium_direction: PremiumDirection)`; `CheckResult.premium_direction` stays `PremiumDirection | None` (None only beside an issue), and `load_strategy` narrows it once `check_strategy` reports no issues, raising `AssertionError` on a None that cannot occur rather than typing it into the seam — reason: row 7 and this section's "derived or the spec is rejected" make an undetermined direction a rejection, and `__post_init__` already refuses it at runtime, so the Optional type published a state the type cannot hold and would make WP3's fill-direction gate (§7, `BasisPositive`) handle a None that never arrives; the proof-carrying type carries the proof. Runtime behaviour is unchanged.

Amendment 2026-09-25 (WP3 T0): settlement_ref implemented as announced above.
