---
entry_type: strategy
id: openap_vol_mkt
canonical_name: Volume to market equity
aliases:
- VolMkt
- Volume to market equity
- Volume2Mkt
one_line: Cross-sectional equity anomaly that uses Volume to market equity to long
  low-signal stocks and short high-signal stocks.
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
- OP reports mean regression coeff across 90 multiple regressions.
- 'Original-paper replication evidence: t=4 in mv reg nonstandard; reported long-short
  return=n/a, t-stat=4.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test volume effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Volume to market equity
  authors:
  - Haugen
  - Baker
  year: 1996
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Volume to market equity is represented in the OpenAP signal catalog as a volume predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average monthly dollar trading volume (vol*abs(prc)) over the previous 12 months, scaled by market value of equity. Exclude if price less than 5. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute VolMkt for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=VolMkt; category=volume; data=Trading; evidence=t=4 in mv reg nonstandard. Review the generated entry before using it as a final public corpus item.
