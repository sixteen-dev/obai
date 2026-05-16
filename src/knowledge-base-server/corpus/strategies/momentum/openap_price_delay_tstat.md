---
entry_type: strategy
id: openap_price_delay_tstat
canonical_name: Price delay SE adjusted
aliases:
- Price delay SE adjusted
- PriceDelayAdj
- PriceDelayTstat
one_line: Cross-sectional equity anomaly that uses Price delay SE adjusted to long
  high-signal stocks and short low-signal stocks.
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
- see PriceDelayRsq. Called D3 in paper. Related statistics shown in Table 2, but
  not our daily version. Table 2 shows daily vs two-stage weekly makes a huge difference.
- 'Original-paper replication evidence: t =7.39 in port sort w/ complicated signal;
  reported long-short return=1.1, t-stat=7.39.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Price delay SE adjusted
  authors:
  - Hou
  - Moskowitz
  year: 2005
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Price delay SE adjusted is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Each July regress daily stock return (ret) on market return (mktrf) in $t, t-1, \ldots, t-4$ using observations from July 1 of the previous year to June 30 of the current year. Trim the highest and lowest 1% of estimated coefficients, standard errors, and t-stats. Define PriceDelayTstat as the ratio of [1*tstat on mktrf$_t-1$ + 2*tstat on mktrf$_t-2$ + 3*tstat on mktrf$_t-3$ + 4*tstat on mktrf$_t-4$], and [tstat on mktrf$_t$ + tstat on mktrf$_t-1$ + tstat on mktrf$_t-2$ + tstat on mktrf$_t-3$ + tstat on mktrf$_t-4$]. Finally winsor the extreme 10 and 90 percentiles each month. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PriceDelayTstat for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PriceDelayTstat; category=lead lag; data=Price; evidence=t =7.39 in port sort w/ complicated signal. Review the generated entry before using it as a final public corpus item.
