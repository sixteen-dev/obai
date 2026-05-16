---
entry_type: strategy
id: openap_change_roa
canonical_name: Change in Return on assets
aliases:
- Change in Return on assets
- ChangeRoA
one_line: Cross-sectional equity anomaly that uses Change in Return on assets to rank
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
- HXZ do not cite anyone for this "replication." OP (HXZ) uses RDQ to get very timely
  data, but we just lag everything by 3 months for simplicity. This is pretty much
  a combination of profitability and investment, if you think about it.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in Return on assets
  authors:
  - Balakrishnan, Bartov
  - Faurel
  year: 2010
  venue: ''
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in Return on assets is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Quarterly return on assets (rdq/atq) minus its value four quarters ago. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute ChangeRoA for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChangeRoA; category=composite accounting; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
