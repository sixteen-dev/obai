---
entry_type: strategy
id: openap_activism2
canonical_name: Active shareholders
aliases:
- Active shareholders
- Activism2
one_line: Cross-sectional equity anomaly that uses Active shareholders to long high-signal
  stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires institutional-holdings (13F) data that the
  OBaI backtest engine does not ingest. Use as routing reference; do not attempt
  backtest execution.
signal_inputs:
- OpenAP 13F data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- works about the same EW in Tab 3
- 'Original-paper replication evidence: t=2.0 in port sort; reported long-short return=0.661666667,
  t-stat=2.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test ownership effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Active shareholders
  authors:
  - Cremers
  - Nair
  year: 2005
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Active shareholders is represented in the OpenAP signal catalog as a ownership predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Institiutional ownership share (maxinstown\_perc) if share greater than 5 percent. Set to missing if G is missing, or if dual share class, or if 24 minus Governance index (G) is less than 19. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Activism2 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Activism2; category=ownership; data=13F; evidence=t=2.0 in port sort. Review the generated entry before using it as a final public corpus item.
