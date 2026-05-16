---
entry_type: strategy
id: openap_varcf
canonical_name: Cash-flow to price variance
aliases:
- CF2Pvar
- Cash-flow to price variance
- VarCF
one_line: Cross-sectional equity anomaly that uses Cash-flow to price variance to
  long low-signal stocks and short high-signal stocks.
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
- OP reports mean regression coeff across 90 multiple regressions. OP shows a minus
  sign in Tab 1, but we find monotonic returns with a plus sign, and the plus sign
  is consistent with traditional theory.
- 'Original-paper replication evidence: t=2.5 in mv reg nonstandard; reported long-short
  return=n/a, t-stat=2.5.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test cash flow risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Cash-flow to price variance
  authors:
  - Haugen
  - Baker
  year: 1996
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Cash-flow to price variance is represented in the OpenAP signal catalog as a cash flow risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rolling variance of (ib+dp)/mve\_c over the past 60 months (minimum 24 months data required). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute VarCF for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=VarCF; category=cash flow risk; data=Accounting; evidence=t=2.5 in mv reg nonstandard. Review the generated entry before using it as a final public corpus item.
