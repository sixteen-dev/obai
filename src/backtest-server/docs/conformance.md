# Backtest Server Conformance

This document defines what "industry standard" means for the OBaI backtest
server and how the deterministic conformance tests verify it. These tests live
in `tests/test_conformance_*.py` and use committed fixture data from
`tests/conformance_fixtures.py`.

The conformance suite is intentionally separate from the agent regression evals
under `src/obai/evaluation`. Agent evals check routing, tool use, and answer
quality. This suite checks the backtest engine's calculations and execution
behavior directly.

## Research Summary

The relevant public references split into calculation libraries, execution
engines, and financial reasoning benchmarks.

Calculation references worth borrowing:

- TA-Lib is the canonical open-source technical-analysis function library. Its
  docs and Python wrapper describe the indicator families, function API, and
  common OHLCV inputs. OBaI already uses `polars-talib`, a Polars expression
  wrapper for TA-Lib-style functions, so indicator parity tests compare OBaI's
  registry mapping and output naming against direct `polars_talib` expressions.
- empyrical provides Quantopian's common financial risk and performance
  metrics, including cumulative returns, max drawdown, annual volatility,
  Sharpe, Sortino, alpha, and beta. It is also the metric dependency used by
  pyfolio.
- pyfolio is Quantopian's portfolio and risk tear-sheet library and is useful as
  the higher-level convention around empyrical-style return-series analytics.
- QuantStats provides a maintained portfolio analytics package. Its docs are
  useful for the return-period versus trade-level distinction; OBaI explicitly
  asserts that closed-trade stats can differ from QuantStats-style period stats.

Execution references worth borrowing:

- Backtrader documents the standard next-bar market-order model for backtests,
  where a market order submitted by strategy logic fills at the next bar's open.
  It also documents percent slippage applied in the unfavorable direction for
  buy and sell orders.
- Zipline separates slippage and commission models and treats slippage models as
  the component responsible for simulated fill prices and fill volumes. Its
  fixed-spread and volume-share models are useful references for cost-model
  boundaries.
- QuantConnect LEAN documents fill, fee, slippage, and margin as customizable
  "reality models". Its equity fill model documents conservative stop behavior:
  a stop fills at the stop level in a continuous market, but at the opening
  price on unfavorable gaps.
- vectorbt is useful for validating the record-keeping shape of simulated
  orders, fees, prices, sizes, and sides.

Financial reasoning benchmarks checked but not used for this suite:

- FinanceBench, FinQA, TAT-QA, FinEval, and FinBen evaluate financial question
  answering, numerical reasoning, retrieval, or LLM/agent behavior. They are
  relevant to OBaI's agent-level evaluation work, not to deterministic
  backtest-engine conformance.

## Conformance Contract

For this repo, "industry standard" means:

1. Technical indicators match TA-Lib or `polars-talib` function behavior for the
   same OHLCV inputs and parameter mapping. Indicator parity covers the backtest
   engine registry; any narrower agent-facing or tool-facing indicator surface is
   a product exposure choice, not a calculation-conformance mismatch.
2. Return-series metrics use common empyrical/QuantStats-style simple returns,
   sample standard deviation for volatility and Sharpe, full-series downside
   semi-deviation for Sortino, cumulative-equity drawdown, and benchmark
   covariance/variance beta.
3. Trade statistics remain trade-based, not period-based. This is intentional:
   OBaI reports closed-trade win rate, profit factor, average trade return, and
   holding period separately from return-series risk metrics. Profit factor is
   the ratio of gross dollar profit to gross dollar loss whenever every closed
   trade reports a realized PnL, which both engines do; a ledger missing any
   of them falls back to summed percentage returns rather than mixing bases.
   Calmar keeps the sign of CAGR: only the drawdown is taken in magnitude, so
   a losing strategy reports a negative ratio.
4. Signal execution avoids lookahead: entry and signal-exit decisions from
   `close[t]` fill at `open[t+1]`.
5. Intrabar stop and take-profit checks use high/low ranges. If a long stop and
   target are both touched in one bar, the stop wins as the conservative
   assumption. The stop level is frozen at the fill — a percentage below it
   (`stop_loss_pct`), or `stop_atr_multiple` times the ATR printed on the bar
   whose close produced the signal (`atr_indicator`) — and is not recomputed as
   the price moves. Both engines check that same level. An entry whose ATR is
   undefined on the signal bar is skipped before any fill or commission, and
   counted under `atr_undefined`. A trailing stop (`trailing_stop_pct` or
   `trailing_stop_atr_multiple`) adds a second level that ratchets: it is
   recomputed at the end of every bar the position survives, from the highest
   high since entry and that bar's own ATR, and never falls. The effective
   stop is the higher of the frozen and trailed levels, so a high printed
   later in a bar cannot tighten the stop that same bar was checked against.
   The exit is labelled `trailing_stop` only when the trail sits strictly
   above the frozen level; a tie belongs to `stop_loss`.
