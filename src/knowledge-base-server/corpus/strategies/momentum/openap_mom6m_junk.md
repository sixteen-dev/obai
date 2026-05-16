---
entry_type: strategy
id: openap_mom6m_junk
canonical_name: Junk Stock Momentum
aliases:
- Junk Stock Momentum
- Mom6Jnk
- Mom6mJunk
one_line: Cross-sectional equity anomaly that uses Junk Stock Momentum to long high-signal
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=4.3 in port sort; reported long-short return=2.12,
  t-stat=4.29.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Junk Stock Momentum
  authors:
  - Avramov et al
  year: 2007
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Junk Stock Momentum is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Mom6m. Include only stocks with a credit rating (splticrm) of BBB or lower. Drop if missing credit rating or non-standard credit rating. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Mom6mJunk for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Mom6mJunk; category=momentum; data=Price; evidence=t=4.3 in port sort. Review the generated entry before using it as a final public corpus item.
