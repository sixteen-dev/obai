---
entry_type: strategy
id: openap_analyst_revision
canonical_name: EPS forecast revision
aliases:
- AnalystRevision
- EPS forecast revision
one_line: Cross-sectional equity anomaly that uses EPS forecast revision to long high-signal
  stocks and short low-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OP only longs top 20 stocks according to signal. We were more flexible. Sample is
  very short but the results seem robust.
- 'Original-paper replication evidence: t=3.2 in long only CAPM alpha; reported long-short
  return=0.460583333, t-stat=3.169151376.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: EPS forecast revision
  authors:
  - Hawkins, Chamberlin, Daniel
  year: 1984
  venue: FAJ
  url: https://www.openassetpricing.com/data/
---
## Thesis
EPS forecast revision is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: keep fpi == "1", last obs each month. Signal is meanest / last month's meanest. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AnalystRevision for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AnalystRevision; category=earnings forecast; data=Analyst; evidence=t=3.2 in long only CAPM alpha. Review the generated entry before using it as a final public corpus item.
