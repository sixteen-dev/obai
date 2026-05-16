---
entry_type: strategy
id: openap_firm_age
canonical_name: Firm age based on CRSP
aliases:
- Firm age based on CRSP
- FirmAge
one_line: Cross-sectional equity anomaly that uses Firm age based on CRSP to long
  low-signal stocks and short high-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires specialized data inputs (short interest, lending
  fees, or other alternative datasets) that the OBaI backtest engine does not ingest.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Other data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OP uses special NYSE archive data that we lack.
- 'Original-paper replication evidence: t=2.5 in reg nonstandard data; reported long-short
  return=n/a, t-stat=2.48.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test info proxy effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Firm age based on CRSP
  authors:
  - Barry
  - Brown
  year: 1984
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Firm age based on CRSP is represented in the OpenAP signal catalog as a info proxy predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Months since start of CRSP coverage. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute FirmAge for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=FirmAge; category=info proxy; data=Other; evidence=t=2.5 in reg nonstandard data. Review the generated entry before using it as a final public corpus item.
