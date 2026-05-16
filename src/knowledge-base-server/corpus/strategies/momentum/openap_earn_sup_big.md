---
entry_type: strategy
id: openap_earn_sup_big
canonical_name: Earnings surprise of big firms
aliases:
- EarnSupBig
- Earnings surprise of big firms
one_line: Cross-sectional equity anomaly that uses Earnings surprise of big firms
  to long high-signal stocks and short low-signal stocks.
category: momentum
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
- Only shows up in Table 6. t-stat is from firm-week regressions.
- 'Original-paper replication evidence: t=9 in mv reg weekly; reported long-short
  return=n/a, t-stat=8.91.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings surprise of big firms
  authors:
  - Hou
  year: 2007
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings surprise of big firms is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average monthly value of EarningsSurprise (defined above) of the 30% largest companies by market value of equity in the same Fama-French 48 industry. Exclude the largest 30% of companies for EarnSupBig (not to compute the anomaly) The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EarnSupBig for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarnSupBig; category=lead lag; data=Accounting; evidence=t=9 in mv reg weekly. Review the generated entry before using it as a final public corpus item.
