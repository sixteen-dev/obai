---
entry_type: strategy
id: openap_rio_disp
canonical_name: Inst Own and Forecast Dispersion
aliases:
- Inst Own and Forecast Dispersion
- RIO_Disp
one_line: Cross-sectional equity anomaly that uses Inst Own and Forecast Dispersion
  to long high-signal stocks and short low-signal stocks.
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
- We deviate a bit to avoid getting into IBES detail file. Since we only use the summary
  file, we screen stdev > 0 and also keep both 4th and 5th quintiles of dispersion.
- 'Original-paper replication evidence: t = 2.47 in conditional sort; reported long-short
  return=0.54, t-stat=2.47.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test short sale constraints effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inst Own and Forecast Dispersion
  authors:
  - Nagel
  year: 2005
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inst Own and Forecast Dispersion is represented in the OpenAP signal catalog as a short sale constraints predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Follow RIO\_MB, except define Disp as the stdev of IBES forecasts where fpi == 1 divided by at, and sort on Disp instead of MB. Finally, let RIO\_Disp = lagged RIO quntile if the Disp quintile >= 4. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RIO_Disp for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RIO_Disp; category=short sale constraints; data=13F; evidence=t = 2.47 in conditional sort. Review the generated entry before using it as a final public corpus item.
