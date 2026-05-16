---
entry_type: strategy
id: openap_bid_ask_spread
canonical_name: Bid-Ask Spread Signal
aliases:
- Bid-ask spread
- BidAskSpread
one_line: Cross-sectional equity anomaly that uses Bid-ask spread to long high-signal
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
- 'Table 4 looks to be the closest; TZ: Table 2 gives portfolio returns, need to take
  difference between extreme portfolios ((7) - (1)) but no t-stat. We use Corwin Schulz
  spread following MP, but OP uses Fitch''s Stock Quotations on the NYSE.'
- 'Original-paper replication evidence: strong port sorts but no LS special data;
  reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Bid-ask spread
  authors:
  - Amihud
  - Mendelson
  year: 1986
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Bid-ask spread is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Effective bid ask spread based on Corwin-Schulz scaled by stock price. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BidAskSpread for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BidAskSpread; category=liquidity; data=Trading; evidence=strong port sorts but no LS special data. Review the generated entry before using it as a final public corpus item.
