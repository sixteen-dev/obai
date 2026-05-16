---
entry_type: strategy
id: openap_earnings_timeliness
canonical_name: Earnings timeliness
aliases:
- Earnings timeliness
- EarningsTimeliness
one_line: Cross-sectional equity anomaly that uses Earnings timeliness to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Table 5 regresses cost of equity proxies (e.g. beta) on various earnings attributes.
- 'Original-paper replication evidence: correlated with BM and other predictors; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings timeliness
  authors:
  - Francis, LaFond, Olsson, Schipper
  year: 2004
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings timeliness is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Earnings (ib) scaled by market capitalization (shrout*abs(prc)) regressed on the 15 month stock return from (t=-11 to t=+3), an indicator for whether that 15- month return is negative and the interaction of these two variables. Rolling regression in annual data with 10 observations. EarningsTimeliness is the R2 of this regression. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute EarningsTimeliness for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarningsTimeliness; category=other; data=Accounting; evidence=correlated with BM and other predictors. Review the generated entry before using it as a final public corpus item.
