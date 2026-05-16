---
entry_type: strategy
id: openap_cheq
canonical_name: Growth in book equity
aliases:
- BEgrowth
- ChEQ
- Growth in book equity
one_line: Cross-sectional equity anomaly that uses Growth in book equity to long low-signal
  stocks and short high-signal stocks.
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
- Table 4, panel A, take difference between High and low SUSG returns, tstat is reported.
  Paper also offers alphas and FM regressions.
- 'Original-paper replication evidence: t=5.38 in EW port sort; reported long-short
  return=0.8, t-stat=5.38.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Growth in book equity
  authors:
  - Lockwood
  - Prombutr
  year: 2010
  venue: JFR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Growth in book equity is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Ratio of book equity (ceq) to book equity in the previous year. Include only if book equity is positive this year and last year. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChEQ for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChEQ; category=investment; data=Accounting; evidence=t=5.38 in EW port sort. Review the generated entry before using it as a final public corpus item.
