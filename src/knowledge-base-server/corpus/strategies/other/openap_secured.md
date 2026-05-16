---
entry_type: strategy
id: openap_secured
canonical_name: Secured debt
aliases:
- Secured debt
- secured
one_line: Cross-sectional equity anomaly that uses Secured debt to long high-signal
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
- Tab 5B shows sign of predictability depends on Z-score quartile. One could improve
  this portfolio by focusing on low Z-score.
- 'Original-paper replication evidence: t > 1.96 in mv reg; reported long-short return=n/a,
  t-stat=1.96.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Secured debt
  authors:
  - Valta
  year: 2016
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Secured debt is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Debt/mortgages and other secured (dm) divided by long-term liabilities (dltt) plus current liabilities (dlc). Replace with 0 if missing. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute secured for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=secured; category=external financing; data=Accounting; evidence=t > 1.96 in mv reg. Review the generated entry before using it as a final public corpus item.
