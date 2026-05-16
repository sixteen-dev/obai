---
entry_type: strategy
id: openap_prob_informed_trading
canonical_name: Probability of Informed Trading
aliases:
- PIN
- ProbInformedTrading
- Probability of Informed Trading
one_line: Cross-sectional equity anomaly that uses Probability of Informed Trading
  to long high-signal stocks and short low-signal stocks.
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
- Table 3 has double sorts with size but no t-stats. Sign of predictability depends
  on size, only increases for bottom 3 size quintiles. Table 6 has mv reg, t=2.5 controlling
  for size, but size has t-stat of 2.8.
- 'Original-paper replication evidence: t=2.5 in mv reg; reported long-short return=n/a,
  t-stat=2.496.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Probability of Informed Trading
  authors:
  - Easley, Hvidkjaer
  - O'Hara
  year: 2002
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Probability of Informed Trading is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Download parameter estimates from the archived version of Hvidkjaers website (https://web.archive.org/web/20110219024112/http://sites.google.com/site/hvidkjaer/data/data-files/pin1983-2001.zip?attredirects=0), and the page for Duarte, Hu, Young, JFE 2020 https://edwinhu.github.io/pin/ and apply Easley et al's Equation 5. Use Hvidkjaers data when available, otherwise use Duarte et al. Use Year t PIN estimates to forecast returns throughout Year t+1 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ProbInformedTrading for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ProbInformedTrading; category=liquidity; data=Trading; evidence=t=2.5 in mv reg. Review the generated entry before using it as a final public corpus item.
