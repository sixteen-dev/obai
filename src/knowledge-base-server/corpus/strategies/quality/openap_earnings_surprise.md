---
entry_type: strategy
id: openap_earnings_surprise
canonical_name: Earnings Surprise
aliases:
- EarnSurp
- Earnings Surprise
- EarningsSurprise
one_line: Cross-sectional equity anomaly that uses Earnings Surprise to long high-signal
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
- No LS but very strong return pattern
- 'Original-paper replication evidence: huge spread in event study; reported long-short
  return=2.975, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings Surprise
  authors:
  - Foster, Olsen
  - Shevlin
  year: 1984
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings Surprise is represented in the OpenAP signal catalog as a earnings growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: EPS (epspxq) minus EPS twelve months ago - Drift, scaled by standard deviation of that expression. Drift is the average earnings growth (EPS - EPS twelve months ago) over the past two years. Exclude if price less than 5 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EarningsSurprise for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarningsSurprise; category=earnings growth; data=Accounting; evidence=huge spread in event study. Review the generated entry before using it as a final public corpus item.
