---
entry_type: strategy
id: openap_rio_turnover
canonical_name: Inst Own and Turnover
aliases:
- Inst Own and Turnover
- RIO_Turnover
one_line: Cross-sectional equity anomaly that uses Inst Own and Turnover to long high-signal
  stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires institutional-holdings (13F) data that the
  OBaI backtest engine does not ingest. Use as routing reference; do not attempt
  backtest execution.
signal_inputs:
- OpenAP 13F data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t = 2.71 in conditional sort; reported long-short
  return=0.92, t-stat=2.71.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test short sale constraints effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inst Own and Turnover
  authors:
  - Nagel
  year: 2005
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inst Own and Turnover is represented in the OpenAP signal catalog as a short sale constraints predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Follow RIO\_MB, except define turnover as vol/shrout. Let RIO\_Turnover = lagged RIO quintile if the Volatility quintile == 5 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RIO_Turnover for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RIO_Turnover; category=short sale constraints; data=13F; evidence=t = 2.71 in conditional sort. Review the generated entry before using it as a final public corpus item.
