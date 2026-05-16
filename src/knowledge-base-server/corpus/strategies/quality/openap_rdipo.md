---
entry_type: strategy
id: openap_rdipo
canonical_name: IPO and no R&D spending
aliases:
- IPO and no R&D spending
- RDIPO
one_line: Cross-sectional equity anomaly that uses IPO and no R&D spending to long
  low-signal stocks and short high-signal stocks.
category: quality
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=2.68 in port sort FF3+Mom alpha; reported
  long-short return=0.76, t-stat=2.68.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test R&D effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: IPO and no R&D spending
  authors:
  - Gou, Lev
  - Shi
  year: 2006
  venue: JBFA
  url: https://www.openassetpricing.com/data/
---
## Thesis
IPO and no R&D spending is represented in the OpenAP signal catalog as a R&D predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if R&D expense (xrd) = 0 and IndIPO = 1. 0 otherwise. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RDIPO for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RDIPO; category=R&D; data=Event; evidence=t=2.68 in port sort FF3+Mom alpha. Review the generated entry before using it as a final public corpus item.
