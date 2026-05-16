---
entry_type: strategy
id: openap_l_rreversal
canonical_name: Long-run reversal
aliases:
- LRreversal
- Long-run reversal
- Mom36m
one_line: Cross-sectional equity anomaly that uses Long-run reversal to long low-signal
  stocks and short high-signal stocks.
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
- Insignificant at 12-month horizon. Many alternative signal designs lead to similar
  results.
- 'Original-paper replication evidence: t=3.3 in long-short; reported long-short return=0.105,
  t-stat=3.29.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test long term reversal effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Long-run reversal
  authors:
  - De Bondt
  - Thaler
  year: 1985
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Long-run reversal is represented in the OpenAP signal catalog as a long term reversal predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Stock return between months t-36 and t-13. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute LRreversal for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=LRreversal; category=long term reversal; data=Price; evidence=t=3.3 in long-short. Review the generated entry before using it as a final public corpus item.
