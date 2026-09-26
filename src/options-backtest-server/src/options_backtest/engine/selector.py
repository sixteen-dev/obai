"""Deterministic package selection and sizing at a decision (ADR 0002 §8, design §9.2).

Called at DEC for a due ENTRY or ROLL_OPEN (``campaign.decide_flat`` owns flatness, schedule,
final-session and campaign-cap gates; a replacement skips the schedule and keeps the
conditions). Everything is read through the decision's as-of view; the outcome records every
candidate considered, so the same inputs in any order give the same order and digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import TradingSession
from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.orders import Order, OrderPurpose
from options_backtest.models.artifacts import CandidateDecision, ConditionCheck
from options_backtest.models.ledger import LedgerState
from options_backtest.models.strategy_checks import ValidatedStrategy

MAX_PACKAGE_EVALUATIONS: Final = 10_000
"""Reaching this many package evaluations stops the search: SELECTION_BUDGET_EXCEEDED."""


@dataclass(frozen=True, slots=True)
class SelectionContext:
    """Decision inputs besides the strategy and the view.

    Attributes:
        decision_id: Event id of the DEC phase-5 event this decision produces.
        session: Decision session; the view is at its DEC.
        prior_session: Previous table session, whose features the conditions read; None on
            the table's first session.
        settles_on: Next table session (the preview's settlement date).
        purpose: ENTRY or ROLL_OPEN.
        campaign_id: Generation id the order opens (``campaign.opening_campaign_id``).
        state: Ledger state at DEC (flat: no option lots).
        schedule: Fee schedule.

    """

    decision_id: str
    session: TradingSession
    prior_session: date | None
    settles_on: date
    purpose: OrderPurpose
    campaign_id: str
    state: LedgerState
    schedule: AssumedFlatFeeSchedule


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    """An order, or none, with the full record of the decision.

    Attributes:
        order: The order to submit; None when no package was chosen.
        decision: Candidates, verdicts and the reason when there is no order.

    """

    order: Order | None
    decision: CandidateDecision


def evaluate_conditions(
    strategy: ValidatedStrategy, view: AsOfView, prior_session: date | None
) -> tuple[ConditionCheck, ...]:
    """Judge every entry condition on the prior session's feature (ADR 0002 §8 step 1).

    Each reads ``view.feature(feature_id(product.underlying_symbol, condition.feature),
    prior_session)``; ``gt``/``gte``/``lt``/``lte`` compare exact ``Decimal``s. No prior
    session, an invisible observation or a None value is unknown, and unknown is false.

    Args:
        strategy: The strategy.
        view: As-of view at DEC.
        prior_session: Previous table session, or None.

    Returns:
        One check per condition, in spec order; () when there are none (which is true).

    """
    raise NotImplementedError


def select(strategy: ValidatedStrategy, view: AsOfView, ctx: SelectionContext) -> SelectionOutcome:
    """Select, size and price one package, or explain why none (ADR 0002 §8).

    1. Conditions: ``evaluate_conditions``; any false gives reason CONDITIONS_FALSE, no search.
    2. Expiries: per allowed root with ``product_rules(root).family == product.family``, each
       expiry date of ``view.listed(root)`` whose contracts are EUROPEAN, CASH and of the rules'
       settlement series, with ``min_dte <= dte <= max_dte``, ``dte > exit_dte``, ``dte >
       trigger_dte`` when ``roll.mode`` is sequential, and ``last_tradable_at_ns >= f3``;
       sorted by ``(|dte - target_dte|, expires_at_ns, root)``.
    3. Inputs per expiry: spot ``view.index_value(underlying_id, max_age_ns=SPOT_MAX_AGE_NS)``;
       DF ``view.curve(UST_CMT).df((expires_at_ns - at_ns)/86_400e9)``; F ``parity_forward``
       over the strikes whose call and put quotes (``QUOTE_MAX_AGE_NS``) are both VALID. Any
       missing: skip PRICING_INPUT_UNAVAILABLE.
    4. Leg candidates, per leg in ``leg_order`` (every leg shares the ``target_dte`` leg's
       expiry): contracts of the leg's type, rejected, in order, for no quote within
       ``QUOTE_MAX_AGE_NS`` (QUOTE_UNAVAILABLE), a status the side cannot use
       (``usable_quote``; QUOTE_STATUS), NO_BID under ``require_positive_bid_for_entry``
       (NO_BID_FOR_ENTRY), ``ask - bid`` above both ``max_absolute_spread_price_units`` and
       ``max_relative_spread · mid`` (SPREAD, exact), cumulative volume absent or below a
       positive ``min_cumulative_volume`` (VOLUME), no IV for a delta leg (PRICING_INPUT) and
       an error above ``tolerance`` (OUT_OF_TOLERANCE). Errors, exact ``Decimal``: delta
       ``|Decimal(spot_delta(F, S, K, t, DF, iv, right)) - target_delta|`` with ``iv`` from the
       mid and ``t`` ACT/365F from DEC; moneyness ``|Decimal(float(K)/float(S)) -
       target_strike_to_spot|``. A ``strike_offset`` leg's one candidate is the contract at the
       anchor's strike plus the offset, error 0 (NO_OFFSET_STRIKE if absent), under the same
       quote filters. Sorted by ``(error, spread_usd, strike, contract_id)``.
    5. Search: depth-first in ``leg_order``; each complete package is one evaluation, judged
       in order DUPLICATE_CONTRACT, STRIKE_ORDER (condor lp < sp < sc < lc, strangle put <
       call, straddle equal), PREMIUM_DIRECTION (``Q·D(1) > 0`` at decision naturals), NO_SIZE
       (step 6), else ELIGIBLE with score ``(expiry_error, Σerror, Σspread_usd, ids)``; the
       minimum is CHOSEN. An expiry whose ``expiry_error`` exceeds the best score's, and every
       later one, is PRUNED before its inputs are read (spot, DF, F None; no leg candidates).
       Reaching ``MAX_PACKAGE_EVALUATIONS`` stops with no order, reason
       SELECTION_BUDGET_EXCEEDED. No eligible package: NO_PACKAGE.
    6. Sizing at decision naturals: ``n`` fits when ``-expiry_bounds(((terms_i, ratio_i·n)),
       0, -D(n) - fees(n)).min_value + Σ|ratio_i|·n·max(trade, cash_settlement rate) <=
       max_campaign_risk_fraction · mid NLV`` (exact products; mid NLV of the flat state is
       CASH + ΣRECEIVABLE + ΣPAYABLE), the ``book_option_trade`` preview at DEC keeps
       ``funding_headroom >= 0``, and ``n <= floor(min_i(side size_i/|ratio_i|) ·
       participation_fraction)`` at the DEC quotes. ``fixed_contracts``: ``contracts`` or
       nothing; ``risk_budget``: the largest fitting ``n`` from ``min(max_contracts,
       max_contracts_per_order)`` down to 1. The order's limit is ``D(n) +
       price_allowance_usd``.

    Args:
        strategy: The strategy.
        view: As-of view at the session's DEC.
        ctx: Decision inputs.

    Returns:
        The order (legs in ``leg_order``, ratio +1 buy / -1 sell, trigger None, submitted at
        DEC) or None, and the decision record.

    """
    raise NotImplementedError
