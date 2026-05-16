---
entry_type: strategy
id: openap_pct_acc
canonical_name: Percent Operating Accruals
aliases:
- AccrOper
- PctAcc
- Percent Operating Accruals
one_line: Cross-sectional equity anomaly that uses Percent Operating Accruals to long
  low-signal stocks and short high-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires firm-level accounting data (balance sheet,
  income statement, cash-flow items) that the OBaI backtest engine does not ingest.
  The engine consumes OHLCV bars on daily/intraday timeframes only. Use as routing
  reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Accounting data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- 'Table 4 Panel A: Report size-adjusted return hedges with p-values; t-stat is approximate,
  I converted the p-value to t but not exact since the table says p <.001 instead
  of giving a value.'
- 'Original-paper replication evidence: t>2.6 in size-adjusted long-short; reported
  long-short return=0.97, t-stat=3.29.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Percent Operating Accruals
  authors:
  - Hafzalla, Lundholm, Van Winkle
  year: 2011
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Percent Operating Accruals is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Income before extraordinary items (ib) minus net cash flow (oancf) divided by absolute value of ib. If oancf is missing, PctAcc is defined as ( (act - act$_{t-12}$) - (che - che$_{t-12}$) - ( (lct - lct$_{t-12}$) - (dlc - dlc$_{t-12}$) - (txp - txp$_{t-12}$) - dp ) )/abs(ib). In either case, if ib is equal to 0, divide by .01 instead. Exclude if price less than 5. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PctAcc for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PctAcc; category=accruals; data=Accounting; evidence=t>2.6 in size-adjusted long-short. Review the generated entry before using it as a final public corpus item.
