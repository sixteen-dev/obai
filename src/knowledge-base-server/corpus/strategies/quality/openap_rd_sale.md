---
entry_type: strategy
id: openap_rd_sale
canonical_name: R&D to Sales
aliases:
- R&D to sales
- rd_sale
one_line: Cross-sectional equity anomaly that uses R&D to sales to long high-signal
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
- No t-stat in Tab 3, but authors say "there is little if any relation between R&D
  relative to sales and future returns." Pattern is non monotinic, mostly increasing,
  but drops in port 5, so we have a negative sign
- 'Original-paper replication evidence: 8 bps spread in port sort; reported long-short
  return=0.08, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: R&D to sales
  authors:
  - Chan, Lakonishok
  - Sougiannis
  year: 2001
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
R&D to sales is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: One year lagged R&D (xrd) divided by one year lagged sales (sale). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute rd_sale for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=rd_sale; category=investment alt; data=Accounting; evidence=8 bps spread in port sort. Review the generated entry before using it as a final public corpus item.
