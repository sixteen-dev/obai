---
entry_type: strategy
id: openap_return_skewqf
canonical_name: Idiosyncratic skewness (Q model)
aliases:
- Idiosyncratic skewness (Q model)
- RetSkewQF
- ReturnSkewQF
one_line: Cross-sectional equity anomaly that uses Idiosyncratic skewness (Q model)
  to rank stocks by the signal and form the source-defined long-short spread.
category: low_volatility
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
- OP uses 3F
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Idiosyncratic skewness (Q model)
  authors:
  - Bali, Engle
  - Murray
  year: 2015
  venue: Book
  url: https://www.openassetpricing.com/data/
---
## Thesis
Idiosyncratic skewness (Q model) is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Skewness of idiosyncratic returns computed as residuals from regression of daily excess returns (ret - rf) on q-factors (r\_mkt, r\_me, r\_ia, r\_roe) over the previous month. We require at least 15 non-missing observations. We download q-factor data from \url{http://global-q.org/index.html}. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute ReturnSkewQF for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ReturnSkewQF; category=risk; data=Price; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