6. Gap-through stop exits fill at the worse open, matching LEAN-style
   conservative stop behavior.
7. Slippage and spread move fills in the unfavorable direction on entry and
   signal-exit fills. Stop, trailing-stop and target exits fill at their level
   or the worse open, and the forced exits — `eod_close`, `time_stop` and
   `end_of_backtest` — fill at that bar's close, so none of them carries those
   costs — a stop-heavy result therefore reads better than it would. Percent
   commission is charged on both entry and exit. Results state both conventions
   in their `fill_timing` and `fill_model` fields.
8. Portfolio mode uses shared cash, discrete share counts, and runs each day in
   phases: a signal exit scheduled by the previous close fills at the open,
   then entries are sized against equity marked at that same open, then stops
   and targets pierced later inside the bar bind, and last the holding cap
   closes at that bar's close. Cash released intrabar is therefore not
   available to that day's opening fills. A held symbol with no bar on a
   portfolio date keeps its last observed mark instead of reverting to its
   entry price. Both engines close any position still open
   when the run ends, at the last available close, so a zero-trade result never
   hides an entry. In single-symbol mode a signal exit scheduled by the previous
   bar fills at the open ahead of that bar's stop or target. `close_eod` applies
   to the entry bar too, and on daily bars every bar ends a session, so a daily
   strategy with `close_eod` enters at the open and exits at that day's close.
   `max_holding_bars` closes at the close of the Nth bar the symbol held,
   counting the entry bar as the first and skipping portfolio dates the symbol
   never printed; `close_eod` outranks it on a bar that ends a session, since
   that exit would happen whatever the cap said and both fill at the close.
   `reentry_cooldown_bars` blocks a symbol from entering again until more than
   that many of its own bars have passed since its last exit, whatever closed
   it. In portfolio mode an exit and a re-entry at the same open are zero bars
   apart, so any cooldown blocks that same-bar flip; the blocked signals are
   counted under `cooldown` and never gain the earlier-signal priority that
   competes for capital.
9. Data-quality metadata is observability only. It must count coverage and
   zero/null prices without changing metrics or requiring the agent eval harness.

## Intentional Differences

OBaI intentionally differs from some public libraries in these areas:

- CAGR uses the actual calendar date range in the equity curve. empyrical's
  `annual_return` annualizes by `len(returns) / annualization_factor`. Calendar
  CAGR is easier to explain in API responses and handles irregular date ranges.
- Public metric values are rounded and expressed as percentages where the API
  schema says percentage. empyrical and QuantStats usually return decimals.
- Jensen alpha is arithmetic annualized from per-bar alpha. empyrical's alpha is
  geometrically annualized from mean alpha series. The OBaI value is stable for
  short deterministic fixtures and easier to reconcile from the code.
- Trade-level win rate and profit factor intentionally differ from QuantStats
  period-level win rate and profit factor.
- VWAP is in-house, not TA-Lib parity. OBaI implements session-resetting intraday
  VWAP as cumulative typical-price volume divided by cumulative volume per
  session. Daily VWAP requests are rejected.
- Single-symbol mode sizes a fixed notional at entry and marks it to market as
  a constant share count for the life of the trade, while portfolio mode tracks
  discrete shares and cash. Conformance tests cover both models.
- The volume-scaled slippage model is a simplified square-root participation
  model with clamps. It borrows the market-impact concept from Zipline/LEAN but
  is not an exact implementation of either engine's model.

## References

- TA-Lib core library: https://github.com/TA-Lib/ta-lib
- TA-Lib Python documentation: https://ta-lib.github.io/ta-lib-python/
- polars-talib package: https://pypi.org/project/polars-talib/
- empyrical: https://github.com/quantopian/empyrical
- pyfolio: https://quantopian.github.io/pyfolio/
- QuantStats: https://github.com/ranaroussi/quantstats
- Backtrader orders: https://www.backtrader.com/docu/order/
- Backtrader slippage: https://www.backtrader.com/docu/slippage/slippage/
- Zipline slippage source docs: https://zipline.ml4trading.io/_modules/zipline/finance/slippage.html
- QuantConnect LEAN reality modeling: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts
- QuantConnect LEAN equity fill model: https://www.lean.io/docs/v2/lean-engine/class-reference/classQuantConnect_1_1Orders_1_1Fills_1_1EquityFillModel.html
- vectorbt order records: https://vectorbt.dev/api/portfolio/orders/
- FinanceBench: https://github.com/patronus-ai/financebench
- FinQA: https://finqasite.github.io/
- TAT-QA: https://github.com/NExTplusplus/tat-qa
- FinEval: https://huggingface.co/papers/2308.09975
- FinBen: https://arxiv.org/abs/2402.12659
