---
entry_type: strategy
id: openap_size
canonical_name: Size
aliases:
- Size
one_line: Cross-sectional equity anomaly that uses Size to long low-signal stocks
  and short high-signal stocks.
category: size
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
- 'Original-paper replication evidence: t=3.1 in long-short; reported long-short return=1.01,
  t-stat=3.07.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test size effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Size
  authors:
  - Banz
  year: 1981
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Size is represented in the OpenAP signal catalog as a size predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Log of monthly market value of equity (abs(prc)*shrout)). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Size for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Size; category=size; data=Price; evidence=t=3.1 in long-short. Review the generated entry before using it as a final public corpus item.
