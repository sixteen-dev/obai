---
entry_type: strategy
id: openap_grcapx3y
canonical_name: Change in capex (three years)
aliases:
- CAPXgr3y
- Change in capex (three years)
- grcapx3y
one_line: Cross-sectional equity anomaly that uses Change in capex (three years) to
  long low-signal stocks and short high-signal stocks.
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
- We follow OP, not HXZ. OP notation is odd, uses cegth2 and cegth3 but no cegth.
- 'Original-paper replication evidence: t=4.7 in port sort; reported long-short return=0.6,
  t-stat=4.71.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in capex (three years)
  authors:
  - Anderson
  - Garcia-Feijoo
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in capex (three years) is represented in the OpenAP signal catalog as a investment growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Capital expenditures (capx) divided by the sum of capital expenditures from year - 1, year -2, and year -3. If capx is missing, replace with annual change in property, plant and equipment (ppent). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute grcapx3y for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=grcapx3y; category=investment growth; data=Accounting; evidence=t=4.7 in port sort. Review the generated entry before using it as a final public corpus item.
