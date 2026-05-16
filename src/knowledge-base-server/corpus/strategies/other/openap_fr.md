---
entry_type: strategy
id: openap_fr
canonical_name: Pension Funding Ratio
aliases:
- FR
- Pension Funding Status
- PensionFunding
one_line: Cross-sectional equity anomaly that uses Pension Funding Status to long
  high-signal stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires firm-level accounting data (balance sheet,
  income statement, cash-flow items) that the OBaI backtest engine does not ingest.
  The engine consumes OHLCV bars on daily/intraday timeframes only. Use as routing
  reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Accounting data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 2 has port sorts but no LS t-stat. Returns are non monotonic, but lowest FR
  seems to have clearly worst performance, EW or VW. Table 3 has longer holding periods,
  and the pattern is more robust, but still no LS. OP focuses on negative FR for technical
  reasons that we don't worry about. VW is very weak, so we focus on EW.
- 'Original-paper replication evidence: 49 bps long-short; reported long-short return=0.4875,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Pension Funding Status
  authors:
  - Franzoni
  - Marin
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Pension Funding Status is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: FR = (FVPA - PBO), scaled by market value of equity. FVPA is pbnaa from 1980 to 1986, pplao + pplao from 1987 to 1997, and pplao after 1997. PBO is pbnvv from 1980 to 1986, pbpro + pbpru from 1987 to 1997, and pbpro after 1997. Exclude if price less than 5 or shrcd > 11. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute FR for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=FR; category=composite accounting; data=Accounting; evidence=49 bps long-short. Review the generated entry before using it as a final public corpus item.
