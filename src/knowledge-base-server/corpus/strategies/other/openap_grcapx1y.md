---
entry_type: strategy
id: openap_grcapx1y
canonical_name: Investment growth (1 year)
aliases:
- CAPXgr1y
- Investment growth (1 year)
- grcapx1y
one_line: Cross-sectional equity anomaly that uses Investment growth (1 year) to rank
  stocks by the signal and form the source-defined long-short spread.
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
- HXZ deviates from OP. OP only examines (1) capx / lag(capx,2) and capx / sum( capx(t-3:t)
  ). OP notation is odd, uses cegth2 and cegth3 but no cegth.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Investment growth (1 year)
  authors:
  - Anderson
  - Garcia-Feijoo
  year: 2006
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Investment growth (1 year) is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Growth between one-year lagged capital expenditures (capx) and two-year lagged capital expenditures. Replace capx with the one year difference in property, plant and equipment (ppent) if capx is missing and the corresponding firm age is greater or equal than two years. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute grcapx1y for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=grcapx1y; category=investment; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
