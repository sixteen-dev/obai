---
entry_type: strategy
id: openap_deldrc
canonical_name: Deferred Revenue
aliases:
- DeferRev
- Deferred Revenue
- DelDRC
one_line: Cross-sectional equity anomaly that uses Deferred Revenue to long high-signal
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
- OP shows only cross-sectional regressions, and the sample is very small (5 years).
  Table 7 has return forecasting regressiosn but no hedge returns. Other tables examine
  analyst forecast errors.
- 'Original-paper replication evidence: t=3.6 in nonstandard reg 5 year sample; reported
  long-short return=n/a, t-stat=3.59.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Deferred Revenue
  authors:
  - Prakash
  - Sinha
  year: 2013
  venue: CAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Deferred Revenue is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual change in deferred revenue (drc) scaled by average total assets (at) in t-1 and t. Exclude if negative book equity (ceq), deferred revenue equal to 0 in both years, revenue less than 5m, or SIC code between 6000 and 6999. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelDRC for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelDRC; category=investment alt; data=Accounting; evidence=t=3.6 in nonstandard reg 5 year sample. Review the generated entry before using it as a final public corpus item.
