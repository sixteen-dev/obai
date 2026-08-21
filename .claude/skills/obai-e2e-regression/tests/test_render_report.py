from __future__ import annotations

import json
import sys
from pathlib import Path

import render_report


def test_manifest_snapshot_is_report_source_of_truth(tmp_path: Path) -> None:
    manifest = {
        "created_at": "2026-07-15T12:00:00Z",
        "selected_tiers": ["core"],
        "estimated_api_calls": 2,
        "cases": [
            {
                "id": "T1",
                "snapshot": {
                    "id": "T1",
                    "feature": "immutable_query",
                    "query": "original materialized query",
                },
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    cases, timestamp, loaded = render_report.load_manifest_cases(tmp_path)

    assert loaded == manifest
    assert timestamp == "2026-07-15T12:00:00Z"
    assert cases["T1"]["query"] == "original materialized query"


def test_legacy_detail_parser_supports_hyphenated_case_ids() -> None:
    details = render_report.parse_detail_blocks(
        "**CORE-OPT-MATH — fail_product**\n- judgment: arithmetic mismatch\n"
    )

    assert details["CORE-OPT-MATH"]["judgment"] == "arithmetic mismatch"


def test_render_supports_v2_outcome_taxonomy(tmp_path: Path) -> None:
    results = {
        "status": "complete",
        "estimated_api_calls": 2,
        "results": [
            {
                "case_id": "T1",
                "verdict": "inconclusive_provider",
                "reason": "rate limit",
                "checks_failed": [],
                "missing_evidence": [],
            }
        ],
    }
    cases = {"T1": {"id": "T1", "feature": "provider", "query": "query at run time"}}

    output = render_report.render(
        results,
        cases,
        {},
        "2026-07-15T12:00:00Z",
        tmp_path,
    )

    assert "inconclusive provider" in output
    assert "query at run time" in output
    assert "rate limit" in output


def test_render_surfaces_semantic_checks_costs_and_abort_reason(tmp_path: Path) -> None:
    results = {
        "estimated_model_requests": 6,
        "observed_model_requests": 4,
        "between_case_model_request_limit": 8,
        "hard_model_request_cap_enforced": False,
        "abort_reason": "between-case limit would be exceeded",
        "results": [
            {
                "case_id": "T1",
                "verdict": "needs_semantic_review",
                "reason": "manual assertion pending",
                "unexecuted_assertions": ["verify payoff arithmetic"],
            }
        ],
    }
    cases = {"T1": {"id": "T1", "feature": "payoff", "query": "query"}}

    output = render_report.render(results, cases, {}, "2026-07-15T12:00:00Z", tmp_path)

    assert "minimum estimated model requests" in output
    assert "observed model requests" in output
    assert "between-case start limit" in output
    assert "not a hard cap" in output
    assert "verify payoff arithmetic" in output
    assert "between-case limit would be exceeded" in output


def test_reviewed_pass_keeps_semantic_review_summary_visible(tmp_path: Path) -> None:
    results = {
        "results": [
            {
                "case_id": "T1",
                "verdict": "pass",
                "semantic_review": {"summary": "Arithmetic reconciled to raw spans."},
            }
        ]
    }
    cases = {"T1": {"id": "T1", "feature": "math", "query": "query"}}

    output = render_report.render(results, cases, {}, "2026-07-15T12:00:00Z", tmp_path)

    assert "Arithmetic reconciled to raw spans." in output


def test_build_details_reconstructs_expected_contract_from_structured() -> None:
    results = {
        "results": [
            {
                "case_id": "T1",
                "verdict": "fail_product",
                "reason": "routing.specialist_missing:options_analysis",
                "checks_failed": ["routing.specialist_missing:options_analysis"],
            }
        ]
    }
    cases = {
        "T1": {
            "id": "T1",
            "feature": "opts",
            "query": "q",
            "expected_tools": ["options_analysis"],
            "expected_sequence": ["market_data_analysis", "options_analysis"],
            "expected_skills": ["obai-options"],
        }
    }

    details = render_report.build_details(results, cases)

    expected = details["T1"]["expected"]
    assert "options_analysis" in expected
    assert "market_data_analysis → options_analysis" in expected
    assert "obai-options" in expected


def test_render_markdown_emits_dashboard_table_and_nonpass_blocks() -> None:
    results = {
        "results": [
            {
                "case_id": "T1",
                "verdict": "pass",
                "feature": "q1",
                "latency_ms": 44000,
                "trace_id": "019f57ca1234",
            },
            {
                "case_id": "O2",
                "verdict": "fail_product",
                "reason": "routing.specialist_missing:options_analysis",
                "feature": "scenario_pnl",
                "latency_ms": 10000,
                "trace_id": "019f57ef5678",
                "checks_failed": ["routing.specialist_missing:options_analysis"],
            },
        ]
    }
    cases = {
        "T1": {"id": "T1", "feature": "q1", "query": "query one"},
        "O2": {
            "id": "O2",
            "feature": "scenario_pnl",
            "query": "scenario query",
            "expected_tools": ["options_analysis"],
        },
    }

    md = render_report.render_markdown(
        results, cases, render_report.build_details(results, cases), "2026-07-16T00:00:00Z"
    )

    assert "# OBaI E2E Regression — 2026-07-16T00:00:00Z" in md
    assert "| ID | Feature | Verdict | Reason | Trace | Latency |" in md
    assert "| T1 | q1 | pass | — | 019f57ca | 44.0s |" in md
    assert "| O2 | scenario_pnl | fail_product |" in md
    # Non-pass cases get an evidence block; clean passes do not.
    assert "**O2 — fail_product**" in md
    assert "**T1 — pass**" not in md
    assert "- expected: tools: options_analysis" in md
    assert "- checks failed: routing.specialist_missing:options_analysis" in md


def test_render_html_populates_expected_from_structured(tmp_path: Path) -> None:
    results = {
        "results": [
            {"case_id": "T1", "verdict": "fail_product", "reason": "boom", "checks_failed": ["c1"]}
        ]
    }
    cases = {
        "T1": {
            "id": "T1",
            "feature": "f",
            "query": "q",
            "expected_tools": ["market_data_analysis"],
            "expected_sequence": ["screener_lookup", "market_data_analysis"],
        }
    }

    out = render_report.render(
        results,
        cases,
        render_report.build_details(results, cases),
        "2026-07-16T00:00:00Z",
        tmp_path,
    )

    assert "screener_lookup → market_data_analysis" in out


def test_main_writes_both_report_md_and_html(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    results = {
        "results": [
            {
                "case_id": "T1",
                "verdict": "fail_product",
                "reason": "boom",
                "feature": "f",
                "checks_failed": ["c1"],
                "latency_ms": 1000,
                "trace_id": "abcd1234ef",
            }
        ]
    }
    (tmp_path / "results.json").write_text(json.dumps(results))
    manifest = {
        "created_at": "2026-07-16T00:00:00Z",
        "cases": [
            {
                "id": "T1",
                "snapshot": {
                    "id": "T1",
                    "feature": "f",
                    "query": "the materialized query",
                    "expected_tools": ["market_data_analysis"],
                },
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, "argv", ["render_report.py", "--run-dir", str(tmp_path)])

    rc = render_report.main()

    assert rc == 0
    report_md = (tmp_path / "report.md").read_text()
    assert "| ID | Feature | Verdict | Reason | Trace | Latency |" in report_md
    assert "**T1 — fail_product**" in report_md
    assert "the materialized query" in report_md
    assert (tmp_path / "report.html").exists()
    assert "market_data_analysis" in (tmp_path / "report.html").read_text()
