---
entry_type: strategy
id: openap_invest
canonical_name: Capex and Inventory Change
aliases:
- Capex and Inventory Change
- invest
one_line: Cross-sectional equity anomaly that uses Capex and Inventory Change to long
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
- Should remove. Paper was retracted,
- 'Original-paper replication evidence: drop; reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Capex and Inventory Change
  authors:
  - Chen
  - Zhang
  year: 2010
  venue: JF, but retracted
  url: https://www.openassetpricing.com/data/
---
## Thesis
Capex and Inventory Change is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual change in property, plant and equipment (ppegt) plus annual change in inventory (invt), scaled by lagged total assets (at). Use ppent if ppegt is missing. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute invest for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=invest; category=investment; data=Accounting; evidence=drop. Review the generated entry before using it as a final public corpus item.
