---
entry_type: strategy
id: openap_rd_ability
canonical_name: R&D ability
aliases:
- R&D ability
- RDAbility
one_line: Cross-sectional equity anomaly that uses R&D ability to long high-signal
  stocks and short low-signal stocks.
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
- 2A has main double sort results. Works both EW and VW. Lots of supporting evidence
  too.
- 'Original-paper replication evidence: t=2.6 in double sort; reported long-short
  return=1.35, t-stat=2.61.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: R&D ability
  authors:
  - Cohen, Diether
  - Malloy
  year: 2013
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
R&D ability is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Regress log of sales growth (sale over sale in previous year) on log of (1+ xrd/sale) in 5 bivariate regressions with (1+xrd/sale) lagged by $1, \ldots, 5$ years. Run regressions over previous 8 years and require at least 6 non-missing observations. Also require at least half of past research and development observations to be non-zero. RDAbility is the mean of the coefficients on the five lags of log(1+xrd/sale). Set to missing if firm is not in the highest tercile of xrd/sale in a year or if reseach and development expenses are non-positive. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RDAbility for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RDAbility; category=other; data=Accounting; evidence=t=2.6 in double sort. Review the generated entry before using it as a final public corpus item.
