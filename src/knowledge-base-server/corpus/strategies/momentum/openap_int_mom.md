---
entry_type: strategy
id: openap_int_mom
canonical_name: Intermediate Momentum
aliases:
- IntMom
- Intermediate Momentum
- Mom12to7
one_line: Cross-sectional equity anomaly that uses Intermediate Momentum to long high-signal
  stocks and short low-signal stocks.
category: momentum
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Text says they use VW, but EW performs similarly. Table 2, column (1).
- 'Original-paper replication evidence: Tab2 t-stat 5.79; reported long-short return=1.2,
  t-stat=5.79.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Intermediate Momentum
  authors:
  - Novy-Marx
  year: 2012
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Intermediate Momentum is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Stock return between months t-12 and t-6 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IntMom for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IntMom; category=momentum; data=Price; evidence=Tab2 t-stat 5.79. Review the generated entry before using it as a final public corpus item.
