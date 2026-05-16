---
entry_type: strategy
id: openap_cp_vol_spread
canonical_name: Call minus Put Vol
aliases:
- CPVolSpread
- Call minus Put Vol
one_line: Cross-sectional equity anomaly that uses Call minus Put Vol to long high-signal
  stocks and short low-signal stocks.
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
- OP says NYSE only but we find all stocks gets closer to their numbers
- 'Original-paper replication evidence: t=4 in port sort; reported long-short return=1.045,
  t-stat=4.2.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Options data is available, with a monthly
  rebalance workflow and a desire to test optionrisk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Call minus Put Vol
  authors:
  - Bali
  - Hovakimian
  year: 2009
  venue: MS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Call minus Put Vol is represented in the OpenAP signal catalog as a optionrisk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: ATM Call vol minus ATM put vol The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CPVolSpread for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CPVolSpread; category=optionrisk; data=Options; evidence=t=4 in port sort. Review the generated entry before using it as a final public corpus item.
