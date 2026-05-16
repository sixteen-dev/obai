---
entry_type: strategy
id: openap_fgr5yr_no_lag
canonical_name: Long-term EPS forecast (Monthly)
aliases:
- EPSForeLT
- Long-term EPS forecast (Monthly)
- fgr5yrNoLag
one_line: Cross-sectional equity anomaly that uses Long-term EPS forecast (Monthly)
  to rank stocks by the signal and form the source-defined long-short spread.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- This is the simple and perhaps intuitive long term forecast signal, but OP is very
  specific about timing.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Long-term EPS forecast (Monthly)
  authors:
  - La Porta
  year: 1996
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Long-term EPS forecast (Monthly) is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Long-term earnings forecast (fgr5yr). Exclude if book equity (ceq), net income (ib), deferred taxes (txdi), dividends (dvp), revenue (sale) or depreciation (dp) is missing. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute fgr5yrNoLag for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=fgr5yrNoLag; category=earnings forecast; data=Analyst; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
