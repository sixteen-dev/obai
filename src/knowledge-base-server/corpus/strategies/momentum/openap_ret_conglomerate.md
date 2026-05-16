---
entry_type: strategy
id: openap_ret_conglomerate
canonical_name: Conglomerate return
aliases:
- Conglomerate return
- RetConglomerate
- retConglomerate
one_line: Cross-sectional equity anomaly that uses Conglomerate return to long high-signal
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
- Also works VW (t=3.2)
- 'Original-paper replication evidence: t=5.5 in port sort; reported long-short return=1.18,
  t-stat=5.51.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Conglomerate return
  authors:
  - Cohen
  - Lou
  year: 2012
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Conglomerate return is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Identify conglomerate firms as those with multiple OPSEG or BUSSEG entries in the Compustat segment data (and require that at least 80% of firm's total assets are covered by segment data). Compute monthly stock return at the 2-digit SIC level for stand-alone (non-conglomerate) firms only, and match those returns to conglomerates' segments. Compute weighted conglomerate return as the industry return of stand-alone companies, weighted with a conglomerate's total sales in each industry. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute retConglomerate for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=retConglomerate; category=lead lag; data=Price; evidence=t=5.5 in port sort. Review the generated entry before using it as a final public corpus item.
