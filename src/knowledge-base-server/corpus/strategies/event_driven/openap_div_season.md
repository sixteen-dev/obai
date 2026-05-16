---
entry_type: strategy
id: openap_div_season
canonical_name: Dividend seasonality
aliases:
- DivSeason
- Dividend seasonality
one_line: Cross-sectional equity anomaly that uses Dividend seasonality to long high-signal
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=16 in long-short; reported long-short return=0.36,
  t-stat=16.19363096.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test payout indicator effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Dividend seasonality
  authors:
  - Hartzmark
  - Salomon
  year: 2013
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Dividend seasonality is represented in the OpenAP signal catalog as a payout indicator predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Drop if 3rd digit of distcd = 2 or >= 6. Assign DivSeason = 0 if there was a dividend paid in the last 12 months. Replace DivSeason = 1 if the third digit of disctcd is 3, 0, or 1, and a positive dividend was paid 2, 5, 8, or 11 months ago, if the third digit is 4 and a dividend was paid 5 or 11 months ago, or if the third digit is 5 and a dividiend was paid 11 months ago. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DivSeason for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DivSeason; category=payout indicator; data=Event; evidence=t=16 in long-short. Review the generated entry before using it as a final public corpus item.
