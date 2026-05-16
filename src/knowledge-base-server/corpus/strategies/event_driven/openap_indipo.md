---
entry_type: strategy
id: openap_indipo
canonical_name: Initial Public Offerings
aliases:
- IndIPO
- Initial Public Offerings
one_line: Cross-sectional equity anomaly that uses Initial Public Offerings to long
  low-signal stocks and short high-signal stocks.
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
- 'Tab II event study t-stat 5 at 1 year. Slightly different than MP. Our construction
  is closest in the spirit of Ritter''s Table II. A few issues. Main thing: sample
  should end in 1987, since the main table is for returns of stocks which IPO''d 1975-1984,
  and the table follows the stocks for 3 years (past 1984). Also: using minimum of
  3 months since IPO helps.'
- 'Original-paper replication evidence: t=4 in event study; reported long-short return=0.8525,
  t-stat=3.97.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Initial Public Offerings
  authors:
  - Ritter
  year: 1991
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Initial Public Offerings is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 1 if IPO in the past 6-36 months. 0 otherwise. IPO dates are taken from Jay Ritter's IPO data available at: http://bear.warrington.ufl.edu/ritter/ipodata.htm. Missing IPO dates imply IndIPO = 0 The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IndIPO for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IndIPO; category=external financing; data=Event; evidence=t=4 in event study. Review the generated entry before using it as a final public corpus item.
