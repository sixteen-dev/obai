---
entry_type: strategy
id: openap_mom12m_off_season
canonical_name: Momentum without the seasonal part
aliases:
- Mom12mOffSeason
- Momentum without the seasonal part
one_line: Cross-sectional equity anomaly that uses Momentum without the seasonal part
  to long high-signal stocks and short low-signal stocks.
category: momentum
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
- This acronym has a different form than the other off season Heston and Sadka ones
  because its behavior is distinct. The other off season signals behave like long-term
  reversal.
- 'Original-paper replication evidence: t=4 in port sort; reported long-short return=1.17,
  t-stat=4.2.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Momentum without the seasonal part
  authors:
  - Heston
  - Sadka
  year: 2008
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Momentum without the seasonal part is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average return in other months over the previous year. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Mom12mOffSeason for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Mom12mOffSeason; category=other; data=Price; evidence=t=4 in port sort. Review the generated entry before using it as a final public corpus item.
