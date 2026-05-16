---
entry_type: strategy
id: openap_div_init
canonical_name: Dividend Initiation
aliases:
- DivInit
- Dividend Initiation
one_line: Cross-sectional equity anomaly that uses Dividend Initiation to long high-signal
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
- We deviate from OP in not imposing an NYSE/AMEX requirement. This allows our portfolios
  to have a reasonable number of stocks. We also "hold" for 6 months rather than 12,
  since most of the returns come in the first 6 months.
- 'Original-paper replication evidence: t=3.4 in event study; reported long-short
  return=0.625, t-stat=3.37.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test payout indicator effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Dividend Initiation
  authors:
  - Michaely, Thaler
  - Womack
  year: 1995
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Dividend Initiation is represented in the OpenAP signal catalog as a payout indicator predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Keep only distcd 2nd digit = 2 or 3. Define dividend initiation as having paid a dividend in month t (divamt > 0), and not having paid a dividend in the last 24 months. DivInit is equal to 1 if a dividend was initiated in the past 6 months, and 0 for all other stocks. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DivInit for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DivInit; category=payout indicator; data=Event; evidence=t=3.4 in event study. Review the generated entry before using it as a final public corpus item.
