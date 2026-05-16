---
entry_type: strategy
id: openap_net_debt_price
canonical_name: Net Debt to Price
aliases:
- NDebtPrice
- Net debt to price
- NetDebtPrice
one_line: Cross-sectional equity anomaly that uses Net debt to price to long low-signal
  stocks and short high-signal stocks.
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
- 'need to adjust sample dates: even though paper says it begins in 1962, there is
  only 1 stock for the first 5 months of 1962. ND/P in paper. Table 4a has size adjusted
  returns for double sorts, on NDP and NOA/POA. I chose 5th NOA/POA Portfolio, but
  they''re all similar. Table 1 Panel C has decile sort returns, but no hedge returns.
  Performance here is not great, as noted in the text. Best performance is Table 4
  which reports conditional sorts. OP drops extreme obs but we don''t.'
- 'Original-paper replication evidence: t=2.3 in double sort; reported long-short
  return=0.658333333, t-stat=2.34.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test leverage effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Net debt to price
  authors:
  - Penman, Richardson
  - Tuna
  year: 2007
  venue: JAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Net debt to price is represented in the OpenAP signal catalog as a leverage predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Long-term debt (dltt) plus debt in current liabilities (dlc) plus preferred stock (pstk) plus preferred dividends in arrears (dvpa) minus treasury stock (tstkp) minus cash and short-term investments (che), scaled by market value of equity. Exclude if SIC between 6000 and 6999, or if missing value for total assets (at), net income (ib), common shares outstanding (csho), book value of equity (ceq) or price close fiscal year (prcc\_f). Keep only 3rd B/M Quintile, following Table 4 (and in contrast to Table 1). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute NetDebtPrice for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=NetDebtPrice; category=leverage; data=Accounting; evidence=t=2.3 in double sort. Review the generated entry before using it as a final public corpus item.
