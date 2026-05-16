---
entry_type: strategy
id: openap_op_leverage
canonical_name: Operating leverage
aliases:
- OPLeverage
- OperLeverage
- Operating leverage
one_line: Cross-sectional equity anomaly that uses Operating leverage to long high-signal
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
- Table III panel b shows EW raw returns strong (t-stat 3.38), somewhat weaker after
  factor adjustment or in VW returns. Table IIIv has both raw and alphas.
- 'Original-paper replication evidence: t=3.38 in port sort; reported long-short return=0.51,
  t-stat=3.38.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Operating leverage
  authors:
  - Novy-Marx
  year: 2011
  venue: ROF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Operating leverage is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Sum of administrative expenses (xsga) and cost of goods sold (cogs), scaled by total assets (at). Use xsga = 0 if xsga is missing. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OPLeverage for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OPLeverage; category=other; data=Accounting; evidence=t=3.38 in port sort. Review the generated entry before using it as a final public corpus item.
