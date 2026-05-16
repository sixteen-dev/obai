---
entry_type: strategy
id: openap_cap_turnover
canonical_name: Capital turnover
aliases:
- CapTurn
- CapTurnover
- Capital turnover
one_line: Cross-sectional equity anomaly that uses Capital turnover to long high-signal
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
- weak in original paper. They forecast returns using many variables. Capital turnover
  is not in the top 11 predictors,
- 'Original-paper replication evidence: t<2 in mv reg nonstandard; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test turnover effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Capital turnover
  authors:
  - Haugen
  - Baker
  year: 1996
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Capital turnover is represented in the OpenAP signal catalog as a turnover predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Lagged sales (sale) divided by two-year lagged assets (at). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CapTurnover for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CapTurnover; category=turnover; data=Accounting; evidence=t<2 in mv reg nonstandard. Review the generated entry before using it as a final public corpus item.
