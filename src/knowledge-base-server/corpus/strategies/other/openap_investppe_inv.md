---
entry_type: strategy
id: openap_investppe_inv
canonical_name: change in ppe and inv/assets
aliases:
- InvestPPEInv
- change in ppe and inv/assets
one_line: Cross-sectional equity anomaly that uses change in ppe and inv/assets to
  long low-signal stocks and short high-signal stocks.
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
- Not in a table. Page 2837 has untabulated results. OP does a complicated 3x3 sort
  with size two stage portfolio construction, but we just equal-weight.
- 'Original-paper replication evidence: t=7 in long-short port; reported long-short
  return=0.57, t-stat=7.13.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: change in ppe and inv/assets
  authors:
  - Lyandres, Sun
  - Zhang
  year: 2008
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
change in ppe and inv/assets is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: One-year change in property, plants and equipment (ppegt) plus one year change in inventory (invt), scaled by one-year lagged assets (at). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute InvestPPEInv for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=InvestPPEInv; category=investment; data=Accounting; evidence=t=7 in long-short port. Review the generated entry before using it as a final public corpus item.
