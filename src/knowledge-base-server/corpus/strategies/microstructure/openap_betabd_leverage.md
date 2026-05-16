---
entry_type: strategy
id: openap_betabd_leverage
canonical_name: Broker-Dealer Leverage Beta
aliases:
- BetaBDLeverage
- Broker-Dealer Leverage Beta
one_line: Cross-sectional equity anomaly that uses Broker-Dealer Leverage Beta to
  long high-signal stocks and short low-signal stocks.
category: microstructure
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Trading data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Strangely, the vol of our LS port is about 30% smaller than OP. Also our returns
  are about 30% higher. As a result, our t-stat is almost twice as large.
- 'Original-paper replication evidence: t=1 in conservative port sort; reported long-short
  return=0.3225, t-stat=1.26.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Broker-Dealer Leverage Beta
  authors:
  - Adrian, Etula
  - Muir
  year: 2014
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Broker-Dealer Leverage Beta is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Regress quarterly stock return minus 3 month treasury bill rate (tbillrate3m) on broker dealer leverage. Use a rolling window of 40 quarters (require at least 20 non-missing observations). BetaBDLeverage is the coefficient on broker-dealer leverage. Broker-dealer leverage is the seasonally adjusted ratio of assets (FRED series BOGZ1FL664090005Q) and liabilities (FRED series BOGZ1FL664190005Q). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BetaBDLeverage for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BetaBDLeverage; category=liquidity; data=Trading; evidence=t=1 in conservative port sort. Review the generated entry before using it as a final public corpus item.
