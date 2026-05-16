---
entry_type: strategy
id: openap_earnings_consistency
canonical_name: Earnings consistency
aliases:
- EarnCons
- Earnings consistency
- EarningsConsistency
one_line: Cross-sectional equity anomaly that uses Earnings consistency to long high-signal
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
- Could not access OP, so we used the Alwathainani's dissertation from VCU. We follow
  MP, which is simpler than OP.
- 'Original-paper replication evidence: t=2.7 in complicated LS port; reported long-short
  return=0.360833333, t-stat=2.67.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings consistency
  authors:
  - Alwathainani
  year: 2009
  venue: BAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings consistency is represented in the OpenAP signal catalog as a earnings growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average earnings growth over previous 48 months. Earnings growth is defined as EPS (epspx) minus EPS 12 months ago divided by average EPS 12 and 24 months ago. Exclude if price less than 5, absolute value of 12 month earnings growth greater 600%, or earnings growth and earnings growth 12 months ago have different signs. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EarningsConsistency for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarningsConsistency; category=earnings growth; data=Accounting; evidence=t=2.7 in complicated LS port. Review the generated entry before using it as a final public corpus item.
