---
entry_type: strategy
id: openap_smile_slope
canonical_name: Put volatility minus call volatility
aliases:
- OSmirkCP
- Put volatility minus call volatility
- SmileSlope
one_line: Cross-sectional equity anomaly that uses Put volatility minus call volatility
  to long low-signal stocks and short high-signal stocks.
category: low_volatility
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires options-chain data (implied volatility, open
  interest, put-call ratios) and the OBaI backtest engine has no options-chain integration.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Options data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=8 in port sort; reported long-short return=1.8,
  t-stat=8.168.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Options data is available, with a monthly
  rebalance workflow and a desire to test optionrisk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Put volatility minus call volatility
  authors:
  - Yan
  year: 2011
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Put volatility minus call volatility is represented in the OpenAP signal catalog as a optionrisk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using OptionMetrics's daily volatility surfaces (vsurfd), keep last observation each month, delta = 0.50 or -0.50, and days to expiration = 30. The signal is then the difference between put implied vol and call implied vol. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute SmileSlope for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=SmileSlope; category=optionrisk; data=Options; evidence=t=8 in port sort. Review the generated entry before using it as a final public corpus item.
