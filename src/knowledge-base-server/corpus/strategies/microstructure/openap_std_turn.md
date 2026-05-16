---
entry_type: strategy
id: openap_std_turn
canonical_name: Share turnover volatility
aliases:
- Share turnover volatility
- TurnovVol
- std_turn
one_line: Cross-sectional equity anomaly that uses Share turnover volatility to long
  low-signal stocks and short high-signal stocks.
category: microstructure
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Trading data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- CVTURN and TURN in OP. Tab 3B has port sort but no LS or t-stats. Tab 5B has FM
  reg.
- 'Original-paper replication evidence: t=3.7 in regression; reported long-short return=n/a,
  t-stat=3.74.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Share turnover volatility
  authors:
  - Chordia, Subra, Anshuman
  year: 2001
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Share turnover volatility is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Standard deviation of turnover (vol/shrout) over the past 36 months. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute std_turn for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=std_turn; category=liquidity; data=Trading; evidence=t=3.7 in regression. Review the generated entry before using it as a final public corpus item.
