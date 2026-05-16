---
entry_type: strategy
id: openap_leverage
canonical_name: Market leverage
aliases:
- Leverage
- Market leverage
one_line: Cross-sectional equity anomaly that uses Market leverage to long high-signal
  stocks and short low-signal stocks.
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
- 'Original-paper replication evidence: t=3.9 in regression; reported long-short return=n/a,
  t-stat=3.93.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test leverage effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Market leverage
  authors:
  - Bhandari
  year: 1988
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Market leverage is represented in the OpenAP signal catalog as a leverage predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Total liabilities (lt) divided by market value of equity. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Leverage for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Leverage; category=leverage; data=Accounting; evidence=t=3.9 in regression. Review the generated entry before using it as a final public corpus item.
