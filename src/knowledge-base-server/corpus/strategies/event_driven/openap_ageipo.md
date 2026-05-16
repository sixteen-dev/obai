---
entry_type: strategy
id: openap_ageipo
canonical_name: IPO and age
aliases:
- AgeIPO
- IPO and age
one_line: Cross-sectional equity anomaly that uses IPO and age to long high-signal
  stocks and short low-signal stocks.
category: event_driven
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires corporate-event data (earnings announcement
  dates, IPOs, spinoffs, mergers) and event-window logic that the OBaI backtest
  engine does not support natively. Use as routing reference; do not attempt backtest
  execution.
signal_inputs:
- OpenAP Event data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Tab IX event study, no t-stat, but the magnitudes are crazy. Table 9, matching firm
  adjusted returns three year. We follow MP. Hand returns use 40 for long port and
  5 for short port 36 month return for simplicity. OP uses 1,500 IPOs total, so this
  cut would only use about 600 events, which is borderline.
- 'Original-paper replication evidence: Event study, no t-stat; reported long-short
  return=0.972222222, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: IPO and age
  authors:
  - Ritter
  year: 1991
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
IPO and age is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Age is (current year - founding year from Jay Ritter's dataset). Exclude if IndIPO == 0. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AgeIPO for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AgeIPO; category=other; data=Event; evidence=Event study, no t-stat. Review the generated entry before using it as a final public corpus item.
