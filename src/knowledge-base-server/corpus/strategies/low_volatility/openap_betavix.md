---
entry_type: strategy
id: openap_betavix
canonical_name: Systematic volatility
aliases:
- Systematic volatility
- betaVIX
one_line: Cross-sectional equity anomaly that uses Systematic volatility to long low-signal
  stocks and short high-signal stocks.
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
- Tab I has port sorts
- 'Original-paper replication evidence: t=3.9 in port sort; reported long-short return=1.04,
  t-stat=3.9.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test volatility effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Systematic volatility
  authors:
  - Ang et al.
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Systematic volatility is represented in the OpenAP signal catalog as a volatility predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Coefficient on daily change in the VIX of a 1-month rolling window regression of daily stock excess returns on market return and the daily change in the CBOE S&P 100 volatility index (downloaded from FRED). Require at least 15 non-missing observations. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute betaVIX for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=betaVIX; category=volatility; data=Price; evidence=t=3.9 in port sort. Review the generated entry before using it as a final public corpus item.
