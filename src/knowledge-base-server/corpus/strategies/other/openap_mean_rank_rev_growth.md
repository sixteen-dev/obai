---
entry_type: strategy
id: openap_mean_rank_rev_growth
canonical_name: Revenue Growth Rank
aliases:
- MeanRankRevGrowth
- RevGrowth
- Revenue Growth Rank
one_line: Cross-sectional equity anomaly that uses Revenue Growth Rank to long high-signal
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
- Lots of supporting results, but not exactly what we do. Tab 6 panel 2 finds t=4.5
  using 3x3 sort with CF and LS corners.
- 'Original-paper replication evidence: t=4.5 in double sort; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test sales growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Revenue Growth Rank
  authors:
  - Lakonishok, Shleifer, Vishny
  year: 1994
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Revenue Growth Rank is represented in the OpenAP signal catalog as a sales growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rank firms by their annual revenue growth each year over the past 5 years. MeanRankRevGrowth is the weighted average of ranks over the past 5 years, that is, MeanRankRevGrowth = (5*Rank$_{t-1}$ + 4*Rank$_{t-2}$ + 3*Rank$_{t-3}$ + 2*Rank$_{t-4}$ + 1*Rank$_{t-5}$)/15. Exclude NASDAQ stocks. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MeanRankRevGrowth for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MeanRankRevGrowth; category=sales growth; data=Accounting; evidence=t=4.5 in double sort. Review the generated entry before using it as a final public corpus item.
