---
entry_type: strategy
id: openap_sin_algo
canonical_name: Sin Stock (selection criteria)
aliases:
- Sin Stock (selection criteria)
- SinStock
- sinAlgo
one_line: Cross-sectional equity anomaly that uses Sin Stock (selection criteria)
  to long high-signal stocks and short low-signal stocks.
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
- Follows OP and uses Compustat segment data plus naics codes. SinAlgo is equal to
  0 for "comparable stocks" (FF48 groups 2, 3, 7 and 43)
- 'Original-paper replication evidence: t-stat = 1.8 in LS nontraditional; reported
  long-short return=0.3, t-stat=2.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Sin Stock (selection criteria)
  authors:
  - Hong
  - Kacperczyk
  year: 2009
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Sin Stock (selection criteria) is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using Compustat Segment data, sinAlgo is defined as a binary variable equal to 1 if at least one segment of a firm is listed as being in at least one of the following industries: sic $\geq$ 2100 & sic $\leq$ 2199, sic $\geq$2080 & sic $\leq$ 2085, NAICS in \{7132, 71312, 713210, 71329, 713290, 72112, 721120\}. As in the original paper, we assume that the sin stock indicator applies to the entire history and future of the identified firm. sinAlgo is equal to 0 if the firm is not identified in the CS Segment data as a sin stock and if the firm is in one of the following industries: (sic $\geq$ 2000 & sic $\leq$ 2046) OR (sic $\geq$ 2050 & sic $\leq$ 2063) OR (sic $\geq$ 2070 & sic $\leq$ 2079) OR (sic $\geq$ 2090 & sic $\leq$ 2092) OR (sic $\geq$ 2095 & sic $\leq$ 2099) OR (sic $\geq$ 2064 & sic $\leq$ 2068) OR (sic $\geq$ 2086 & sic $\leq$ 2087) OR (sic $\geq$ 920 & sic $\leq$ 999) OR (sic $\geq$ 3650 & sic $\leq$ 3652) OR sic == 3732 OR (sic $\geq$ 3931 & sic $\leq$ 3932) OR (sic $\geq$ 3940 & sic $\leq$ 3949) OR (sic $\geq$ 7800 & sic $\leq$ 7833) OR (sic $\geq$ 7840 & sic $\leq$ 7841) OR (sic $\geq$ 7900 & sic $\leq$ 7911) OR (sic $\geq$ 7920 & sic $\leq$ 7933) OR (sic $\geq$ 7940 & sic $\leq$ 7949) OR sic $==$ 7980 OR (sic $\geq$ 7990 & sic $\leq$ 7999) The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute sinAlgo for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=sinAlgo; category=other; data=Other; evidence=t-stat = 1.8 in LS nontraditional. Review the generated entry before using it as a final public corpus item.
