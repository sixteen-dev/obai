---
entry_type: strategy
id: openap_intanbm
canonical_name: Intangible return using BM
aliases:
- IntanBM
- Intangible return using BM
one_line: Cross-sectional equity anomaly that uses Intangible return using BM to long
  low-signal stocks and short high-signal stocks.
category: momentum
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=4.0 in mv reg; reported long-short return=n/a,
  t-stat=3.99.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test long term reversal effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Intangible return using BM
  authors:
  - Daniel
  - Titman
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Intangible return using BM is represented in the OpenAP signal catalog as a long term reversal predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: In each month, run a cross-sectional regression of a firm's five-year stock return on 5 year lagged BM (defined above) and a constructed regressor that is the change in BM from 5 years ago to today plus the five-year stock return. The residual from that regression is IntanBM. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IntanBM for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IntanBM; category=long term reversal; data=Accounting; evidence=t=4.0 in mv reg. Review the generated entry before using it as a final public corpus item.
