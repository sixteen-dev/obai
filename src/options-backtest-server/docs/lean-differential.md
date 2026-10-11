# LEAN differential report

ADR 0002 §12 step 6, as amended by §16 owner decision 3. Three golden scenarios, L1 = G01,
L2 = G02 and L3 = G05, run through our simulator (`tests/e2e/runner.py`). Their datasets are
exported to LEAN's data format (`tests/lean/export.py`), replayed natively in LEAN by
`tests/lean/replay/ReplayAlgorithm.cs` and reconciled by `tests/lean/reconcile.py`. A
comparison passes only if `LEAN − ours` equals exactly the difference its known mismatches
predict (0 when none applies). There is no tolerance.

Everything below comes from one run. The tables are the run's `$LEAN_REPORT_DIR/{L1,L2,L3}.json`
rendered row for row. The probe observations come from the same run's `replay.jsonl` files.

## This run

| | |
|---|---|
| Date | 2026-09-26, 08:00:43Z to 08:00:59Z |
| LEAN commit | `b1337938bacbdcdf7327ba4c6a10ffa89ffac403` (QuantConnect/Lean, "Fix market close for Cboe indices (#9830)", 2026-09-25) |
| LEAN build | `Launcher/QuantConnect.Lean.Launcher.csproj`, Debug, net10.0 |
| .NET SDK | `10.0.401` |
| Our code | Branch `feat/options-backtest-wp1` at `acba2e2`, plus the uncommitted ADR 0002 working tree. The artifact digests under [Provenance](#provenance) identify the engine output that was compared. |

```sh
LEAN_REPORT_DIR=<scratch dir> \
LEAN_ROOT=$HOME/.local/share/obai-lean/Lean \
DOTNET_ROOT=$HOME/.local/share/obai-lean/dotnet \
  uv run pytest --no-cov -m lean tests/lean -v
```

```
tests/lean/test_lean_differential.py::test_lean_replay_reconciles_exactly[L1] PASSED [ 16%]
tests/lean/test_lean_differential.py::test_lean_replay_reconciles_exactly[L2] PASSED [ 33%]
tests/lean/test_lean_differential.py::test_lean_replay_reconciles_exactly[L3] PASSED [ 50%]
tests/lean/test_lean_probes.py::test_echo_probe_every_fill_and_close_mark_equals_the_export PASSED [ 66%]
tests/lean/test_lean_probes.py::test_expiry_probe_a_cash_settled_package_settles_at_the_official_value PASSED [ 83%]
tests/lean/test_lean_probes.py::test_combo_limit_probe_is_strict_at_an_exact_limit PASSED [100%]

====================== 6 passed, 38 deselected in 15.81s =======================
```

## Result

| Scenario | Golden | Window | Comparisons | Exact (0 predicted) | Nonzero, predicted | Unexplained |
|---|---|---|---|---|---|---|
| L1 | G01 (`G01_credit_vertical_take_profit.toml`) | 2024-03-04 to 2024-03-06 | 34 | 30 | 4 | 0 |
| L2 | G02 (`G02_credit_vertical_settles_between_strikes.toml`) | 2024-03-04 to 2024-03-18 | 75 | 70 | 5 | 0 |
| L3 | G05 (`G05_exit_limit_missed_then_filled.toml`) | 2024-03-04 to 2024-03-07 | 41 | 37 | 4 | 0 |

The run made 150 comparisons. 137 were exactly equal. 13 differed, and each differed by exactly
the amount its mismatch predicts. None was unexplained. The 13 differences:

| Scenario | Check | At | Subject | Ours | LEAN | LEAN − ours | Predicted | M |
|---|---|---|---|---|---|---|---|---|
| L1 | settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L1 | settled_cash_after_fill | 2024-03-05:F1:4:1 | — | 10088.00 | 10006.00 | -82.00 | -82.00 | M2 |
| L1 | settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L1 | settled_cash_at_close | 2024-03-05 | — | 10088.00 | 10006.00 | -82.00 | -82.00 | M2 |
| L2 | settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L2 | settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L2 | mid_nlv | 2024-03-15 | — | 9788.00 | 10088.0000 | 300.0000 | 300.00 | M14 |
| L2 | exercise_time_s | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4895 | 1710561599 | 1710565200 | 3601 | 3601 | M14 |
| L2 | exercise_time_s | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4900 | 1710561599 | 1710565200 | 3601 | 3601 | M14 |
| L3 | settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L3 | settled_cash_after_fill | 2024-03-06:F1:4:1 | — | 10088.00 | 10006.00 | -82.00 | -82.00 | M2 |
| L3 | settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | 88.00 | M2 |
| L3 | settled_cash_at_close | 2024-03-06 | — | 10088.00 | 10006.00 | -82.00 | -82.00 | M2 |

## What this run validates

- **The export.** LEAN read our minute bars at the paths, price scales and time zones of
  `export.LEAN_FORMAT` (M12). Every fill time matches ours to the second, and every leg price
  equals the bid (sells) or ask (buys) at our instant. L2 spans the 2024-03-10 DST change.
- **Leg fill prices.** All 10 leg fills (L1 4, L2 2, L3 4) are at the natural price, which is
  the price at which LEAN fills a combo market order at that minute.
- **Fees per fill.** The replay uses a fee model written to match ours ($1 × |contracts|, $0 on
  exercise). This comparison therefore checks the contract count, not the fee schedule.
- **Cash and holdings.** Trade-date total cash matches after every fill and at every close,
  as do holdings per contract at every close and the final total cash.
- **Mid NLV.** Mid NLV matches LEAN's total portfolio value at every close, with one exception:
  L2's expiry day, where the difference is predicted (M14).
- **Expiry cash settlement (L2).** The run checks the number of exercises, the quantities, the
  index value LEAN exercised at, zero exercise fees, and settlement cash of −300.00. The short
  4900 P has 3.00 intrinsic × 100; the long 4895 P expires out of the money and pays 0.
- **L3's missed exit.** LEAN received a combo limit at our 0.80 debit limit, which was 0.05
  under the 0.85 market. It stayed live from our DEC to our F3 cancel and did not fill. Both
  legs were cancelled.

## What this run does not validate

- **Our decisions (M1).** LEAN decides nothing here. Our fills are replayed as combo *market*
  orders at our instants. Everything that chose those fills is ours and goes unchecked: entry
  and exit timing, strike and expiry selection, exit triggers, limit prices, retries,
  participation caps, all-or-none, quote-age and validity rules, and the campaign and roll
  logic.
- **Fills exactly at the limit (M6).** Every fill in L1–L3 is at a package debit equal to its
  limit (price allowance 0.00: entries at −90.00, exits at 80.00). The combo-limit probe shows
  that LEAN's `ComboLimit` does not fill at an exact limit. As limit orders, LEAN would have
  filled none of these. The differential cannot show that difference because it replays
  market orders.
- **Funding (M5).** The replay runs with `BuyingPowerModel.Null`. Encumbrance, headroom and
  `INSUFFICIENT_CAPITAL` are not compared.
- **The T+1 rule (M2).** LEAN settles option cash immediately. The only check is that our
  settled cash plus our own receivable and payable equals LEAN's cash, so our postings sum to
  the right amount. Whether T+1 is the correct rule is not tested.
- **The settlement value (M3).** The export writes our official value into the expiry day's
  16:00 SPX bar, and LEAN exercises at that bar. The run confirms the payoff arithmetic, not
  the value or where it comes from.
- **XSP (M9).** LEAN has no XSP, so G19 (the XSP settlement divisor) has no LEAN counterpart.
- **Assignment, American exercise, physical delivery (M10).** Assignment simulation is
  disabled. Only European cash-settled SPXW is replayed.
- **The M13 and M4 edge cases.** Neither was exercised at its boundary in this run. See the
  table below.
- **Pricing, IV, Greeks, features and selector deltas.** None of these is involved. The
  QuantLib reference tests in `tests/reference/` cover pricing.
- **Market realism.** The markets are synthetic, placed on real 2024 dates. LEAN confirms only
  that it reads what we exported.
- **Breadth.** The run covers three scenarios. Each is a 1-lot SPXW put credit vertical with
  one campaign, no rolls, no early close and no holiday. Invalid, incomplete, stale-quote and
  missing-data paths are covered only by the e2e goldens and unit tests: `replay_input` refuses
  any run that is not valid.

## Known mismatches M1–M14

The statements are ADR 0002 §12's, with M11 as amended by §16 decision 3.

| M | Mismatch | In this run |
|---|---|---|
| M1 | Fills are replayed at our instants. Timing, limits, retries, participation and all-or-none are ours. | In force for every fill: L1 2, L2 1, L3 2, which is 10 legs. It predicts no numeric difference. |
| M2 | LEAN settles immediately and we post T+1. Trade-date total cash is compared; our lag is asserted separately. | Exercised: 10 rows (L1 4, L2 2, L3 4), each ±(premium − fees) until the next session. |
| M3 | LEAN exercises at its last underlying price. The export sets the expiry day's 16:00 bar to the official value. | Exercised: L2 `settlement_value` ×2 (ours 4897.00, LEAN 4897), and the expiry probe (4997.50). The match holds by construction. |
| M4 | LEAN auto-exercises only at intrinsic ≥ 0.01. | Not exercised at the threshold. Every expiring leg had intrinsic 0 or ≥ 0.01: L2 had 3.00 and 0; the expiry probe had 2.50, 7.50 and 0. |
| M5 | Funding is not cross-checked. `INSUFFICIENT_CAPITAL` cannot be reproduced. | Not exercised, because nothing is compared. |
| M6 | `ComboLimit` is strict; we fill at `D == limit`. | Combo-limit probe only. The L1–L3 fills at `D == limit` are replayed as market orders, so this is never reached there. L3's diagnostic limit missed by 0.05, not at the boundary. |
| M7 | Total portfolio value equals our mid NLV only when every held leg has a 16:00 bar. | Holds at every close of L1–L3 except L2 2024-03-15 (M14). |
| M8 | LEAN's market-hours database governs; the data stays labeled synthetic. | In force. All dates are regular 2024 sessions. It predicts no numeric difference. |
| M9 | No XSP. | Not exercised. |
| M10 | Simulated assignment is disabled (`NullOptionAssignmentModel`). | In force. No European contract can be assigned early. |
| M11 | LEAN's C# API, with no Python interop (§16 decision 3). | In force: `ReplayAlgorithm.cs` and `ProbeAlgorithm.cs`. |
| M12 | LEAN reads SPX bars in America/Chicago and SPXW bars in America/New_York. The export converts through the zone rules. | Exercised at every fill time and every close, including across the 2024-03-10 DST change (L2). |
| M13 | LEAN marks a leg with an all-zero side at its other side (a NO_BID leg at its ask); we mark at the mid. | Not exercised as a nonzero prediction: no leg held at a CUT had a zero side at 16:00. L2's still-held legs on 2024-03-15 were both NO_BID at F3 (bid 0.00, ask 0.10), so LEAN marked each at 0.10 against a mid of 0.05. The long and short legs cancel, so this run cannot tell LEAN's ask mark from the mid. The formula is tested only against hand-written records in `tests/lean/test_reconcile.py`. |
| M14 | LEAN processes an SPXW expiry at 01:00 America/New_York the next calendar day and marks the still-held package at the F3 bars at 16:00. | Exercised in L2. Two `exercise_time_s` rows differ by +3601 s: our CUT is 23:59:59 EDT, LEAN's exercise is 01:00 EDT on 2024-03-16. `mid_nlv` on 2024-03-15 differs by +300.00: the package marked at F3 nets to 0, minus our settlement cash of −300.00. |

## Probes

`tests/lean/test_lean_probes.py` runs `tests/lean/probes/ProbeAlgorithm.cs` on the export of a
synthetic SPXW market: SPX at 5000 at every print, strikes 4990–5010, zero rates. Each probe
checks one fact about LEAN that the differential relies on.

| Probe | Fact asserted | Observed in this run | Result |
|---|---|---|---|
| echo | Every fill and 16:00 mark LEAN reports equals the export: sells at the bid, buys at the ask, marks at the mid, SPX at its print. Combo market orders fill in the submitting minute. There is no unsettled cash. | A 3-leg package ×2 opened 2024-03-04 15:46 (F1) and closed 2024-03-05 15:47 (F2), each in its own minute. Unsettled cash was 0.0 at every snapshot. Cash went 100000 → 111724.00 → 99168.00. | passed |
| expiry | A cash-settled package held to expiry settles at the official value in the expiry day's 16:00 SPX bar (M3). Each leg pays its intrinsic value and an out-of-the-money leg pays nothing (M4). Exercise has no fee. | SPX settlement was pinned at 4997.50 for 2024-03-08, and LEAN's 16:00 SPX was 4997.5. `OptionExercise` fills came at 2024-03-09 01:00 New York, index 4997.5, fee 0. Cash moved +750.00 (4990 C) and −250.00 (5000 P); the 4995 P paid 0. End cash was 96222.00. | passed |
| combo-limit | `ComboLimitFill` is strict (M6): a limit exactly at the natural price never fills; one tick through it fills in the next minute. | A −0.90 limit at the natural −0.90 (2024-03-04 DEC) was cancelled at F3 with no fill. A −0.85 limit submitted 2024-03-05 15:45 filled at 15:46 at 2.00 / 1.10. | passed |

## Run notes

- **Test order.** pytest collects `test_lean_differential.py` before `test_lean_probes.py`, so
  in this run the scenarios ran before the probes. ADR 0002 §12 step 3 and the probe module's
  docstring both say the probes run first. The suite fails if any test fails, so this run's
  verdict does not depend on the order. But a failing probe does not stop the scenarios from
  running. This order was not changed here.
- **LEAN's failed data requests.** Every LEAN run logged requests for data the export does not
  contain:
  - SPXW trade bars and open interest (`*_trade_european.zip`, `*_openinterest_european.zip`).
    We export quotes only.
  - `equity/usa/hour/spy.zip`, LEAN's default benchmark.

  LEAN filled and marked from the quote bars, which the echo probe asserts. No compared value
  depends on the missing files.
- **Export scope.** The export covers each whole synthetic dataset: 19 SPX sessions, and 10
  SPXW sessions up to the 2024-03-15 expiry. LEAN runs only the account curve's window.

## Provenance

The dataset `manifest_id` and our artifact digests per scenario. "= L1" means the digest equals
L1's.

| | L1 | L2 | L3 |
|---|---|---|---|
| manifest_id | `003c4d470c5a5277cab57e02371a3f292d5a06228d86ad7bf576bc5c305d0683` | `1d1743e603ec9bc27a8aced6b58e42c572bfb108a687ef5b6a1bd25add6a6c4f` | `37e590e7afb218322251d5851842dd4ef1461ea1110fd69f45af28f8d8074cc3` |
| events | `0d3fde9bd863d1f437769da357c02da8b0d7c77f89ba04f2ab6a0edbfbf00f09` | `c231030993973790e7ca816a04296532b80c1efb7232f2764f8c79db86fb6d59` | `156ba659f53bd873f275e265353cd4dc745342ca98d9f2515145f6c90f671f0b` |
| journal | `29d56f5078b81dd2088ce0ec6b1d4963d0ed092b16ebfe6508fa5b67a005a57e` | `7a408679e8970ae1519c2d2e189410647d7f4005c64668bdfbe5e25897a914a0` | `f9d706dd1c81de3263f39b29b61d22882333bc9841fc3fd1ac5bc6359ade5826` |
| positions | `16435f26fafc19a3da0a06a37da35b46a084580a9f711a8d126d053b66ccf247` | `65454ede4de3db79f0a10c0a525333eb442bb2e9f08b66d7d4ede1ca7fe98b6d` | `a6300cf2f89b4628e7800ccf0fe68cd677dda58f242c610883e31982c07a8ae2` |
| account_curve | `145fb402c29238eb41e72b6c36db36d3f13d00966c5ab8944f5327d72d421a51` | `59f71dc3ce654137bf714c742fb64a06561167261af7cb24a3f4e025ffec4e58` | `01e670c7d2eb3a4d5a64182ccbf963a350c65cf445fd8d28e1a8075d8399ea18` |
| campaigns | `c1d0db785de83d726c80f52e2c8663c30f6ae7fa0377767501cdeb821ac31548` | `485186a027b75208cd844fe504b424d780ebc11d6edaa248e3468183ba5c8201` | `47d057d489c083e64c083bb8054b6b4ea3a3617f3a11a07a1503c1e4326188ff` |
| candidate_decisions | `4a41e34aed3ee45ec032a1469803067136ddbe418a372cb3996db060c0b911e1` | = L1 | = L1 |
| quality | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | = L1 | = L1 |

### Export digests

| Export file (sha256) | L1 | L2 | L3 |
|---|---|---|---|
| `index/usa/minute/spx/20240304_trade.zip` | `e7095bb954c80f1f3a173ff8012f7e12037a4d0ee17c185503f85f0519926df6` | = L1 | = L1 |
| `index/usa/minute/spx/20240305_trade.zip` | `01a4347237bad956cf62f8c4636814b914787c99953296bae00ef20160da37ce` | = L1 | = L1 |
| `index/usa/minute/spx/20240306_trade.zip` | `5a88f5dcfec2ac64baf489b5c1c5a359331a915f09cf683c3ffc3d488fdcd3c3` | = L1 | = L1 |
| `index/usa/minute/spx/20240307_trade.zip` | `560966c05e321024c7d33d036276b10d048918144dffbb9efd73608182b65b1d` | = L1 | = L1 |
| `index/usa/minute/spx/20240308_trade.zip` | `a2b2e845cec6841b7cbb569c3d38943c2731c63e95846a2f639d5eda44178416` | = L1 | = L1 |
| `index/usa/minute/spx/20240311_trade.zip` | `dc236fce7ef0fc6a64986a122e1a2e204720693da6b5c16ed287690c2144a84d` | = L1 | = L1 |
| `index/usa/minute/spx/20240312_trade.zip` | `796b7de1a72cd071e7082082492329661f8958127a5adbb2961693ed03e10d9c` | = L1 | = L1 |
| `index/usa/minute/spx/20240313_trade.zip` | `c265ff426c42f03baebd73315e0f131a356661a5ab14f1500813c65b7f96bdfb` | = L1 | = L1 |
| `index/usa/minute/spx/20240314_trade.zip` | `3c13a9f4489f18cfc0d8645638f72d4be81483e895b4b36c75abb77d3c688a2f` | = L1 | = L1 |
| `index/usa/minute/spx/20240315_trade.zip` | `94f99f9da404715db631c12dcf6e7639e3beaedb252cf03d465fbd5321abd9c6` | `ed1783039e19b5e7b0664e94caa9bf432c88483f151ae398ae2f6f00c75e660a` | = L1 |
| `index/usa/minute/spx/20240318_trade.zip` | `527faa02cf2dcae6848cec3232e45d2fde133ac24117e76baf52be0467601976` | = L1 | = L1 |
| `index/usa/minute/spx/20240319_trade.zip` | `f019e6de6d213b634aca9cce5148664e59a49a4fa3269cd43da5f1c6b3953fbb` | = L1 | = L1 |
| `index/usa/minute/spx/20240320_trade.zip` | `18245952c4263fd408295eb5d6f6a31a2376702587b6fbfea3d741b32a357c9c` | = L1 | = L1 |
| `index/usa/minute/spx/20240321_trade.zip` | `6c772b2409bff58b2e8027437738074eb844dd6baf1e1f4f73e84442e3cea731` | = L1 | = L1 |
| `index/usa/minute/spx/20240322_trade.zip` | `4a4ab02f49987608a127468bfc5d149289620b1d45f1d50ea225c64dc51c9f2d` | = L1 | = L1 |
| `index/usa/minute/spx/20240325_trade.zip` | `7f788e8b94ec3d62102355802e0ff265d0f969aa42a7c20f52995ab6b74a29d0` | = L1 | = L1 |
| `index/usa/minute/spx/20240326_trade.zip` | `c8be03e69eb3fd03b65e9a1c88b907b2c2db38298ad8edc4b8ced5d6b8cda389` | = L1 | = L1 |
| `index/usa/minute/spx/20240327_trade.zip` | `742685664469edd7eb4e5411a59159607fb245bc362e8238af17cab7900a367e` | = L1 | = L1 |
| `index/usa/minute/spx/20240328_trade.zip` | `1a408c74d0b24c01d00c4706c9dae5f809a39cddf06fe9a84f2f2280b2de977a` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240304_quote_european.zip` | `29760fef2d886841d3459df47803496f7088a2c0a33aeeafe134fee78d240120` | `58d8342f4736ba4b9684a6ea203c8076c7166f13ec5628001485ce3052d3209a` | `58d8342f4736ba4b9684a6ea203c8076c7166f13ec5628001485ce3052d3209a` |
| `indexoption/usa/minute/spxw/20240305_quote_european.zip` | `3b1ea3f68d09eb3a318571378f84ef13c6b3dbb4736d1b1a8c067edc492bb564` | `55331d310183b00b0d77f0251631ead3ea74794da28ff967a3b4e02d4713ebea` | `3e85efa845d8fa1dd0e67a815b7dc4df311eceab198e488f9ef889c4d7928d45` |
| `indexoption/usa/minute/spxw/20240306_quote_european.zip` | `48398d667753af6ce30c947315286e2409a97a870292ba9507fd835c37eb6f24` | = L1 | `039c88809f595f3f59c1085bb964e4b9cc3e1bb80bb653f294a1952f5d097dfc` |
| `indexoption/usa/minute/spxw/20240307_quote_european.zip` | `bc46ac475455cc3b7cfd96a77ed3373bdc4c7a9adb9fe2022d62774e9205c20f` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240308_quote_european.zip` | `a2d3574ba064badbc7ec89d28aebd9c63fc0a2c54ce23938f9f20f0abc1c52da` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240311_quote_european.zip` | `f579156a79f55fbd112b8d615b19008c92c6dbc6fd35e0f1cbbd163f753b76cd` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240312_quote_european.zip` | `a5eec386be501b926569dbef3f22eddd597b82e1b60f95ea2e233ea8d9db5ff9` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240313_quote_european.zip` | `34701b6a56d784389bd903a311993e99e098fc77def6d441c8c08d28e99f59f7` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240314_quote_european.zip` | `4686e4ac9d4ebf3f171606912b65aefb0adb01d0c387fdf36afb97821a6b375f` | = L1 | = L1 |
| `indexoption/usa/minute/spxw/20240315_quote_european.zip` | `366a8151e17aa99cb0384f65f8fd689d94cb44882651b386619f36527ab104f5` | `2e062ec5fd70157027ad0d963acd0b3650a93914734ae7bb4bdbacaadde176de` | = L1 |

## L1 = G01

`tests/e2e/scenarios/G01_credit_vertical_take_profit.toml`, window 2024-03-04 to 2024-03-06.

| Check | At | Subject | Ours | LEAN | Predicted LEAN − ours | M | Verdict |
|---|---|---|---|---|---|---|---|
| fill_count | 2024-03-04:F1:4:1 | — | 2 | 2 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 2.00 | 2 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1.10 | 1.1 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| fill_fees | 2024-03-04:F1:4:1 | — | 2.00 | 2 | 0 | — | exact |
| total_cash_after_fill | 2024-03-04:F1:4:1 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| fill_count | 2024-03-05:F1:4:1 | — | 2 | 2 | 0 | — | exact |
| fill_time_s | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4900 | 1709671560 | 1709671560 | 0 | — | exact |
| fill_price | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4900 | 1.20 | 1.2 | 0 | — | exact |
| fill_quantity | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4900 | 1 | 1 | 0 | — | exact |
| fill_time_s | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4895 | 1709671560 | 1709671560 | 0 | — | exact |
| fill_price | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4895 | 0.40 | 0.4 | 0 | — | exact |
| fill_quantity | 2024-03-05:F1:4:1 | SPXW:2024-03-15:P:4895 | -1 | -1 | 0 | — | exact |
| fill_fees | 2024-03-05:F1:4:1 | — | 2.00 | 2 | 0 | — | exact |
| total_cash_after_fill | 2024-03-05:F1:4:1 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_after_fill | 2024-03-05:F1:4:1 | — | 10088.00 | 10006.00 | -82.00 | M2 | predicted |
| total_cash_at_close | 2024-03-04 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-04 | — | 9983.00 | 9983.0000 | 0.00 | — | exact |
| total_cash_at_close | 2024-03-05 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-05 | — | 10088.00 | 10006.00 | -82.00 | M2 | predicted |
| mid_nlv | 2024-03-05 | — | 10006.00 | 10006.00 | 0 | — | exact |
| total_cash_at_close | 2024-03-06 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-06 | — | 10006.00 | 10006.00 | 0 | — | exact |
| mid_nlv | 2024-03-06 | — | 10006.00 | 10006.00 | 0 | — | exact |
| unmatched_fills | run | — | 0 | 0 | 0 | — | exact |
| unexecuted_actions | run | — | 0 | 0 | 0 | — | exact |
| final_total_cash | run | — | 10006.00 | 10006.00 | 0 | — | exact |

## L2 = G02

`tests/e2e/scenarios/G02_credit_vertical_settles_between_strikes.toml`, window 2024-03-04 to 2024-03-18.

| Check | At | Subject | Ours | LEAN | Predicted LEAN − ours | M | Verdict |
|---|---|---|---|---|---|---|---|
| fill_count | 2024-03-04:F1:4:1 | — | 2 | 2 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 2.00 | 2 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1.10 | 1.1 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| fill_fees | 2024-03-04:F1:4:1 | — | 2.00 | 2 | 0 | — | exact |
| total_cash_after_fill | 2024-03-04:F1:4:1 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| total_cash_at_close | 2024-03-04 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-04 | — | 9958.000 | 9958.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-05 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-05 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-05 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-05 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-05 | — | 9963.000 | 9963.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-06 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-06 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-06 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-06 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-06 | — | 9970.500 | 9970.5000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-07 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-07 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-07 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-07 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-07 | — | 9978.00 | 9978.0000 | 0.00 | — | exact |
| total_cash_at_close | 2024-03-08 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-08 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-08 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-08 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-08 | — | 9985.500 | 9985.5000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-11 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-11 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-11 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-11 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-11 | — | 10018.00 | 10018.0000 | 0.00 | — | exact |
| total_cash_at_close | 2024-03-12 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-12 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-12 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-12 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-12 | — | 10033.00 | 10033.0000 | 0.00 | — | exact |
| total_cash_at_close | 2024-03-13 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-13 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-13 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-13 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-13 | — | 10058.000 | 10058.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-14 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-14 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-14 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-14 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-14 | — | 10078.000 | 10078.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-15 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-15 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-15 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-15 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-15 | — | 9788.00 | 10088.0000 | 300.00 | M14 | predicted |
| total_cash_at_close | 2024-03-18 | — | 9788.00 | 9788.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-18 | — | 9788.00 | 9788.00 | 0 | — | exact |
| mid_nlv | 2024-03-18 | — | 9788.00 | 9788.00 | 0 | — | exact |
| exercise_count | 2024-03-15:CUT:6:1 | — | 2 | 2 | 0 | — | exact |
| exercise_time_s | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4895 | 1710561599 | 1710565200 | 3601 | M14 | predicted |
| exercise_quantity | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4895 | -1 | -1 | 0 | — | exact |
| settlement_value | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4895 | 4897.00 | 4897 | 0 | — | exact |
| exercise_time_s | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4900 | 1710561599 | 1710565200 | 3601 | M14 | predicted |
| exercise_quantity | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4900 | 1 | 1 | 0 | — | exact |
| settlement_value | 2024-03-15:CUT:6:1 | SPXW:2024-03-15:P:4900 | 4897.00 | 4897 | 0 | — | exact |
| settlement_fees | 2024-03-15:CUT:6:1 | — | 0.00 | 0 | 0 | — | exact |
| settlement_cash | 2024-03-15:CUT:6:1 | — | -300.00 | -300.00 | 0 | — | exact |
| unmatched_fills | run | — | 0 | 0 | 0 | — | exact |
| unexecuted_actions | run | — | 0 | 0 | 0 | — | exact |
| final_total_cash | run | — | 9788.00 | 9788.00 | 0 | — | exact |

## L3 = G05

`tests/e2e/scenarios/G05_exit_limit_missed_then_filled.toml`, window 2024-03-04 to 2024-03-07.

| Check | At | Subject | Ours | LEAN | Predicted LEAN − ours | M | Verdict |
|---|---|---|---|---|---|---|---|
| fill_count | 2024-03-04:F1:4:1 | — | 2 | 2 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | 2.00 | 2 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| fill_time_s | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1709585160 | 1709585160 | 0 | — | exact |
| fill_price | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1.10 | 1.1 | 0 | — | exact |
| fill_quantity | 2024-03-04:F1:4:1 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| fill_fees | 2024-03-04:F1:4:1 | — | 2.00 | 2 | 0 | — | exact |
| total_cash_after_fill | 2024-03-04:F1:4:1 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_after_fill | 2024-03-04:F1:4:1 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| fill_count | 2024-03-06:F1:4:1 | — | 2 | 2 | 0 | — | exact |
| fill_time_s | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4900 | 1709757960 | 1709757960 | 0 | — | exact |
| fill_price | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4900 | 1.20 | 1.2 | 0 | — | exact |
| fill_quantity | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4900 | 1 | 1 | 0 | — | exact |
| fill_time_s | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4895 | 1709757960 | 1709757960 | 0 | — | exact |
| fill_price | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4895 | 0.40 | 0.4 | 0 | — | exact |
| fill_quantity | 2024-03-06:F1:4:1 | SPXW:2024-03-15:P:4895 | -1 | -1 | 0 | — | exact |
| fill_fees | 2024-03-06:F1:4:1 | — | 2.00 | 2 | 0 | — | exact |
| total_cash_after_fill | 2024-03-06:F1:4:1 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_after_fill | 2024-03-06:F1:4:1 | — | 10088.00 | 10006.00 | -82.00 | M2 | predicted |
| total_cash_at_close | 2024-03-04 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-04 | — | 10000.00 | 10088.00 | 88.00 | M2 | predicted |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-04 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-04 | — | 9958.000 | 9958.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-05 | — | 10088.00 | 10088.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-05 | — | 10088.00 | 10088.00 | 0 | — | exact |
| holding | 2024-03-05 | SPXW:2024-03-15:P:4895 | 1 | 1 | 0 | — | exact |
| holding | 2024-03-05 | SPXW:2024-03-15:P:4900 | -1 | -1 | 0 | — | exact |
| mid_nlv | 2024-03-05 | — | 9963.000 | 9963.0000 | 0.000 | — | exact |
| total_cash_at_close | 2024-03-06 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-06 | — | 10088.00 | 10006.00 | -82.00 | M2 | predicted |
| mid_nlv | 2024-03-06 | — | 10006.00 | 10006.00 | 0 | — | exact |
| total_cash_at_close | 2024-03-07 | — | 10006.00 | 10006.00 | 0 | — | exact |
| settled_cash_at_close | 2024-03-07 | — | 10006.00 | 10006.00 | 0 | — | exact |
| mid_nlv | 2024-03-07 | — | 10006.00 | 10006.00 | 0 | — | exact |
| diagnostic_fills | o:2024-03-05:exit | — | 0 | 0 | 0 | — | exact |
| diagnostic_cancels | o:2024-03-05:exit | — | 2 | 2 | 0 | — | exact |
| unmatched_fills | run | — | 0 | 0 | 0 | — | exact |
| unexecuted_actions | run | — | 0 | 0 | 0 | — | exact |
| final_total_cash | run | — | 10006.00 | 10006.00 | 0 | — | exact |

## Rerun

1. Build the toolchain outside the repository at the pinned commit. The
   [README](../README.md#lean-differential-opt-in) has the steps.
2. From `src/options-backtest-server`:

   ```sh
   LEAN_REPORT_DIR=<dir> LEAN_ROOT=<LEAN checkout> DOTNET_ROOT=<.NET SDK dir> \
     uv run pytest --no-cov -m lean tests/lean -v
   ```

   Without `LEAN_ROOT`, a built launcher, or `dotnet`, the tests skip and give the reason.
3. `<dir>/{L1,L2,L3}.json` holds every row of the tables above. Replace this page whenever the
   engine, the export, the replay, the probes or LEAN changes. If a later run shows the same
   manifest ids and artifact and export digests, it saw the same inputs and outputs.
