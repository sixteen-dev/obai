---
entry_type: strategy
id: openap_coskewacx
canonical_name: Coskewness using daily returns
aliases:
- CoskewACX
- Coskewness using daily returns
one_line: Cross-sectional equity anomaly that uses Coskewness using daily returns
  to long low-signal stocks and short high-signal stocks.
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
- Used primarily as a control variable in the paper, but this paper has details on
  the signal construction that are not found in Harvey and Siddique. ACX simply use
  de-meaned stock returns rather than CAPM residual. Also uses daily data, and equal
  weighting.
- 'Original-paper replication evidence: t=2.8 in port sort; reported long-short return=0.28,
  t-stat=2.76.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Coskewness using daily returns
  authors:
  - Ang, Chen
  - Xing
  year: 2006
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Coskewness using daily returns is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Signal is the sample counterpart of $E[\tilde{r}_{it} \tilde{r}_{mt}^2]/( SD[\tilde{r}_{it} ] SD[\tilde{r}_{mt}]^2$ where $\tilde{r}_{it}$ is the de-meaned stock return and $\tilde{r}_{mt}$ is the de-meaned market excess return. Signal is computed using the past year of daily data, and using the NYSE CRSP VW index for the market (dsia), with returns continuously compounded. See code for details. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CoskewACX for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CoskewACX; category=risk; data=Price; evidence=t=2.8 in port sort. Review the generated entry before using it as a final public corpus item.
