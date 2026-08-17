"""Tests for the offline benchmark report.

``conftest.py`` puts both skills' ``scripts/`` dirs on ``sys.path``, which is
what makes the flat ``import benchmark_report`` below resolve.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import benchmark_report as br
import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

HUB_MODEL = "gpt-5.6-sol"
ALT_MODEL = "gpt-5.6-terra"
SPECIALIST_MODEL = "gpt-5.6-luna"

PRICES = {
    "prices": {
        "gpt-5.6-sol": {"input": 2.0, "cached_input": 0.5, "output": 8.0},
        "gpt-5.6-terra": {"input": 4.0, "cached_input": 1.0, "output": 16.0},
        "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.25, "output": 4.0},
    }
}

# (input - cached) * input_rate + cached * cached_rate + output * output_rate, /1e6.
HUB_SOL_COST = ((1000 - 400) * 2.0 + 400 * 0.5 + 200 * 8.0) / 1e6
HUB_TERRA_COST = ((1000 - 400) * 4.0 + 400 * 1.0 + 200 * 16.0) / 1e6
GUARD_COST = (100 * 1.0 + 10 * 4.0) / 1e6
SPECIALIST_COST = ((2000 - 1000) * 1.0 + 1000 * 0.25 + 300 * 4.0) / 1e6
SOL_PACKET_COST = HUB_SOL_COST + GUARD_COST + SPECIALIST_COST
TERRA_PACKET_COST = HUB_TERRA_COST + GUARD_COST + SPECIALIST_COST


def _usage(input_tokens: int, cached_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "original_usage.input_tokens": input_tokens,
        "original_usage.input_tokens_details.cached_tokens": cached_tokens,
        "original_usage.input_tokens_details.cache_write_tokens": 0,
        "original_usage.output_tokens": output_tokens,
        "original_usage.output_tokens_details.reasoning_tokens": 0,
        "original_usage.total_tokens": input_tokens + output_tokens,
    }


HUB_USAGE = _usage(1000, 400, 200)
GUARD_USAGE = _usage(100, 0, 10)
SPECIALIST_USAGE = _usage(2000, 1000, 300)


def _node(span_id: str, parent: str | None, name: str, trace_id: str, kind: str) -> dict[str, Any]:
    return {
        "id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent,
        "name": name,
        "type": kind,
        "start_time": "2026-08-15T04:08:07.100000Z",
        "end_time": "2026-08-15T04:08:43.700000Z",
    }


def _llm(
    span_id: str,
    parent: str,
    trace_id: str,
    model: str,
    effort: str | None,
    usage: dict[str, Any],
) -> dict[str, Any]:
    span = _node(span_id, parent, "Response", trace_id, "llm")
    span["model"] = model
    span["provider"] = "openai"
    span["metadata"] = {"provider": "openai", "id": f"resp-{span_id}"}
    if effort is not None:
        span["metadata"]["reasoning"] = {
            "context": "all_turns",
            "effort": effort,
            "mode": "standard",
        }
    span["usage"] = dict(usage)
    return span


def make_trace(
    trace_id: str,
    hub_model: str,
    hub_effort: str | None,
    *,
    specialist_model: str = SPECIALIST_MODEL,
    specialist_tool: str = "market_data_analysis",
    with_guardrail: bool = True,
    with_specialist: bool = True,
) -> dict[str, Any]:
    prefix = trace_id
    spans = [
        _node(f"{prefix}-root", None, "Task", trace_id, "general"),
        _node(f"{prefix}-hub", f"{prefix}-root", "central_hub", trace_id, "general"),
        _node(f"{prefix}-turn", f"{prefix}-hub", "Turn", trace_id, "general"),
        _llm(f"{prefix}-hub-llm", f"{prefix}-turn", trace_id, hub_model, hub_effort, HUB_USAGE),
    ]
    if with_guardrail:
        spans += [
            _node(
                f"{prefix}-gt", f"{prefix}-root", "financial_query_guardrail", trace_id, "general"
            ),
            _node(
                f"{prefix}-ga",
                f"{prefix}-gt",
                "obai_financial_query_guardrail",
                trace_id,
                "general",
            ),
            _llm(
                f"{prefix}-g-llm", f"{prefix}-ga", trace_id, SPECIALIST_MODEL, "none", GUARD_USAGE
            ),
        ]
    if with_specialist:
        spans += [
            _node(f"{prefix}-st", f"{prefix}-turn", specialist_tool, trace_id, "tool"),
            _node(f"{prefix}-sa", f"{prefix}-st", "obai_market_data_agent", trace_id, "general"),
            _llm(
                f"{prefix}-s-llm",
                f"{prefix}-sa",
                trace_id,
                specialist_model,
                "medium",
                SPECIALIST_USAGE,
            ),
        ]
    return {"id": trace_id, "spans": spans}


def make_packet(case_id: str, trace: dict[str, Any], **extra: Any) -> dict[str, Any]:
    packet = {
        "id": case_id,
        "feature": "fixture_shape",
        "final_response": "Fixture response.",
        "cli": {"exit_code": 0, "stdout_json": {"guardrail_rejected": False}},
        "trace": trace,
    }
    packet.update(extra)
    return packet


def _combo_key(combo: dict[str, Any]) -> str:
    return f"{combo['model']}:{combo['effort']}"


def _default_manifest(combo: dict[str, Any]) -> dict[str, Any]:
    manifest = {
        "run_id": f"run-{combo['model']}-{combo['effort']}",
        "schema_version": 1,
        "cases_snapshot_sha256": "a" * 64,
        "suite_fingerprint": "b" * 64,
        "git": {"sha": "c" * 40, "dirty": False},
        "calendar_anchor": "2026-08-15T04:08:07.695386Z",
        "selected_tiers": ["core"],
    }
    manifest.update(combo.get("manifest", {}))
    return manifest


def _write_combo(session_dir: Path, combo: dict[str, Any]) -> dict[str, Any]:
    model, effort = combo["model"], combo["effort"]
    run_dir = session_dir / f"{model}@{effort}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case_id, spec in combo["cases"].items():
        packet_path = _write_packet(run_dir, case_id, spec, combo)
        results.append(
            {
                "case_id": case_id,
                "verdict": spec["verdict"],
                "deterministic_verdict": spec.get("deterministic_verdict", spec["verdict"]),
                "feature": spec.get("feature", "market_data"),
                "latency_ms": spec.get("latency_ms"),
                "packet_path": str(packet_path) if packet_path else None,
            }
        )
    _write_run_artifacts(run_dir, combo, results)
    return {
        "model": model,
        "effort": effort,
        "run_dir": run_dir.name,
        "status": "complete",
        "source_digest": combo.get("source_digest", "digest-A"),
    }


def _write_packet(
    run_dir: Path, case_id: str, spec: dict[str, Any], combo: dict[str, Any]
) -> Path | None:
    if spec.get("verdict") == "skipped_dependency" or spec.get("no_packet"):
        return None
    packet = spec.get("packet")
    if packet is None:
        trace = make_trace(
            f"tr-{run_dir.name}-{case_id}",
            spec.get("hub_model", combo["model"]),
            spec.get("hub_effort", combo["effort"]),
        )
        packet = make_packet(case_id, trace)
    path = run_dir / f"{case_id}.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def _write_run_artifacts(
    run_dir: Path, combo: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    run_id = f"run-{combo['model']}-{combo['effort']}"
    preliminary = {
        "schema_version": 1,
        "run_id": run_id,
        "complete": True,
        "results": [dict(row, verdict=row["deterministic_verdict"]) for row in results],
    }
    (run_dir / "results.json").write_text(json.dumps(preliminary), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(_default_manifest(combo)), encoding="utf-8")
    if combo.get("reviews") is not None:
        (run_dir / "semantic_reviews.json").write_text(
            json.dumps({"schema_version": 2, "run_id": run_id, "reviews": combo["reviews"]}),
            encoding="utf-8",
        )
    if combo.get("preliminary_only"):
        return
    reviewed = {
        "schema_version": 2,
        "run_id": run_id,
        "semantic_review_complete": True,
        "reviewed_at": "2026-08-15T05:00:00Z",
        "reviewed_case_count": len(results),
        "results": results,
    }
    (run_dir / "reviewed-results.json").write_text(json.dumps(reviewed), encoding="utf-8")


def build_session(
    tmp_path: Path,
    combos: list[dict[str, Any]],
    *,
    tier: str = "core",
    incumbent: tuple[str, str] = (HUB_MODEL, "medium"),
) -> Path:
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    entries = [_write_combo(session_dir, combo) for combo in combos]
    manifest = {
        "schema_version": 1,
        "session_id": "sess-1",
        "created_at": "2026-08-15T04:00:00Z",
        "tier": tier,
        "max_api_calls_per_combo": 20,
        "incumbent": {"model": incumbent[0], "effort": incumbent[1]},
        "incumbent_included": any(
            entry["model"] == incumbent[0] and entry["effort"] == incumbent[1] for entry in entries
        ),
        "combos": entries,
    }
    (session_dir / "benchmark_session.json").write_text(json.dumps(manifest), encoding="utf-8")
    return session_dir


def _cases(**overrides: Any) -> dict[str, dict[str, Any]]:
    base = {
        "CORE-FX": {"verdict": "pass", "feature": "market_data", "latency_ms": 1000},
        "CORE-GUARD": {"verdict": "pass", "feature": "guardrail_refusal", "latency_ms": 2000},
        "CORE-OPT": {"verdict": "pass_degraded", "feature": "options", "latency_ms": 3000},
    }
    for case_id, spec in overrides.items():
        key = case_id.replace("_", "-")
        base[key] = {**base.get(key, {}), **spec}
    return base


@pytest.fixture
def prices_path(tmp_path: Path) -> Path:
    path = tmp_path / "model_prices.yaml"
    path.write_text(yaml.safe_dump(PRICES), encoding="utf-8")
    return path


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger-root" / "benchmarks" / "ledger.jsonl"


def _two_combos(
    *, cases_a: dict[str, Any] | None = None, cases_b: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [
        {"model": HUB_MODEL, "effort": "medium", "cases": cases_a or _cases()},
        {"model": ALT_MODEL, "effort": "max", "cases": cases_b or _cases()},
    ]


def _final(session_dir: Path, prices: Path, ledger: Path) -> int:
    return br.run_final(session_dir, prices_path=prices, ledger_path=ledger)


def _read_report(session_dir: Path) -> dict[str, Any]:
    return json.loads((session_dir / "benchmark.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# session manifest / CLI guards
# --------------------------------------------------------------------------


def test_missing_session_dir_exits_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    code = br.main(["--session-dir", str(tmp_path / "nope")])
    assert code == br.EXIT_CONFIGURATION
    assert "session" in capsys.readouterr().err.lower()


def test_invalid_session_manifest_exits_configuration(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "benchmark_session.json").write_text("{}", encoding="utf-8")
    assert br.main(["--session-dir", str(session_dir)]) == br.EXIT_CONFIGURATION


# --------------------------------------------------------------------------
# audit mode
# --------------------------------------------------------------------------


def test_audit_flags_disagreeing_cases_with_packet_paths(tmp_path: Path):
    cases_b = _cases(CORE_OPT={"verdict": "fail_product"})
    session_dir = build_session(tmp_path, _two_combos(cases_b=cases_b))
    assert br.run_audit(session_dir) == br.EXIT_SUCCESS

    audit = json.loads((session_dir / "audit.json").read_text(encoding="utf-8"))
    worklist = {entry["case_id"] for entry in audit["worklist"]}
    assert worklist == {"CORE-OPT"}
    entry = audit["worklist"][0]
    assert set(entry["packet_paths"]) == {f"{HUB_MODEL}:medium", f"{ALT_MODEL}:max"}
    assert entry["packet_paths"][f"{ALT_MODEL}:max"].endswith("CORE-OPT.json")
    assert audit["matrix"]["CORE-FX"][f"{HUB_MODEL}:medium"]["verdict"] == "pass"
    text = (session_dir / "audit.md").read_text(encoding="utf-8")
    assert "CORE-OPT" in text


def test_audit_agreement_yields_empty_worklist(tmp_path: Path):
    session_dir = build_session(tmp_path, _two_combos())
    assert br.run_audit(session_dir) == br.EXIT_SUCCESS
    audit = json.loads((session_dir / "audit.json").read_text(encoding="utf-8"))
    assert audit["worklist"] == []
    assert "No disagreements" in (session_dir / "audit.md").read_text(encoding="utf-8")


def test_audit_includes_draft_semantic_statuses(tmp_path: Path):
    combos = _two_combos()
    combos[0]["reviews"] = [
        {"case_id": "CORE-FX", "assertions": [{"assertion": "cites a price", "status": "pass"}]}
    ]
    combos[1]["reviews"] = [
        {"case_id": "CORE-FX", "assertions": [{"assertion": "cites a price", "status": "fail"}]}
    ]
    session_dir = build_session(tmp_path, combos)
    assert br.run_audit(session_dir) == br.EXIT_SUCCESS
    audit = json.loads((session_dir / "audit.json").read_text(encoding="utf-8"))
    cell = audit["matrix"]["CORE-FX"][f"{HUB_MODEL}:medium"]
    assert cell["semantic_statuses"] == {"cites a price": "pass"}
    assert [entry["case_id"] for entry in audit["worklist"]] == ["CORE-FX"]


def test_audit_rejects_a_malformed_draft_instead_of_reporting_agreement(tmp_path: Path):
    combos = _two_combos()
    combos[0]["reviews"] = [
        {"case_id": "CORE-FX", "assertions": [{"text": "cites a price", "result": "pass"}]}
    ]
    session_dir = build_session(tmp_path, combos)
    with pytest.raises(br.ArtifactError, match="assertion"):
        br.run_audit(session_dir)


def test_audit_rejects_a_draft_whose_reviews_are_not_a_list(tmp_path: Path):
    session_dir = build_session(tmp_path, _two_combos())
    (session_dir / f"{HUB_MODEL}@medium" / "semantic_reviews.json").write_text(
        json.dumps({"reviews": {"CORE-FX": {}}}), encoding="utf-8"
    )
    assert br.main(["--session-dir", str(session_dir), "--audit"]) == br.EXIT_CONFIGURATION


def test_audit_missing_results_exits_configuration(tmp_path: Path):
    session_dir = build_session(tmp_path, _two_combos())
    (session_dir / f"{ALT_MODEL}@max" / "results.json").unlink()
    assert br.main(["--session-dir", str(session_dir), "--audit"]) == br.EXIT_CONFIGURATION


# --------------------------------------------------------------------------
# final mode: artifact guards
# --------------------------------------------------------------------------


def test_final_refuses_preliminary_only_run(tmp_path: Path, prices_path: Path, ledger_path: Path):
    combos = _two_combos()
    combos[1]["preliminary_only"] = True
    session_dir = build_session(tmp_path, combos)
    with pytest.raises(br.ArtifactError, match=re.escape("reviewed-results.json")):
        _final(session_dir, prices_path, ledger_path)


def test_final_happy_path_writes_outputs_and_ledger(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    session_dir = build_session(tmp_path, _two_combos())
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS

    report = _read_report(session_dir)
    assert report["intersection_size"] == 3
    assert report["ranking"] == [f"{HUB_MODEL}:medium", f"{ALT_MODEL}:max"]
    assert (session_dir / "benchmark.md").exists()
    lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["session_id"] == "sess-1"
    assert entry["tier"] == "core"
    assert entry["ranking"] == report["ranking"]
    assert entry["session_dir"] == str(session_dir)


def test_ledger_appends_without_truncating(tmp_path: Path, prices_path: Path, ledger_path: Path):
    session_dir = build_session(tmp_path, _two_combos())
    _final(session_dir, prices_path, ledger_path)
    _final(session_dir, prices_path, ledger_path)
    assert len(ledger_path.read_text(encoding="utf-8").strip().splitlines()) == 2


# --------------------------------------------------------------------------
# final mode: fairness gates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"cases_snapshot_sha256": "d" * 64}, "cases_snapshot_sha256"),
        ({"suite_fingerprint": "e" * 64}, "suite_fingerprint"),
        ({"git": {"sha": "c" * 40, "dirty": True}}, "git.dirty"),
    ],
)
def test_manifest_fairness_gates_fire(
    tmp_path: Path, prices_path: Path, ledger_path: Path, override: dict[str, Any], needle: str
):
    combos = _two_combos()
    combos[1]["manifest"] = override
    session_dir = build_session(tmp_path, combos)
    with pytest.raises(br.FairnessError, match=needle):
        _final(session_dir, prices_path, ledger_path)


def test_source_digest_mismatch_fires(tmp_path: Path, prices_path: Path, ledger_path: Path):
    combos = _two_combos()
    combos[1]["source_digest"] = "digest-B"
    session_dir = build_session(tmp_path, combos)
    with pytest.raises(br.FairnessError, match="source_digest"):
        _final(session_dir, prices_path, ledger_path)


def test_calendar_anchor_span_is_a_warning_not_a_failure(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    combos = _two_combos()
    combos[1]["manifest"] = {"calendar_anchor": "2026-08-16T01:02:03.000000Z"}
    session_dir = build_session(tmp_path, combos)
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    warnings = " ".join(_read_report(session_dir)["warnings"])
    assert "calendar" in warnings.lower()


def test_dirty_tree_on_every_combo_is_a_warning(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    combos = _two_combos()
    for combo in combos:
        combo["manifest"] = {"git": {"sha": "c" * 40, "dirty": True}}
    session_dir = build_session(tmp_path, combos)
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    assert any("dirty" in warning for warning in _read_report(session_dir)["warnings"])


def test_sha_only_mismatch_with_equal_digest_is_a_warning(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    """A mid-session commit of already-present bytes must not void the session.

    The byte-level source_digest and the dirty flag are the load-bearing
    equality proof; when both match, a differing git.sha is bookkeeping and
    demotes to a warning instead of exit 3.
    """
    combos = _two_combos()
    combos[1]["manifest"] = {"git": {"sha": "f" * 40, "dirty": False}}
    session_dir = build_session(tmp_path, combos)
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    warnings = " ".join(_read_report(session_dir)["warnings"])
    assert "git.sha" in warnings
    assert "source_digest" in warnings


def test_incumbent_absent_from_combos_is_a_warning(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    session_dir = build_session(tmp_path, _two_combos(), incumbent=(ALT_MODEL, "high"))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    warnings = " ".join(_read_report(session_dir)["warnings"])
    assert "incumbent" in warnings.lower()


# --------------------------------------------------------------------------
# final mode: combo-took-effect gate
# --------------------------------------------------------------------------


def test_took_effect_passes_when_only_hub_spans_match(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    session_dir = build_session(tmp_path, _two_combos())
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    by_key = {combo["key"]: combo for combo in report["combos"]}
    assert by_key[f"{HUB_MODEL}:medium"]["llm_span_count"] == 9


def test_took_effect_rejects_wrong_hub_model(tmp_path: Path, prices_path: Path, ledger_path: Path):
    cases = _cases(CORE_FX={"hub_model": ALT_MODEL})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    with pytest.raises(br.FairnessError, match="CORE-FX"):
        _final(session_dir, prices_path, ledger_path)


def test_took_effect_rejects_wrong_hub_effort(tmp_path: Path, prices_path: Path, ledger_path: Path):
    cases = _cases(CORE_FX={"hub_effort": "high"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    with pytest.raises(br.FairnessError, match="effort"):
        _final(session_dir, prices_path, ledger_path)


def test_took_effect_rejects_model_outside_allowlist(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    trace = make_trace("tr-alien", HUB_MODEL, "medium", specialist_model="gpt-4o-mini")
    cases = _cases(CORE_FX={"packet": make_packet("CORE-FX", trace)})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    with pytest.raises(br.FairnessError, match=re.escape("gpt-4o-mini")):
        _final(session_dir, prices_path, ledger_path)


def test_took_effect_ignores_specialist_and_guardrail_effort(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # A terra specialist at 'medium' while the combo is terra:max must pass.
    trace = make_trace(
        "tr-mixed",
        ALT_MODEL,
        "max",
        specialist_model=ALT_MODEL,
        specialist_tool="strategy_analysis",
    )
    cases_b = _cases(CORE_FX={"packet": make_packet("CORE-FX", trace)})
    session_dir = build_session(tmp_path, _two_combos(cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS


def test_a_case_whose_trace_evidence_is_missing_is_a_warning_not_a_fatal_error(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # The shape run_one writes when the Opik lookup or evidence fetch failed.
    packet = make_packet(
        "CORE-OPT",
        {"id": "tr-lost", "lookup_error": "timeout", "evidence_error": None, "spans": None},
    )
    cases_b = _cases(CORE_OPT={"verdict": "inconclusive_harness", "packet": packet})
    session_dir = build_session(tmp_path, _two_combos(cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert "CORE-OPT" not in report["intersection"]
    assert any("tr-lost" in note for note in report["diagnostics"])


def test_a_combo_with_no_captured_hub_span_is_a_took_effect_violation(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # Packets pruned or never recorded: zero spans, $0.00, and a vacuous pass.
    cases_b = {case_id: dict(spec, no_packet=True) for case_id, spec in _cases().items()}
    session_dir = build_session(tmp_path, _two_combos(cases_b=cases_b))
    with pytest.raises(br.FairnessError, match="no hub llm span"):
        _final(session_dir, prices_path, ledger_path)


def test_main_maps_a_took_effect_violation_to_exit_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    cases = _cases(CORE_FX={"hub_model": ALT_MODEL})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    assert br.main(["--session-dir", str(session_dir)]) == br.EXIT_FAIRNESS
    assert "CORE-FX" in capsys.readouterr().err
    assert not (session_dir / "benchmark.json").exists()


def test_classify_span_kind_orders_guardrail_before_specialist():
    assert br.classify_span_kind(["Turn", "central_hub", "Task"]) == "hub"
    assert br.classify_span_kind(["Turn", "market_data_analysis", "Task"]) == "specialist"
    assert br.classify_span_kind(["Task", "obai_financial_query_guardrail"]) == "guardrail"


def test_unresolvable_parent_is_reported_as_a_diagnostic(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    trace = make_trace("tr-orphan", HUB_MODEL, "medium", with_specialist=False)
    for span in trace["spans"]:
        if span["id"] == "tr-orphan-turn":
            span["parent_span_id"] = "tr-orphan-missing"
    cases = _cases(CORE_FX={"packet": make_packet("CORE-FX", trace)})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    diagnostics = " ".join(_read_report(session_dir)["diagnostics"])
    assert "tr-orphan-missing" in diagnostics


# --------------------------------------------------------------------------
# final mode: dollars
# --------------------------------------------------------------------------


def test_cost_applies_the_cached_input_split(tmp_path: Path, prices_path: Path, ledger_path: Path):
    session_dir = build_session(tmp_path, _two_combos())
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    by_key = {combo["key"]: combo for combo in _read_report(session_dir)["combos"]}
    assert by_key[f"{HUB_MODEL}:medium"]["cost_usd"] == pytest.approx(3 * SOL_PACKET_COST)
    assert by_key[f"{ALT_MODEL}:max"]["cost_usd"] == pytest.approx(3 * TERRA_PACKET_COST)


def test_cost_dedupes_the_followup_and_last_poll_trace(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    async_trace = make_trace(
        "tr-async", HUB_MODEL, "medium", with_guardrail=False, with_specialist=False
    )
    packet = make_packet(
        "CORE-FX",
        make_trace("tr-initial", HUB_MODEL, "medium"),
        followup={"job_id": "job-1", "trace": async_trace, "polls": [{"trace": async_trace}]},
    )
    cases = _cases(CORE_FX={"packet": packet})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    by_key = {combo["key"]: combo for combo in _read_report(session_dir)["combos"]}
    expected = 3 * SOL_PACKET_COST + HUB_SOL_COST
    assert by_key[f"{HUB_MODEL}:medium"]["cost_usd"] == pytest.approx(expected)
    assert by_key[f"{HUB_MODEL}:medium"]["llm_span_count"] == 10


def test_null_rate_for_an_observed_model_is_a_hard_error(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    prices_path.write_text(
        yaml.safe_dump(
            {"prices": {"gpt-5.6-sol": {"input": None, "cached_input": 1, "output": 1}}}
        ),
        encoding="utf-8",
    )
    session_dir = build_session(tmp_path, _two_combos())
    with pytest.raises(br.ArtifactError, match=re.escape("gpt-5.6-sol")):
        _final(session_dir, prices_path, ledger_path)


def test_missing_model_row_names_the_yaml_path(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    prices_path.write_text(yaml.safe_dump({"prices": {}}), encoding="utf-8")
    session_dir = build_session(tmp_path, _two_combos())
    with pytest.raises(br.ArtifactError, match=re.escape(str(prices_path))):
        _final(session_dir, prices_path, ledger_path)


# --------------------------------------------------------------------------
# final mode: scoring, ranking, podium, disqualifier
# --------------------------------------------------------------------------


def test_undecided_cases_are_excluded_from_the_intersection(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases_b = _cases(CORE_OPT={"verdict": "inconclusive_provider"})
    session_dir = build_session(tmp_path, _two_combos(cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["intersection"] == ["CORE-FX", "CORE-GUARD"]
    by_key = {combo["key"]: combo for combo in report["combos"]}
    excluded = by_key[f"{ALT_MODEL}:max"]["excluded"]
    assert excluded == [{"case_id": "CORE-OPT", "verdict": "inconclusive_provider"}]


def test_skipped_dependency_cases_do_not_crash_the_walk(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases = _cases(
        CORE_SKIP={"verdict": "skipped_dependency", "feature": "crypto", "latency_ms": None}
    )
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases, cases_b=cases))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    assert "CORE-SKIP" not in _read_report(session_dir)["intersection"]


def test_ranking_prefers_strict_then_total_then_cost(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # Both combos decide every case; sol takes 3 strict passes, terra 2.
    cases_b = _cases(CORE_OPT={"verdict": "fail_product"})
    cases_a = _cases(CORE_OPT={"verdict": "pass"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases_a, cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["ranking"][0] == f"{HUB_MODEL}:medium"
    top = next(c for c in report["combos"] if c["key"] == f"{HUB_MODEL}:medium")
    assert (top["strict"], top["total"]) == (3, 3)


def test_equal_quality_breaks_on_cost_then_reports_no_tie(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    session_dir = build_session(tmp_path, _two_combos())
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["ranking"] == [f"{HUB_MODEL}:medium", f"{ALT_MODEL}:max"]
    assert report["ties"] == []


def test_fully_equal_combos_are_reported_as_a_tie(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    combos = [
        {"model": HUB_MODEL, "effort": "medium", "cases": _cases()},
        {"model": HUB_MODEL, "effort": "high", "cases": _cases()},
    ]
    for combo in combos:
        for spec in combo["cases"].values():
            spec["hub_effort"] = combo["effort"]
    session_dir = build_session(tmp_path, combos)
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert [sorted(pair) for pair in report["ties"]] == [
        sorted([f"{HUB_MODEL}:medium", f"{HUB_MODEL}:high"])
    ]
    assert "tie" in (session_dir / "benchmark.md").read_text(encoding="utf-8").lower()


def test_podium_rule_suppresses_a_recommendation_it_cannot_support(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases_a = _cases(CORE_OPT={"verdict": "pass"})
    cases_b = _cases(CORE_OPT={"verdict": "inconclusive_harness"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases_a, cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["decision_grade"] is False
    assert report["recommended"] is None
    podium = report["podium"]
    assert podium["cases_outside_intersection"] == 1
    assert podium["strict_gap"] == 0
    text = (session_dir / "benchmark.md").read_text(encoding="utf-8")
    assert "not decision-grade" in text


def test_a_fully_measured_cost_tiebreak_still_recommends(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # Equal quality, nothing outside the intersection: the gap is 0, but there
    # is no unmeasured case that could reorder anything, so cost decides.
    session_dir = build_session(tmp_path, _two_combos())
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["podium"]["cases_outside_intersection"] == 0
    assert report["podium"]["strict_gap"] == 0
    assert report["decision_grade"] is True
    assert report["recommended"] == f"{HUB_MODEL}:medium"


def test_podium_gap_ignores_a_disqualified_leader(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    # sol:medium outscores both others but fails a guardrail case, so the only
    # meaningful gap is between the two recommendable combos, which is 0.
    wide = _cases(
        CORE_A1={"verdict": "pass", "feature": "options"},
        CORE_A2={"verdict": "pass", "feature": "options"},
        CORE_EXTRA={"verdict": "pass", "feature": "events"},
    )
    disqualified = {**wide, "CORE-GUARD": {**wide["CORE-GUARD"], "verdict": "fail_product"}}
    weaker = {
        **wide,
        "CORE-OPT": {**wide["CORE-OPT"], "verdict": "pass_degraded"},
        "CORE-A1": {**wide["CORE-A1"], "verdict": "pass_degraded"},
        "CORE-A2": {**wide["CORE-A2"], "verdict": "pass_degraded"},
    }
    undecided = {
        **weaker,
        "CORE-EXTRA": {**weaker["CORE-EXTRA"], "verdict": "inconclusive_harness"},
    }
    combos = [
        {"model": HUB_MODEL, "effort": "medium", "cases": disqualified},
        {"model": ALT_MODEL, "effort": "max", "cases": undecided},
        {"model": HUB_MODEL, "effort": "high", "cases": weaker},
    ]
    for combo in combos:
        combo["cases"] = {
            case_id: {**spec, "hub_effort": combo["effort"]}
            for case_id, spec in combo["cases"].items()
        }
    session_dir = build_session(tmp_path, combos)
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["ranking"][0] == f"{HUB_MODEL}:medium"
    assert report["podium"]["cases_outside_intersection"] == 1
    assert report["podium"]["strict_gap"] == 0
    assert f"{HUB_MODEL}:medium" not in report["podium"]["explanation"]
    assert report["decision_grade"] is False
    assert report["recommended"] is None


def test_decision_grade_ranking_recommends_the_top_combo(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases_a = _cases()
    cases_b = _cases(CORE_OPT={"verdict": "fail_product"}, CORE_FX={"verdict": "fail_product"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases_a, cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["decision_grade"] is True
    assert report["recommended"] == f"{HUB_MODEL}:medium"


def test_guardrail_failure_disqualifies_the_top_combo(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases_a = _cases(CORE_GUARD={"verdict": "fail_product"}, CORE_OPT={"verdict": "pass"})
    cases_b = _cases(CORE_FX={"verdict": "pass_degraded"}, CORE_OPT={"verdict": "fail_product"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases_a, cases_b=cases_b))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    by_key = {combo["key"]: combo for combo in report["combos"]}
    assert report["ranking"][0] == f"{HUB_MODEL}:medium"
    assert report["decision_grade"] is True
    assert by_key[f"{HUB_MODEL}:medium"]["disqualified"] is True
    assert report["recommended"] == f"{ALT_MODEL}:max"
    assert "DISQUALIFIED" in (session_dir / "benchmark.md").read_text(encoding="utf-8")


def test_recommendation_flags_a_change_from_the_incumbent(
    tmp_path: Path, prices_path: Path, ledger_path: Path
):
    cases_a = _cases(CORE_OPT={"verdict": "fail_product"}, CORE_FX={"verdict": "fail_product"})
    session_dir = build_session(tmp_path, _two_combos(cases_a=cases_a))
    assert _final(session_dir, prices_path, ledger_path) == br.EXIT_SUCCESS
    report = _read_report(session_dir)
    assert report["recommended"] == f"{ALT_MODEL}:max"
    text = (session_dir / "benchmark.md").read_text(encoding="utf-8")
    assert "confirmation repeats" in text.lower()
