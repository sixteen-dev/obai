---
entry_type: strategy
id: openap_rio_mb
canonical_name: Inst Own and Market to Book
aliases:
- Inst Own and Market to Book
- RIO_MB
one_line: Cross-sectional equity anomaly that uses Inst Own and Market to Book to
  long high-signal stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t = 4.91 in conditional sort; reported long-short
  return=1.07, t-stat=4.91.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test short sale constraints effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inst Own and Market to Book
  authors:
  - Nagel
  year: 2005
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inst Own and Market to Book is represented in the OpenAP signal catalog as a short sale constraints predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Residual institutional ownership (RIO) is defined as log(institutional ownership (instown\_perc)/(1-institutional ownership)) + 23.6 - 2.89*log(market value of equity) +.08*log(market value of equity)$^2$. Replace instown\_perc with 0 if it is missing, with .9999 if it's above .9999, and with .0001 if it's below .0001. Then lag RIO by 6 months. Keep only stocks with size > 20th percentile across exchcd == 1 or 2. Define MB as market cap / (ceq + txditc), where txditc = 0 if missing, and MB is replaced with missing if ceq < 0. Then sort into quintiles based on the lagged RIO, and sort into quintiles based on MB. Finally, let RIO\_MB = lagged RIO quintile if the MB quintile = 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RIO_MB for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RIO_MB; category=short sale constraints; data=13F; evidence=t = 4.91 in conditional sort. Review the generated entry before using it as a final public corpus item.
