---
entry_type: strategy
id: openap_accruals
canonical_name: Accruals
aliases:
- Accruals
one_line: Cross-sectional equity anomaly that uses Accruals to long low-signal stocks
  and short high-signal stocks.
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
- Table 6 year t+1 hedge. Only size adjusted and CAPM adjusted.
- 'Original-paper replication evidence: t > 4 in port sort CAPM alpha 12 month holding;
  reported long-short return=0.866666667, t-stat=4.71.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Accruals
  authors:
  - Sloan
  year: 1996
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Accruals is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual change in current total assets (act) minus annual change in cash and short-term investements (che) minus annual change in current liabilities (lct) minus annual change in debt in current liabilities (dlc) minus change in income taxes (txp). All divided by average total assets (at) over this year and last year. Exclude if abs(prc) < 5. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Accruals for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Accruals; category=accruals; data=Accounting; evidence=t > 4 in port sort CAPM alpha 12 month holding. Review the generated entry before using it as a final public corpus item.
