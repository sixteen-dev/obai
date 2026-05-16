---
entry_type: strategy
id: openap_roaq
canonical_name: Return on assets (qtrly)
aliases:
- Return on assets (qtrly)
- RoAq
- roaq
one_line: Cross-sectional equity anomaly that uses Return on assets (qtrly) to long
  high-signal stocks and short low-signal stocks.
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
- This is like a more timely version of the other profitability measures. Interestingly,
  they don't cite Fama French 2006, nor Novy Marx 2013. MP have a very slightly different
  formulation.
- 'Original-paper replication evidence: t=6.5 in port sort, nontraditional; reported
  long-short return=0.841666667, t-stat=6.45.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return on assets (qtrly)
  authors:
  - Balakrishnan, Bartov
  - Faurel
  year: 2010
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return on assets (qtrly) is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Quarterly net income (ibq) divided by lagged total assets (atq). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute roaq for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=roaq; category=profitability; data=Accounting; evidence=t=6.5 in port sort, nontraditional. Review the generated entry before using it as a final public corpus item.
