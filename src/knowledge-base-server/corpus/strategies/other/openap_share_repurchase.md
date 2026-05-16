---
entry_type: strategy
id: openap_share_repurchase
canonical_name: Share repurchases
aliases:
- Share repurchases
- ShareRepurchase
one_line: Cross-sectional equity anomaly that uses Share repurchases to long high-signal
  stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=1.85 in long - benchmark port; reported
  long-short return=0.17, t-stat=1.852179859.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test payout indicator effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Share repurchases
  authors:
  - Ikenberry, Lakonishok, Vermaelen
  year: 1995
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Share repurchases is represented in the OpenAP signal catalog as a payout indicator predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if stock repurchase indicated in cash flow statement (prstkc > 0), and 0 if prstkc = 0. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ShareRepurchase for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ShareRepurchase; category=payout indicator; data=Accounting; evidence=t=1.85 in long - benchmark port. Review the generated entry before using it as a final public corpus item.
