---
entry_type: strategy
id: openap_downside_beta
canonical_name: Downside beta
aliases:
- Downside beta
- DownsideBeta
- betaDown
one_line: Cross-sectional equity anomaly that uses Downside beta to long high-signal
  stocks and short low-signal stocks.
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
- Tables 1-7 show only realized beta-. Only on Table 8 do you see past beta- which
  is insignificant.
- 'Original-paper replication evidence: t=0.6 in port sort; reported long-short return=0.11,
  t-stat=0.6.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Downside beta
  authors:
  - Ang, Chen
  - Xing
  year: 2006
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Downside beta is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Beta of Daily stock return (ret - rf) regressed on market return (mktrf) for days for which the market return was less than the average market return over the previous year. Rolling window of 252 trading days with at least 50 observations. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DownsideBeta for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DownsideBeta; category=risk; data=Price; evidence=t=0.6 in port sort. Review the generated entry before using it as a final public corpus item.
