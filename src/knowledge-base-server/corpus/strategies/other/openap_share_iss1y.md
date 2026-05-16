---
entry_type: strategy
id: openap_share_iss1y
canonical_name: Share issuance (1 year)
aliases:
- Share issuance (1 year)
- ShareIs5
- ShareIss1Y
one_line: Cross-sectional equity anomaly that uses Share issuance (1 year) to long
  low-signal stocks and short high-signal stocks.
category: other
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=7.08 in univariate reg; reported long-short
  return=n/a, t-stat=7.08.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Share issuance (1 year)
  authors:
  - Pontiff
  - Woodgate
  year: 2008
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Share issuance (1 year) is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Growth in number of shares between t-18 and t-6. Number of shares is calculated as shrout/cfacshr to adjust for splits. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ShareIss1Y for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ShareIss1Y; category=external financing; data=Accounting; evidence=t=7.08 in univariate reg. Review the generated entry before using it as a final public corpus item.
