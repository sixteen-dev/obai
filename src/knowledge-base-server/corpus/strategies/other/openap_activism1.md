---
entry_type: strategy
id: openap_activism1
canonical_name: Takeover vulnerability
aliases:
- Activism1
- Takeover vulnerability
one_line: Cross-sectional equity anomaly that uses Takeover vulnerability to long
  high-signal stocks and short low-signal stocks.
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
- works a bit better EW in Tab 3
- 'Original-paper replication evidence: t=3.1 in port sort; reported long-short return=0.9025,
  t-stat=3.13.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Takeover vulnerability
  authors:
  - Cremers
  - Nair
  year: 2005
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Takeover vulnerability is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 24 minus Governance Index (G). Set to missing if G is missing, or if not in the highest quartile of institutional ownership (maxinstown\_perc), or if dual share class. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Activism1 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Activism1; category=other; data=13F; evidence=t=3.1 in port sort. Review the generated entry before using it as a final public corpus item.
