---
entry_type: strategy
id: openap_price_delay_rsq
canonical_name: Price delay r square
aliases:
- Price delay r square
- PriceDelayRsq
one_line: Cross-sectional equity anomaly that uses Price delay r square to long high-signal
  stocks and short low-signal stocks.
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
- Called D1 in paper. Our primary delay measure, we use the daily stock level version.
  OP focuses on complicated two-stage portfolio-based version
- 'Original-paper replication evidence: t =3.4 in port sort char adj; reported long-short
  return=0.31, t-stat=3.4.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Price delay r square
  authors:
  - Hou
  - Moskowitz
  year: 2005
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Price delay r square is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Each July regress stock return on day $t$ on market return in $t, t-1, \ldots, t-4$ using observations from July 1 of the previous year to June 30 of the current year. Then regress again with no market return lags. PriceDelay Rsq = 1 - [Rsq from second regression]/[Rsq from first regression] The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PriceDelayRsq for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PriceDelayRsq; category=lead lag; data=Price; evidence=t =3.4 in port sort char adj. Review the generated entry before using it as a final public corpus item.
