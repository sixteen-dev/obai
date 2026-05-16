---
entry_type: strategy
id: openap_earnings_streak
canonical_name: Earnings surprise streak
aliases:
- EarnStreak
- Earnings surprise streak
- EarningsStreak
one_line: Cross-sectional equity anomaly that uses Earnings surprise streak to long
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
- Table 3's footnote says stocks remain in the relevant portfolio for 6 months, which
  we take to mean that earnings announcements more than 6 months old are not used.
  Announcements are quarterly anyway, so the rankings need to change quarterly. We
  reassign portfolios monthly because earnings announcements occur throughout the
  year. This was updated in 2021 February. Old version was much simpler.
- 'Original-paper replication evidence: t=9.5 in port sort ff3 alpha; reported long-short
  return=0.957, t-stat=9.51.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings surprise streak
  authors:
  - Loh
  - Warachka
  year: 2012
  venue: MS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings surprise streak is represented in the OpenAP signal catalog as a earnings growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Use fpi == 6 and only the last statpers for each anndats_act. Define surp = (actual - meanest)/price. Define a firm-anndats as a streak if surp has the same sign as the most recent surp observation. Keep only streaks. Then define signal = surp. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EarningsStreak for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarningsStreak; category=earnings growth; data=Accounting; evidence=t=9.5 in port sort ff3 alpha. Review the generated entry before using it as a final public corpus item.
