---
entry_type: strategy
id: openap_illiquidity
canonical_name: Amihud's illiquidity
aliases:
- Amihud's illiquidity
- Illiquid
- Illiquidity
one_line: Cross-sectional equity anomaly that uses Amihud's illiquidity to long high-signal
  stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=6.6 in univariate reg; reported long-short
  return=n/a, t-stat=6.6.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Amihud's illiquidity
  authors:
  - Amihud
  year: 2002
  venue: JFM
  url: https://www.openassetpricing.com/data/
---
## Thesis
Amihud's illiquidity is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Past twelve month average of: daily return (abs(ret)) divided by turnover((abs(prc)*vol) The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Illiquidity for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Illiquidity; category=liquidity; data=Trading; evidence=t=6.6 in univariate reg. Review the generated entry before using it as a final public corpus item.
