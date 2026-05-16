---
entry_type: strategy
id: openap_composite_debt_issuance
canonical_name: Composite debt issuance
aliases:
- Composite debt issuance
- CompositeDebtIssuance
- DebtFinC
one_line: Cross-sectional equity anomaly that uses Composite debt issuance to long
  low-signal stocks and short high-signal stocks.
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
- Table 5B. Uses decile sorts, but then longs top 3 and shorts bottom 3 portfolios.
  Table 3 offers hedge return alphas for IPOs and debt issue indicators too.
- 'Original-paper replication evidence: t=8.59 in port sort CAPM alpha; reported long-short
  return=0.523, t-stat=8.59.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Composite debt issuance
  authors:
  - Lyandres, Sun
  - Zhang
  year: 2008
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Composite debt issuance is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Log of long-term debt (dltt) plus debt in current liabilties (dlc) minus log of the same variable 5 years ago. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CompositeDebtIssuance for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CompositeDebtIssuance; category=external financing; data=Accounting; evidence=t=8.59 in port sort CAPM alpha. Review the generated entry before using it as a final public corpus item.
