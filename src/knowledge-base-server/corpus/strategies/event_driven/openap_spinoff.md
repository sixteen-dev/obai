---
entry_type: strategy
id: openap_spinoff
canonical_name: Spinoffs
aliases:
- Spinoff
- Spinoffs
one_line: Cross-sectional equity anomaly that uses Spinoffs to long high-signal stocks
  and short low-signal stocks.
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
- OP uses CCH Capital Changes Reporter, but we use CRSP acquisition file. More importantly
  OP excludes about 75% of spinoffs because they are not "pure" spinoffs, but we do
  not in order to keep a reasonable amount of stocks. Tab 3B event study uses matched-firm-adjusted
  returns and holds for 2 years, for only 140 spinoffs. Predictability is a judgment
  call, but if VW gets a t-stat of 2.43, it seems it should have a 50/50 shot.
- 'Original-paper replication evidence: t=2.3 in event study; reported long-short
  return=2.083333333, t-stat=2.43.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Spinoffs
  authors:
  - Cusatis, Miles
  - Woolridge
  year: 1993
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Spinoffs is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Spinoffs are identified as all observations in the CRSP acquisition file with valid acperm entry. Spinoff is a binary variable equal to 1 if a firm is identified in the CRSP Acquisition data and if it has at most two years of history in the CRSP stock return data. Spinoff is equal to 0 otherwise. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Spinoff for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Spinoff; category=other; data=Event; evidence=t=2.3 in event study. Review the generated entry before using it as a final public corpus item.
