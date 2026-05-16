---
entry_type: strategy
id: openap_feps
canonical_name: Analyst earnings per share
aliases:
- Analyst earnings per share
- FEPS
one_line: Cross-sectional equity anomaly that uses Analyst earnings per share to long
  high-signal stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=2.7 in port sort; reported long-short return=1.199,
  t-stat=2.66.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Analyst earnings per share
  authors:
  - Cen, Wei,
  - Zhang
  year: 2006
  venue: WP
  url: https://www.openassetpricing.com/data/
---
## Thesis
Analyst earnings per share is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using IBES unadjusted forecasts, keep fpi == 1, signal is meanest. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute FEPS for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=FEPS; category=profitability; data=Analyst; evidence=t=2.7 in port sort. Review the generated entry before using it as a final public corpus item.
