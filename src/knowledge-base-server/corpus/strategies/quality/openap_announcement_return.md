---
entry_type: strategy
id: openap_announcement_return
canonical_name: Earnings announcement return
aliases:
- AnnounRet
- AnnouncementReturn
- Earnings announcement return
one_line: Cross-sectional equity anomaly that uses Earnings announcement return to
  long high-signal stocks and short low-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 4 has port sort but no t-stats. Tab 7 has huge t-stats in regressions
- 'Original-paper replication evidence: t=9.3 in regression; reported long-short return=n/a,
  t-stat=9.25.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test earnings event effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings announcement return
  authors:
  - Chan, Jegadeesh
  - Lakonishok
  year: 1996
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings announcement return is represented in the OpenAP signal catalog as a earnings event predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Get announcement date for quarterly earnings from IBES (fpi = 6). AnnouncementReturn is the sum of (ret - mktrf + rf) from one day before an earnings announcement to 2 days after the announcement. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AnnouncementReturn for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AnnouncementReturn; category=earnings event; data=Price; evidence=t=9.3 in regression. Review the generated entry before using it as a final public corpus item.
