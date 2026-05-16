---
entry_type: strategy
id: openap_abnormal_accruals
canonical_name: Abnormal Accruals
aliases:
- Abnormal Accruals
- AbnormalAccruals
- AccrAbn
one_line: Cross-sectional equity anomaly that uses Abnormal Accruals to long low-signal
  stocks and short high-signal stocks.
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
- OP is aggressive and lags accounting data by only 3 months (p361) instead of the
  usual 6. This likely accounts for our relative underperformance.
- 'Original-paper replication evidence: t=8 port sort w/ nonstandard data lag; reported
  long-short return=0.916666667, t-stat=8.43.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Abnormal Accruals
  authors:
  - Xie
  year: 2001
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Abnormal Accruals is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define Accruals as net income (ib) minus operating cash flow (oancf), divided by average total assets (at) for years t-1 and t. If oancf is missing, replace operating cash flow with funds from operations (fopt) minus the annual change in total current assets (act) plus the annual change in cash and short-term investments (che) plus the annual change in current liabilities (lct) minus the annual change in debt in current liabilities (dlc). For each year t and 2-digit sic code, regress Accruals on: the inverse of average total assets for year t-1, the change in revenue (sale) from year t-1 to t divided by total assets for t-1, propery plant and equipment (ppegt) divided by total assets for t-1. AbnormalAccrual is the residual from this cross-sectional regression. See code for more details. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AbnormalAccruals for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AbnormalAccruals; category=accruals; data=Accounting; evidence=t=8 port sort w/ nonstandard data lag. Review the generated entry before using it as a final public corpus item.
