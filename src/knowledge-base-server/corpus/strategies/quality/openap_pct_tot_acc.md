---
entry_type: strategy
id: openap_pct_tot_acc
canonical_name: Percent Total Accruals
aliases:
- AccrPct
- PctTotAcc
- Percent Total Accruals
one_line: Cross-sectional equity anomaly that uses Percent Total Accruals to long
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
- 'Table 5 Panel A: Report size-adjusted return hedges with p-values; t-stat is approximate,
  I converted the p-value to t but not exact since the table says p <.001 instead
  of giving a value.'
- 'Original-paper replication evidence: t>2.6 in size-adjusted long-short; reported
  long-short return=0.71, t-stat=3.29.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Percent Total Accruals
  authors:
  - Hafzalla, Lundholm, Van Winkle
  year: 2011
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Percent Total Accruals is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Net income (ni) minus (purchase of common and preferred stock (prstkcc) minus sale of common and preferred stock (sstk) plus dividends (dvt), cash flow from operations (oancf), from financing (fincf) and investment (ivncf)). Scaled by absolute value of net income. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PctTotAcc for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PctTotAcc; category=accruals; data=Accounting; evidence=t>2.6 in size-adjusted long-short. Review the generated entry before using it as a final public corpus item.
