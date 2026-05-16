---
entry_type: strategy
id: openap_chn_analyst
canonical_name: Decline in Analyst Coverage
aliases:
- ChNAnalyst
- Decline in Analyst Coverage
one_line: Cross-sectional equity anomaly that uses Decline in Analyst Coverage to
  long low-signal stocks and short high-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Tab 2 says t=0.3 for all stocks, but t>3 for size quintiles 1-2
- 'Original-paper replication evidence: t > 3 in port sort FF3 alpha for small stocks;
  reported long-short return=0.46, t-stat=3.34.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings event effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Decline in Analyst Coverage
  authors:
  - Scherbina
  year: 2008
  venue: ROF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Decline in Analyst Coverage is represented in the OpenAP signal catalog as a earnings event predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using IBES forecasts, keep if fpi = 1, fpedats not missing, and fpedats > statpers + 30. Binary variable equal to 1 if the number of analysts (numest) for next quarter's EPS estimate decreased relative to three months ago, and 0 if it increased. Keep if in bottom two size quintiles among all firms to match OP's table. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChNAnalyst for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChNAnalyst; category=earnings event; data=Analyst; evidence=t > 3 in port sort FF3 alpha for small stocks. Review the generated entry before using it as a final public corpus item.
