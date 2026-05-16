---
entry_type: strategy
id: openap_herfbe
canonical_name: Industry concentration (equity)
aliases:
- HerfBE
- Industry concentration (equity)
one_line: Cross-sectional equity anomaly that uses Industry concentration (equity)
  to long low-signal stocks and short high-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires specialized data inputs (short interest, lending
  fees, or other alternative datasets) that the OBaI backtest engine does not ingest.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Other data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Tab3 H(Equity) characteristic adjusted t-stat 2.52. Judgment call.
- 'Original-paper replication evidence: t = 2.52 in characteristics-adjusted port
  sort; reported long-short return=0.24, t-stat=2.52.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Industry concentration (equity)
  authors:
  - Hou
  - Robinson
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Industry concentration (equity) is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Three-year rolling average of the three digit industry Herfindahl index based on firm book equity. Exclude regulated industries (4011, 4210, 4213 & year $\leq$ 1980; 4512 & year $\leq$ 1978, 4812, 4813 & year $\leq$ 1982, 4900-4999 in any year) The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute HerfBE for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=HerfBE; category=other; data=Other; evidence=t = 2.52 in characteristics-adjusted port sort. Review the generated entry before using it as a final public corpus item.
