---
entry_type: strategy
id: openap_ind_mom
canonical_name: Industry Momentum
aliases:
- IndMom
- Industry Momentum
one_line: Cross-sectional equity anomaly that uses Industry Momentum to long high-signal
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
- More results on Table 3. OP's long port equally weights three industry portfolios,
  where the three industries are the top 3 according to the signal and the industry
  portfolios are value-weighted (similarly for the short port). We approximate this
  by equally weighting stocks. Performance is weaker, reminiscent of our Menzly and
  Ozbas replication.
- 'Original-paper replication evidence: t=4.6 in long-short; reported long-short return=0.43,
  t-stat=4.65.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Industry Momentum
  authors:
  - Grinblatt
  - Moskowitz
  year: 1999
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Industry Momentum is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Weighted average of firm-level 6 month buy-and-hold return. Average is taken over two digit industries each month and weights are based on market value of equity. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IndMom for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IndMom; category=momentum; data=Price; evidence=t=4.6 in long-short. Review the generated entry before using it as a final public corpus item.
