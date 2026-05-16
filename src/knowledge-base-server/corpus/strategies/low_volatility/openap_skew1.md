---
entry_type: strategy
id: openap_skew1
canonical_name: Volatility smirk near the money
aliases:
- OSmirkNTM
- Volatility smirk near the money
- skew1
one_line: Cross-sectional equity anomaly that uses Volatility smirk near the money
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
- Tab3A shows weekly ret LS t-stat of 2.19. Screens in appendix are important.
- 'Original-paper replication evidence: t = 2.19 in port sort; reported long-short
  return=0.64, t-stat=2.19.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Options data is available, with a monthly
  rebalance workflow and a desire to test optionrisk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Volatility smirk near the money
  authors:
  - Xing, Zhang
  - Zhao
  year: 2010
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Volatility smirk near the money is represented in the OpenAP signal catalog as a optionrisk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using OptionMetrics data, among options with duration between 10 and 60 days, implied volatility of put option with moneyness closest to but above 1 minus implied volatility of call option with moneyness closest to but below 1. Keep only volume > 0, implied vol between 0.03 and 2.0, price > 0.125, open interest > 0. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute skew1 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=skew1; category=optionrisk; data=Options; evidence=t = 2.19 in port sort. Review the generated entry before using it as a final public corpus item.
