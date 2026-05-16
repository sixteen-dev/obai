---
entry_type: strategy
id: openap_dol_vol
canonical_name: Past trading volume
aliases:
- DolVol
- Past trading volume
- VolumeDol
one_line: Cross-sectional equity anomaly that uses Past trading volume to long low-signal
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
- OP is an exploration style paper, and DolVol is just one predictor. Tables 4-6 have
  related results, but Table 6 is the simplest. Table 6A has t=2.86 for NYSE subsample,
  Table 6B has t=2.6 for NASDAQ subsample, we write down 2.86, but use NYSE and NASDAQ
  in our ports.
- 'Original-paper replication evidence: t=2.9 in regression; reported long-short return=n/a,
  t-stat=2.86.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test volume effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Past trading volume
  authors:
  - Brennan, Chordia, Subra
  year: 1998
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Past trading volume is represented in the OpenAP signal catalog as a volume predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Log of two-month lagged trading volume (vol) times two-month lagged price (prc). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DolVol for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DolVol; category=volume; data=Trading; evidence=t=2.9 in regression. Review the generated entry before using it as a final public corpus item.
