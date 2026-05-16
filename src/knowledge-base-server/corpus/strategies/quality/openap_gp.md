---
entry_type: strategy
id: openap_gp
canonical_name: Gross Profits / Total Assets
aliases:
- GP
- ProfGross
- gross profits / total assets
one_line: Cross-sectional equity anomaly that uses gross profits / total assets to
  long high-signal stocks and short low-signal stocks.
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
- Tab 2a says NYSE breakpoints, but our code gets much closer to their result without
  all stock breakpoints.
- 'Original-paper replication evidence: t=2.5 in VW LS quint; reported long-short
  return=0.31, t-stat=2.49.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: gross profits / total assets
  authors:
  - Novy-Marx
  year: 2013
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
gross profits / total assets is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Revenue (sale) - cost of goods solds (cogs), divided by total assets (at). Drop if financial. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute GP for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=GP; category=profitability; data=Accounting; evidence=t=2.5 in VW LS quint. Review the generated entry before using it as a final public corpus item.
