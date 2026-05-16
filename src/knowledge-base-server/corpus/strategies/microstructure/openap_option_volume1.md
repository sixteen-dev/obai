---
entry_type: strategy
id: openap_option_volume1
canonical_name: Option to stock volume
aliases:
- OptVol
- Option to stock volume
- OptionVolume1
one_line: Cross-sectional equity anomaly that uses Option to stock volume to long
  low-signal stocks and short high-signal stocks.
category: microstructure
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
- OP is actually weekly, and sorts in deciles, then longs 9+10 and shorts 1+2
- 'Original-paper replication evidence: t = 3.45 in port sort CAPM alpha weekly; reported
  long-short return=0.916, t-stat=3.45.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Options data is available, with a monthly
  rebalance workflow and a desire to test volume effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Option to stock volume
  authors:
  - Johnson
  - So
  year: 2012
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Option to stock volume is represented in the OpenAP signal catalog as a volume predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Total monthly option volume (volume) over all puts and calls, divided by monthly stock trading volume (vol). Exclude if price less than 1 or share code greater 11 or option volume or stock volume data are missing for the previous month. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OptionVolume1 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OptionVolume1; category=volume; data=Options; evidence=t = 3.45 in port sort CAPM alpha weekly. Review the generated entry before using it as a final public corpus item.
