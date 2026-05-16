---
entry_type: strategy
id: openap_bid_asktaq
canonical_name: Bid-ask spread (TAQ)
aliases:
- Bid-ask spread (TAQ)
- BidAskTAQ
one_line: Cross-sectional equity anomaly that uses Bid-ask spread (TAQ) to long high-signal
  stocks and short low-signal stocks.
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
- Insignificant with IVOL in mv reg. Could maybe be significant without IVOL, so it's
  a judgement call.
- 'Original-paper replication evidence: t=1.3 in mv reg; reported long-short return=n/a,
  t-stat=1.26.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Bid-ask spread (TAQ)
  authors:
  - Hou
  - Loh
  year: 2016
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Bid-ask spread (TAQ) is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: TAQ-based trading costs estimates (SAS code). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BidAskTAQ for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BidAskTAQ; category=liquidity; data=Trading; evidence=t=1.3 in mv reg. Review the generated entry before using it as a final public corpus item.
