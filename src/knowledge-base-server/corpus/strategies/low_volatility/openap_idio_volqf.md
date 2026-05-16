---
entry_type: strategy
id: openap_idio_volqf
canonical_name: Idiosyncratic risk (q factor)
aliases:
- IdioVolQF
- Idiosyncratic risk (q factor)
one_line: Cross-sectional equity anomaly that uses Idiosyncratic risk (q factor) to
  rank stocks by the signal and form the source-defined long-short spread.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test volatility effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Idiosyncratic risk (q factor)
  authors:
  - Ang et al.
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Idiosyncratic risk (q factor) is represented in the OpenAP signal catalog as a volatility predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Standard deviation of residuals from q-factor regressions using the past month of daily data. Value weighted The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute IdioVolQF for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IdioVolQF; category=volatility; data=Price; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
