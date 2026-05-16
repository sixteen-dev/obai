---
entry_type: strategy
id: openap_nanalyst
canonical_name: Number of analysts
aliases:
- Number of analysts
- nanalyst
one_line: Cross-sectional equity anomaly that uses Number of analysts to long high-signal
  stocks and short low-signal stocks.
category: other
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
- Not a focus of the paper. Table 2 shows summary stats that suggest size adjusted
  returns are different by group, but no t-stats and size adjustment may affect each
  group differently.
- 'Original-paper replication evidence: spread in median ret each leg size adj; reported
  long-short return=0.566666667, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test info proxy effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Number of analysts
  authors:
  - Elgers, Lo
  - Pfeiffer
  year: 2001
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Number of analysts is represented in the OpenAP signal catalog as a info proxy predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Number of estimates (numest) in IBES for one-quarter ahead earnings. Replace with 0 if missing after 1989. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute nanalyst for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=nanalyst; category=info proxy; data=Analyst; evidence=spread in median ret each leg size adj. Review the generated entry before using it as a final public corpus item.
