---
entry_type: strategy
id: openap_sgr
canonical_name: Annual sales growth
aliases:
- Annual sales growth
- sgr
one_line: Cross-sectional equity anomaly that uses Annual sales growth to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Variation of meanrankrevgrowth. Original paper uses only 5-year growth. Included
  to match HXZ,
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test sales growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Annual sales growth
  authors:
  - Lakonishok, Shleifer, Vishny
  year: 1994
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Annual sales growth is represented in the OpenAP signal catalog as a sales growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Sales (sale) relative to t-1. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute sgr for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=sgr; category=sales growth; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
