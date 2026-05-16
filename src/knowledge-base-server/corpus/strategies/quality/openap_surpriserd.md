---
entry_type: strategy
id: openap_surpriserd
canonical_name: Unexpected R&D increase
aliases:
- SurpriseRD
- Unexpected R&D increase
one_line: Cross-sectional equity anomaly that uses Unexpected R&D increase to long
  high-signal stocks and short low-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires firm-level accounting data (balance sheet,
  income statement, cash-flow items) that the OBaI backtest engine does not ingest.
  The engine consumes OHLCV bars on daily/intraday timeframes only. Use as routing
  reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Accounting data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 3 has event study, table 5 has LS ports. Table 5 p-value = 0.000, so we use
  norm dist assuming p-value = 0.0004. FF3 loadings should roughly cancel out.
- 'Original-paper replication evidence: t=3.5 in long-short; reported long-short return=0.294,
  t-stat=3.540083799.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test R&D effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Unexpected R&D increase
  authors:
  - Eberhart, Maxwell
  - Siddique
  year: 2004
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Unexpected R&D increase is represented in the OpenAP signal catalog as a R&D predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if: R&D (xrd) scaled by revenue (revt) is positive, R&D scaled by total assets (at) is positive, annual R&D growth is greater than 5%, annual growth in R&D over total assets is greater than 5%. SurpriseRD is 0 otherwise. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute SurpriseRD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=SurpriseRD; category=R&D; data=Accounting; evidence=t=3.5 in long-short. Review the generated entry before using it as a final public corpus item.
