---
entry_type: strategy
id: openap_coskewness
canonical_name: Coskewness
aliases:
- Coskew
- Coskewness
one_line: Cross-sectional equity anomaly that uses Coskewness to long low-signal stocks
  and short high-signal stocks.
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
- The text reports 3.60 percent annual hedge return and p value < 0.05 (page 1278),
  but no table or further details. OP states CAPM residuals are used, but we find
  we replicate OP very closely using simple de-meaning, which follow ACX. ACX report
  private conversations with Harvey about replicating Coskewness in their paper.
- 'Original-paper replication evidence: p-val<0.05 in long-short; reported long-short
  return=0.3, t-stat=1.96.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Coskewness
  authors:
  - Harvey
  - Siddique
  year: 2000
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Coskewness is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Signal is the sample counterpart of $E[\tilde{r}_{it} \tilde{r}_{mt}^2]/( SD[\tilde{r}_{it} ] SD[\tilde{r}_{mt}]^2$ where $\tilde{r}_{it}$ is the de-meaned stock excess return and $\tilde{r}_{mt}$ is the de-meaned market excess return. Signal is computed using the past 60 months of monthly data, and using the NYSE/AMEX CRSP VW index for the market (msic). See code for details. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Coskewness for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Coskewness; category=risk; data=Price; evidence=p-val<0.05 in long-short. Review the generated entry before using it as a final public corpus item.
