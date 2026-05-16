---
entry_type: strategy
id: openap_mom12m
canonical_name: Momentum (12 month)
aliases:
- Mom12m
- Momentum (12 month)
one_line: Cross-sectional equity anomaly that uses Momentum (12 month) to long high-signal
  stocks and short low-signal stocks.
category: momentum
asset_classes:
- equities
typical_holding_period: quarterly
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
- see Mom6m
- 'Original-paper replication evidence: t=3.7 long-short; reported long-short return=1.31,
  t-stat=3.74.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Momentum (12 month)
  authors:
  - Jegadeesh
  - Titman
  year: 1993
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Momentum (12 month) is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Stock return between months t-12 and t-1 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Mom12m for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Mom12m; category=momentum; data=Price; evidence=t=3.7 long-short. Review the generated entry before using it as a final public corpus item.
