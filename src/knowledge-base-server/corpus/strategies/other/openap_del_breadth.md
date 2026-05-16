---
entry_type: strategy
id: openap_del_breadth
canonical_name: Breadth of ownership
aliases:
- Breadth of ownership
- DelBreadth
one_line: Cross-sectional equity anomaly that uses Breadth of ownership to long high-signal
  stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: quarterly
engine_fit: reference_only
approximation_notes: Signal requires institutional-holdings (13F) data that the
  OBaI backtest engine does not ingest. Use as routing reference; do not attempt
  backtest execution.
signal_inputs:
- OpenAP 13F data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 4 (chose the cumluative annual return, but they report 1,2,3 and 4 quarters),
- 'Original-paper replication evidence: t=4.0 in port sort; reported long-short return=0.673333333,
  t-stat=3.96.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test ownership effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Breadth of ownership
  authors:
  - Chen, Hong
  - Stein
  year: 2002
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Breadth of ownership is represented in the OpenAP signal catalog as a ownership predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Quarterly change in the number of institutional owners (numinstowners) from 13F data. Exclude if in the lowest quintile of stocks by market value of equity (based on NYSE stocks only). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelBreadth for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelBreadth; category=ownership; data=13F; evidence=t=4.0 in port sort. Review the generated entry before using it as a final public corpus item.
