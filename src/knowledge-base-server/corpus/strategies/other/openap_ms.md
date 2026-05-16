---
entry_type: strategy
id: openap_ms
canonical_name: Mohanram G-score
aliases:
- MS
- Mohanram G-score
- Mscore
one_line: Cross-sectional equity anomaly that uses Mohanram G-score to long high-signal
  stocks and short low-signal stocks.
category: other
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
- OP's signal is really complicated, and the text is not extremely detailed. We do
  the best we can to mimic OP's results, but complications in data lagging, combining
  annual and quarterly data, and sample selection make this extremely difficult. We
  get a t-stat near 6 and monotonic returns, quite in the spirite of OP, but still
  far from OP's t-stat of 9. Could be that OP uses overlapping samples. We use Portfolio
  Period = 1 to try to get close.
- 'Original-paper replication evidence: t=9 in port sort nonstandard data lag; reported
  long-short return=1.575, t-stat=9.14.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Mohanram G-score
  authors:
  - Mohanram
  year: 2005
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Mohanram G-score is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: See code for details. MS is only evaluated for low BM firms and comes from combining three signals related to profitability and cash flow, two signals related to income volatility, and three signals related to investment. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MS for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MS; category=composite accounting; data=Accounting; evidence=t=9 in port sort nonstandard data lag. Review the generated entry before using it as a final public corpus item.
