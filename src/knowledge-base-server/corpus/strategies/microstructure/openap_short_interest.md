---
entry_type: strategy
id: openap_short_interest
canonical_name: Short Interest
aliases:
- Short Interest
- ShortInterest
one_line: Cross-sectional equity anomaly that uses Short Interest to long low-signal
  stocks and short high-signal stocks.
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
- Table 1, Panel A has a low- and high- short abnormal return from pooled observations
  (not calendar time). Standard errors are shown by group and range from 0.8 to 3.7
  percent. Hard to say since it's so far from our calendar time ports. Most of the
  paper is about the correlation between short interest and valuations ratios. Strange
  case where OP does not invesigate predictability much, but predictability is very
  strong.
- 'Original-paper replication evidence: 35 bps spread in port sort; reported long-short
  return=0.35, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test short sale constraints effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Short Interest
  authors:
  - Dechow et al.
  year: 2001
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Short Interest is represented in the OpenAP signal catalog as a short sale constraints predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Short-interest from Compustat (shortint) scaled by shares outstanding (shrout). Short-interest data are available bi-weekly with a four day lag. We use the mid-month observation to make sure data would be available in real time. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ShortInterest for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ShortInterest; category=short sale constraints; data=Trading; evidence=35 bps spread in port sort. Review the generated entry before using it as a final public corpus item.
