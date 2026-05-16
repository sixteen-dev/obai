---
entry_type: strategy
id: openap_exch_switch
canonical_name: Exchange Switch
aliases:
- ExchSwitch
- Exchange Switch
one_line: Cross-sectional equity anomaly that uses Exchange Switch to long low-signal
  stocks and short high-signal stocks.
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
- Fig 1 shows month 1 has most negative expected return. Number of events approx 3,000,
  so this should work in portfolios.
- 'Original-paper replication evidence: t = 3.6 in event study; reported long-short
  return=0.455, t-stat=3.61.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Exchange Switch
  authors:
  - Dharan
  - Ikenberry
  year: 1995
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Exchange Switch is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if a firm switched from AMEX or NASDAQ to NYSE within the past year, or from NASDAQ to AMEX within the past year. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ExchSwitch for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ExchSwitch; category=other; data=Event; evidence=t = 3.6 in event study. Review the generated entry before using it as a final public corpus item.
