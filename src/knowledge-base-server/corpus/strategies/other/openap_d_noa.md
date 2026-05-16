---
entry_type: strategy
id: openap_d_noa
canonical_name: change in net operating assets
aliases:
- change in net operating assets
- dNoa
one_line: Cross-sectional equity anomaly that uses change in net operating assets
  to long low-signal stocks and short high-signal stocks.
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
- Seems to be monthly. Table 4 footnote says decile portfolios formed monthly for
  NOA. Interestingly they have a minimum 4 month lag.
- 'Original-paper replication evidence: t=8.9 in mv reg; reported long-short return=n/a,
  t-stat=8.85.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: change in net operating assets
  authors:
  - Hirshleifer, Hou, Teoh, Zhang
  year: 2004
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
change in net operating assets is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 12-month growth in Net Operating Assets scaled by lagged total assets (at). Net Operating assets are operating assets minus operating liabilities. Operating assets are total assets (at) minus cash- and short-term investments (che), operating liabilities are total assets minus long-term debt (dltt), minority interest (mib), deferred charges (dlc), book equity (ceq) and preferred stock (pstk), all items (except at and ceq) replaced with 0 if missing. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute dNoa for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=dNoa; category=investment; data=Accounting; evidence=t=8.9 in mv reg. Review the generated entry before using it as a final public corpus item.
