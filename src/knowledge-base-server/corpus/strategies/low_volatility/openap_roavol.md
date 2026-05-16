---
entry_type: strategy
id: openap_roavol
canonical_name: RoA volatility
aliases:
- RoA volatility
- roavol
one_line: Cross-sectional equity anomaly that uses RoA volatility to rank stocks by
  the signal and form the source-defined long-short spread.
category: low_volatility
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
- Table 5 regresses cost of equity proxies (e.g. beta) on various earnings attributes.
- 'Original-paper replication evidence: correlated with BM and other predictors; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test cash flow risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: RoA volatility
  authors:
  - Francis, LaFond, Olsson, Schipper
  year: 2004
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
RoA volatility is represented in the OpenAP signal catalog as a cash flow risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rolling standard deviation of quarterly return on assets (roaq) over 4 years (minimum 2 years). The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute roavol for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=roavol; category=cash flow risk; data=Accounting; evidence=correlated with BM and other predictors. Review the generated entry before using it as a final public corpus item.
