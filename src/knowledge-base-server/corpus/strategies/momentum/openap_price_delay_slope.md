---
entry_type: strategy
id: openap_price_delay_slope
canonical_name: Price delay coeff
aliases:
- Price delay coeff
- PriceDelay
- PriceDelaySlope
one_line: Cross-sectional equity anomaly that uses Price delay coeff to long high-signal
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
- see PriceDelayRsq. Called D2 in paper. Related statistics shown in Table 2, but
  not our daily version. Table 2 shows daily vs two-stage weekly makes a huge difference.
- 'Original-paper replication evidence: t =7.7 in port sort w/ complicated signal;
  reported long-short return=1.21, t-stat=7.7.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Price delay coeff
  authors:
  - Hou
  - Moskowitz
  year: 2005
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Price delay coeff is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Each July regress daily stock return (ret) on market return (mktrf) in $t, t-1, \ldots, t-4$ with observations over the previous year. Define PriceDelaySlope as the ratio of ]1*beta on mktrf$_t-1$ + 2*beta on mktrf$_t-2$ + 3*beta on mktrf$_t-3$ + 4*beta on mktrf$_t-4$], and [beta on mktrf$_t$ + beta on mktrf$_t-1$ + beta on mktrf$_t-2$ + beta on mktrf$_t-3$ + beta on mktrf$_t-4$]. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PriceDelaySlope for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PriceDelaySlope; category=lead lag; data=Price; evidence=t =7.7 in port sort w/ complicated signal. Review the generated entry before using it as a final public corpus item.
