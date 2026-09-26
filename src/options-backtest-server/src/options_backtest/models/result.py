"""The simulation result: only what WP3 can defend (ADR 0002 §6, design §14.5, §15.2).

WP4/WP5 fields (metrics, research status, evidence verdict, run digest) are absent, not zero.
The constructor refuses ``headline_eligible`` unless the run is valid and ends flat, a non-null
``final_equity_usd`` unless it is valid, and a SYNTHETIC_FIXTURE result without a
SYNTHETIC_FIXTURE_NOT_HISTORICAL warning (T2-D2 adds these checks).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from options_backtest.data.records import FidelityClass
from options_backtest.errors import Issue
from options_backtest.money import Usd


class CalculationStatus(StrEnum):
    """Run validity (design §14.5); it only moves away from VALID, once."""

    VALID = "valid"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


class WarningCode(StrEnum):
    """Disclosures of a run; emission rules are ADR 0002 §17's.

    SYNTHETIC_FIXTURE_NOT_HISTORICAL: once, first, dated ``start_date``, for synthetic data.
    DATA_COVERAGE_GAP: per
    session whose due entry or replacement met a GAP chain partition. PRICING_INPUT_UNAVAILABLE:
    per decision that skipped an expiry for spot, curve or forward. FEATURE_UNAVAILABLE: per
    decision with a condition whose feature was unknown. SELECTION_BUDGET_EXCEEDED: per decision
    that hit the evaluation cap. EXIT_DEFERRED: per session a due exit or roll close had no valid
    decision quote. EXIT_UNFILLED: per closing order cancelled after F3. INSUFFICIENT_CAPITAL:
    per order, at its first funding refusal.
    """

    SYNTHETIC_FIXTURE_NOT_HISTORICAL = "SYNTHETIC_FIXTURE_NOT_HISTORICAL"
    DATA_COVERAGE_GAP = "DATA_COVERAGE_GAP"
    PRICING_INPUT_UNAVAILABLE = "PRICING_INPUT_UNAVAILABLE"
    FEATURE_UNAVAILABLE = "FEATURE_UNAVAILABLE"
    SELECTION_BUDGET_EXCEEDED = "SELECTION_BUDGET_EXCEEDED"
    EXIT_DEFERRED = "EXIT_DEFERRED"
    EXIT_UNFILLED = "EXIT_UNFILLED"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"


@dataclass(frozen=True, slots=True)
class RunWarning:
    """One disclosed warning (ADR 0002 §6's ``Warning``, renamed off the builtin).

    Attributes:
        code: Warning code.
        session_date: Session it arose in.
        message: Human-readable explanation; never empty.
        refs: Event, order, observation or contract ids it concerns.

    """

    code: WarningCode
    session_date: date
    message: str
    refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResultProvenance:
    """Inputs and versions that reproduce the result (design §15.2 subset).

    Attributes:
        manifest_id: Dataset manifest id.
        engine_version: Engine version.
        policy_versions: (policy kind, policy id) pairs of the resolved run.
        calendar_version: Dataset calendar version.
        product_rules_version: Dataset product-rules version.
        feature_versions: Dataset (feature name, version) pairs.
        license_policy_id: Dataset license policy.

    """

    manifest_id: str
    engine_version: str
    policy_versions: tuple[tuple[str, str], ...]
    calendar_version: str
    product_rules_version: str
    feature_versions: tuple[tuple[str, str], ...]
    license_policy_id: str


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """The WP3 result envelope.

    Attributes:
        calculation_status: Valid, invalid or incomplete.
        data_fidelity: The dataset's fidelity (the minimum over segments).
        limitations: The dataset's limitations.
        execution_basis: Always ``synthetic_natural_package``.
        calibration_status: Always ``uncalibrated``.
        cost_basis: Always ``assumed_schedule``.
        assignment_basis: Always ``not_applicable`` (European cash settlement).
        window_requested: (start_date, end_date) of the resolved run.
        window_simulated: (start_date, last window session processed); a run stopped by
            invalidity ends at that session.
        valuation_clock: The clock profile, ``scheduled_daily_v1`` (marks at the common close).
        initial_equity_usd: ``account.initial_cash_usd``.
        final_equity_usd: Mid NLV of the final window session's account point; None unless
            valid.
        open_positions: (contract_id, signed quantity) held after the final window session,
            sorted by contract id.
        unsettled_cash: (ISO settle date, signed balance) of every RECEIVABLE (+) and PAYABLE
            (-) still outstanding when the run stopped, sorted by date then sign.
        end_policy: The strategy's end policy.
        warnings: Warnings in emission order.
        invalid_reasons: Why the run is invalid or incomplete; () when valid.
        headline_eligible: Valid and flat at the end.
        provenance: Reproduction inputs.

    """

    calculation_status: CalculationStatus
    data_fidelity: FidelityClass
    limitations: tuple[str, ...]
    execution_basis: Literal["synthetic_natural_package"]
    calibration_status: Literal["uncalibrated"]
    cost_basis: Literal["assumed_schedule"]
    assignment_basis: Literal["not_applicable"]
    window_requested: tuple[date, date]
    window_simulated: tuple[date, date]
    valuation_clock: str
    initial_equity_usd: Usd
    final_equity_usd: Usd | None
    open_positions: tuple[tuple[str, int], ...]
    unsettled_cash: tuple[tuple[str, Usd], ...]
    end_policy: Literal["liquidate_at_final_session", "mark_open_positions"]
    warnings: tuple[RunWarning, ...]
    invalid_reasons: tuple[Issue, ...]
    headline_eligible: bool
    provenance: ResultProvenance
