---
entry_type: strategy
id: openap_s_treversal
canonical_name: Short term reversal
aliases:
- Mom1m
- STreversal
- Short term reversal
one_line: Cross-sectional equity anomaly that uses Short term reversal to long low-signal
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=12 in port sort; reported long-short return=1.99,
  t-stat=12.44.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test short-term reversal effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Short term reversal
  authors:
  - Jegadeesh
  year: 1990
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Short term reversal is represented in the OpenAP signal catalog as a short-term reversal predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Stock return (ret) over the previous month. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute STreversal for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=STreversal; category=short-term reversal; data=Price; evidence=t=12 in port sort. Review the generated entry before using it as a final public corpus item.
