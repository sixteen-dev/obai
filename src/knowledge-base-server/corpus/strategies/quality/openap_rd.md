---
entry_type: strategy
id: openap_rd
canonical_name: R&D over market cap
aliases:
- R&D over market cap
- RD
one_line: Cross-sectional equity anomaly that uses R&D over market cap to long high-signal
  stocks and short low-signal stocks.
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
- Table 4 has portfolio returns, but no-tstats. Table 6 does not show LS, but it has
  t-stat for high = 4.44.
- 'Original-paper replication evidence: strong port sort; reported long-short return=0.8875,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test R&D effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: R&D over market cap
  authors:
  - Chan, Lakonishok
  - Sougiannis
  year: 2001
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
R&D over market cap is represented in the OpenAP signal catalog as a R&D predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: R&D expense (xrd) over market value of equity. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RD; category=R&D; data=Accounting; evidence=strong port sort. Review the generated entry before using it as a final public corpus item.
