#!/usr/bin/env python3
"""Audit and rank benchmarked hub model/effort combos from captured artifacts.

This script is offline only: it reads the benchmark session manifest, each
combo's E2E run artifacts and the price table, then writes reports. It spawns
nothing, imports no paid code path, and makes zero provider calls.
"""

# Operator script: stdout/stderr is the interface, exactly like the e2e
# skill's run_suite.py. The repo-root ruff config excludes this tree; this
# file-level exemption keeps an explicit `ruff check <file>` clean too.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parents[2]
E2E_SCRIPT_DIR = SKILL_DIR.parent / "obai-e2e-regression" / "scripts"
if str(E2E_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_SCRIPT_DIR))

from judge_packet import FINANCIAL_SPECIALIST_TOOLS  # noqa: E402

DEFAULT_PRICES_PATH = SKILL_DIR / "config" / "model_prices.yaml"
DEFAULT_LEDGER_PATH = REPO_ROOT / ".e2e-runs" / "benchmarks" / "ledger.jsonl"
SESSION_MANIFEST_NAME = "benchmark_session.json"
# The guardrail agent/tool span pair RECON pinned in every captured packet.
GUARDRAIL_SPAN_NAMES = frozenset({"obai_financial_query_guardrail", "financial_query_guardrail"})
# Shipped specialist/guardrail models; they are legitimate in any combo's run.
SHIPPED_NON_HUB_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-terra"})
# Real ancestor chains are <= 5 deep; anything past this is a cycle or corruption.
MAX_ANCESTOR_DEPTH = 50
STRICT_VERDICT = "pass"
DEGRADED_VERDICT = "pass_degraded"
FAIL_VERDICT = "fail_product"
DECIDED_VERDICTS = frozenset({STRICT_VERDICT, DEGRADED_VERDICT, FAIL_VERDICT})
GUARDRAIL_FEATURE_MARKER = "guardrail"
SCHEMA_VERSION = 1
EXIT_SUCCESS = 0
EXIT_CONFIGURATION = 2
EXIT_FAIRNESS = 3


class ArtifactError(RuntimeError):
    """A required benchmark artifact is missing, malformed, or incomplete."""


class FairnessError(RuntimeError):
    """Two combos were not produced under comparable, comparable-enough conditions."""


@dataclass(frozen=True)
class CaseResult:
    """One case row taken from a run's results file."""

    case_id: str
    verdict: str
    deterministic_verdict: str
    feature: str
    latency_ms: float | None
    packet_path: str | None


@dataclass(frozen=True)
class ComboArtifacts:
    """Everything final mode needs about one benchmarked combo."""

    key: str
    model: str
    effort: str
    run_dir: Path
    source_digest: str
    manifest: dict[str, Any]
    results: list[CaseResult]


@dataclass(frozen=True)
class ClassifiedSpan:
    """One llm span with its orchestration role resolved from its ancestors."""

    case_id: str
    span_id: str
    kind: str
    model: str
    effort: str | None
    usage: dict[str, Any]


@dataclass(frozen=True)
class ComboScan:
    """The classified llm spans of every packet captured for one combo."""

    spans: list[ClassifiedSpan] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComboScore:
    """One combo's scoreboard row, computed over the decided intersection."""

    key: str
    strict: int
    total: int
    cost_usd: float
    median_latency_ms: float | None
    disqualified: bool
    excluded: list[dict[str, str]]


# ---------------------------------------------------------------------------
# artifact loading
# ---------------------------------------------------------------------------


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    """Read a JSON object from disk.

    Args:
        path: File to read.
        label: Human-readable name used in error messages.

    Returns:
        The decoded mapping.

    Raises:
        ArtifactError: The file is missing, unreadable, or not a JSON object.
    """
    if not path.is_file():
        msg = f"missing {label}: {path}"
        raise ArtifactError(msg)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"cannot read {label} at {path}: {exc}"
        raise ArtifactError(msg) from exc
    if not isinstance(value, dict):
        msg = f"{label} at {path} must contain a JSON object"
        raise ArtifactError(msg)
    return value


def load_session(session_dir: Path) -> dict[str, Any]:
    """Load and validate the benchmark session manifest.

    Args:
        session_dir: Directory holding ``benchmark_session.json``.

    Returns:
        The validated session manifest.

    Raises:
        ArtifactError: The directory or manifest is missing or malformed.
    """
    if not session_dir.is_dir():
        msg = f"session directory does not exist: {session_dir}"
        raise ArtifactError(msg)
    manifest = load_json_object(session_dir / SESSION_MANIFEST_NAME, "session manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        msg = f"session manifest schema_version must be {SCHEMA_VERSION}"
        raise ArtifactError(msg)
    combos = manifest.get("combos")
    if not isinstance(combos, list) or not combos:
        msg = "session manifest needs a non-empty combos list"
        raise ArtifactError(msg)
    for index, entry in enumerate(combos):
        _validate_combo_entry(index, entry)
    return manifest


def _validate_combo_entry(index: int, entry: object) -> None:
    if not isinstance(entry, dict):
        msg = f"session manifest combos[{index}] must be an object"
        raise ArtifactError(msg)
    for name in ("model", "effort", "run_dir"):
        value = entry.get(name)
        if not isinstance(value, str) or not value:
            msg = f"session manifest combos[{index}].{name} must be a non-empty string"
            raise ArtifactError(msg)


def combo_key(entry: dict[str, Any]) -> str:
    """Return the ``model:effort`` display key for a session manifest entry.

    Args:
        entry: One validated session manifest combo entry.

    Returns:
        The combo spec string.
    """
    return f"{entry['model']}:{entry['effort']}"


def parse_case_results(payload: dict[str, Any], label: str) -> list[CaseResult]:
    """Parse the ``results`` list of a preliminary or reviewed results file.

    Args:
        payload: Decoded results file.
        label: Human-readable name used in error messages.

    Returns:
        One :class:`CaseResult` per case, in file order.

    Raises:
        ArtifactError: A row is malformed or a case id repeats.
    """
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        msg = f"{label} needs a non-empty results list"
        raise ArtifactError(msg)
    seen: set[str] = set()
    results: list[CaseResult] = []
    for index, row in enumerate(rows):
        result = _parse_case_row(index, row, label)
        if result.case_id in seen:
            msg = f"{label} has duplicate case {result.case_id}"
            raise ArtifactError(msg)
        seen.add(result.case_id)
        results.append(result)
    return results


def _parse_case_row(index: int, row: object, label: str) -> CaseResult:
    if not isinstance(row, dict):
        msg = f"{label} results[{index}] must be an object"
        raise ArtifactError(msg)
    case_id = row.get("case_id")
    verdict = row.get("verdict")
    if not isinstance(case_id, str) or not case_id:
        msg = f"{label} results[{index}] has no case_id"
        raise ArtifactError(msg)
    if not isinstance(verdict, str) or not verdict:
        msg = f"{label} results[{index}] ({case_id}) has no verdict"
        raise ArtifactError(msg)
    latency = row.get("latency_ms")
    packet_path = row.get("packet_path")
    deterministic = row.get("deterministic_verdict")
    feature = row.get("feature")
    return CaseResult(
        case_id=case_id,
        verdict=verdict,
        deterministic_verdict=deterministic if isinstance(deterministic, str) else verdict,
        feature=feature if isinstance(feature, str) else "",
        latency_ms=float(latency) if isinstance(latency, int | float) else None,
        packet_path=packet_path if isinstance(packet_path, str) and packet_path else None,
    )


def load_combo(session_dir: Path, entry: dict[str, Any]) -> ComboArtifacts:
    """Load one combo's reviewed results and run manifest for final mode.

    Args:
        session_dir: Benchmark session directory.
        entry: The session manifest entry for this combo.

    Returns:
        The combo's loaded artifacts.

    Raises:
        ArtifactError: An artifact is missing, preliminary-only, or malformed.
    """
    key = combo_key(entry)
    run_dir = session_dir / str(entry["run_dir"])
    reviewed = load_json_object(run_dir / "reviewed-results.json", f"{key} reviewed-results.json")
    if reviewed.get("semantic_review_complete") is not True:
        msg = (
            f"{key} reviewed-results.json is preliminary-only; run finalize_review.py "
            f"for {run_dir} before the final report"
        )
        raise ArtifactError(msg)
    manifest = load_json_object(run_dir / "manifest.json", f"{key} run manifest.json")
    digest = entry.get("source_digest")
    if not isinstance(digest, str) or not digest:
        msg = f"session manifest entry for {key} has no source_digest"
        raise ArtifactError(msg)
    _validate_run_manifest(key, manifest)
    return ComboArtifacts(
        key=key,
        model=str(entry["model"]),
        effort=str(entry["effort"]),
        run_dir=run_dir,
        source_digest=digest,
        manifest=manifest,
        results=parse_case_results(reviewed, f"{key} reviewed-results.json"),
    )


def _validate_run_manifest(key: str, manifest: dict[str, Any]) -> None:
    for name in ("cases_snapshot_sha256", "suite_fingerprint", "calendar_anchor"):
        value = manifest.get(name)
        if not isinstance(value, str) or not value:
            msg = f"{key} run manifest.json has no {name}"
            raise ArtifactError(msg)
    git = manifest.get("git")
    if not isinstance(git, dict) or not isinstance(git.get("sha"), str):
        msg = f"{key} run manifest.json has no git.sha"
        raise ArtifactError(msg)
    if not isinstance(git.get("dirty"), bool):
        msg = f"{key} run manifest.json has no boolean git.dirty"
        raise ArtifactError(msg)


# ---------------------------------------------------------------------------
# fairness gates
# ---------------------------------------------------------------------------


def _mismatch(name: str, values: dict[str, object]) -> list[str]:
    distinct = {json.dumps(value, sort_keys=True, default=str) for value in values.values()}
    if len(distinct) <= 1:
        return []
    detail = ", ".join(f"{key}={value!r}" for key, value in sorted(values.items()))
    return [f"{name} differs across combos: {detail}"]


def check_fairness(combos: Sequence[ComboArtifacts]) -> None:
    """Refuse to rank combos that were not run against the same suite and tree.

    ``git.sha`` is deliberately absent from the hard list: the byte-level
    ``source_digest`` and the ``git.dirty`` flag are the load-bearing equality
    proof, so a sha that differs while both of those match is a mid-session
    commit of already-present bytes — bookkeeping, surfaced as a warning by
    :func:`fairness_warnings` instead of voiding N paid runs.

    Args:
        combos: Every loaded combo of the session.

    Raises:
        ArtifactError: There is no combo to compare.
        FairnessError: Any hard comparability field differs across combos.
    """
    if not combos:
        msg = "no combos to compare"
        raise ArtifactError(msg)
    mismatches: list[str] = []
    mismatches += _mismatch(
        "cases_snapshot_sha256", {c.key: c.manifest["cases_snapshot_sha256"] for c in combos}
    )
    mismatches += _mismatch(
        "suite_fingerprint", {c.key: c.manifest["suite_fingerprint"] for c in combos}
    )
    mismatches += _mismatch("git.dirty", {c.key: c.manifest["git"]["dirty"] for c in combos})
    mismatches += _mismatch("source_digest", {c.key: c.source_digest for c in combos})
    if mismatches:
        msg = "combos are not comparable:\n  " + "\n  ".join(mismatches)
        raise FairnessError(msg)


def fairness_warnings(combos: Sequence[ComboArtifacts], session: dict[str, Any]) -> list[str]:
    """Collect the soft comparability warnings that do not block a report.

    Args:
        combos: Every loaded combo of the session.
        session: The session manifest.

    Returns:
        Human-readable warning lines, possibly empty.
    """
    warnings: list[str] = []
    warnings += [
        f"{mismatch}; the fingerprinted source tree is byte-identical (source_digest equal), "
        "so a mid-session commit of already-present bytes is the only explanation and the "
        "runs remain comparable"
        for mismatch in _mismatch("git.sha", {c.key: c.manifest["git"]["sha"] for c in combos})
    ]
    days = sorted({str(combo.manifest["calendar_anchor"])[:10] for combo in combos})
    if len(days) > 1:
        warnings.append(
            f"combo runs span more than one UTC calendar day ({', '.join(days)}); "
            "market data moved between runs"
        )
    dirty = sorted(combo.key for combo in combos if combo.manifest["git"]["dirty"])
    if dirty:
        warnings.append(f"git tree was dirty for: {', '.join(dirty)}; the runs are not a commit")
    incumbent = session.get("incumbent")
    incumbent_key = ""
    if isinstance(incumbent, dict):
        incumbent_key = f"{incumbent.get('model')}:{incumbent.get('effort')}"
    if incumbent_key not in {combo.key for combo in combos}:
        warnings.append(
            f"incumbent {incumbent_key} is absent from the combo list; "
            "the ranking has no shipped baseline to compare against"
        )
    return warnings


# ---------------------------------------------------------------------------
# span classification and the combo-took-effect gate
# ---------------------------------------------------------------------------


def resolve_packet_path(run_dir: Path, case_id: str, raw: str | None, label: str) -> Path | None:
    """Resolve a results row's packet path, tolerating a moved session dir.

    The packet living beside the results file being scored wins over the
    absolute path recorded at run time: that recorded path is where a later
    run or ``--resume-session`` writes, so preferring it would make a copied
    or archived session report the *original's* spans, costs, and hub models.

    Args:
        run_dir: The combo's run directory.
        case_id: Case whose packet is wanted.
        raw: The recorded absolute path, or None for skipped cases.
        label: Human-readable name used in error messages.

    Returns:
        The packet path, or None when the case captured no packet.

    Raises:
        ArtifactError: A path was recorded but no packet exists.
    """
    fallback = run_dir / f"{case_id}.json"
    if fallback.is_file():
        return fallback
    if raw is None:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    msg = f"{label}: packet for {case_id} is at neither {candidate} nor {fallback}"
    raise ArtifactError(msg)


def packet_traces(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect the initial, follow-up, and poll traces of a packet, deduped.

    The async follow-up trace is the same object as the final poll's trace,
    so summing both would bill that turn twice.

    This traversal mirrors ``finalize_review._span_ids`` in the e2e gate skill
    (private there, and it returns span ids rather than traces, so it cannot
    be imported). If the gate ever captures another trace slot, that helper
    and this one have to change together or the cost sum and the took-effect
    gate quietly skip the new spans.

    Args:
        packet: One captured evidence packet.

    Returns:
        Distinct trace objects, keyed by trace id.
    """
    candidates: list[object] = [packet.get("trace")]
    followup = packet.get("followup")
    if isinstance(followup, dict):
        candidates.append(followup.get("trace"))
        polls = followup.get("polls")
        if isinstance(polls, list):
            candidates.extend(poll.get("trace") for poll in polls if isinstance(poll, dict))
    traces: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trace in candidates:
        identity = _trace_identity(trace)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        traces.append(trace)  # type: ignore[arg-type]
    return traces


def _trace_identity(trace: object) -> str | None:
    if not isinstance(trace, dict):
        return None
    identity = trace.get("id")
    return str(identity) if isinstance(identity, str) and identity else f"anon-{id(trace)}"


def ancestor_names(
    span: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> tuple[list[str], str | None]:
    """Walk a span's parents, collecting their names.

    Args:
        span: The span whose ancestry is wanted.
        by_id: Every span of the trace, keyed by ``id``.

    Returns:
        The ancestor names nearest-first, plus a diagnostic when the chain
        could not be walked to the root.
    """
    names: list[str] = []
    current = span
    for _ in range(MAX_ANCESTOR_DEPTH):
        parent_id = current.get("parent_span_id")
        if not isinstance(parent_id, str) or not parent_id:
            return names, None
        parent = by_id.get(parent_id)
        if parent is None:
            return names, f"span {span.get('id')} has unresolvable parent {parent_id}"
        names.append(str(parent.get("name") or ""))
        current = parent
    return names, f"span {span.get('id')} exceeded ancestor depth {MAX_ANCESTOR_DEPTH}"


def classify_span_kind(names: Sequence[str]) -> str:
    """Classify an llm span as guardrail, specialist, or hub work.

    Guardrail is checked first: its span names are not specialist tools, so
    without the earlier check its spans would be scored as hub work.

    Args:
        names: The span's ancestor names.

    Returns:
        One of ``guardrail``, ``specialist``, ``hub``.
    """
    unique = set(names)
    if unique & GUARDRAIL_SPAN_NAMES:
        return "guardrail"
    if unique & FINANCIAL_SPECIALIST_TOOLS:
        return "specialist"
    return "hub"


def classify_trace_spans(
    case_id: str, trace: dict[str, Any]
) -> tuple[list[ClassifiedSpan], list[str]]:
    """Classify every llm span of one trace.

    A trace whose ``spans`` is null is the shape the gate stores when the Opik
    lookup or evidence fetch failed (``run_one`` writes ``"spans": None``
    beside ``lookup_error``/``evidence_error``). The gate itself degrades to an
    empty span list there and judges the case inconclusive, so the case is
    undecided and outside the scored intersection anyway; a missing trace must
    not make the whole report unrunnable.

    Args:
        case_id: Case the trace belongs to.
        trace: One captured trace with a ``spans`` list.

    Returns:
        The classified llm spans and any ancestry or missing-evidence
        diagnostics.

    Raises:
        ArtifactError: An llm span lacks a model or usage counters.
    """
    raw = trace.get("spans")
    if not isinstance(raw, list):
        note = (
            f"{case_id}: trace {trace.get('id')!r} captured no spans list "
            "(trace lookup or evidence fetch failed); it contributes no spans or cost"
        )
        return [], [note]
    spans = [span for span in raw if isinstance(span, dict)]
    by_id = {str(span["id"]): span for span in spans if isinstance(span.get("id"), str)}
    classified: list[ClassifiedSpan] = []
    diagnostics: list[str] = []
    for span in [span for span in spans if span.get("type") == "llm"]:
        names, note = ancestor_names(span, by_id)
        if note is not None:
            diagnostics.append(f"{case_id}: {note}")
        classified.append(_classify_llm_span(case_id, span, names))
    return classified, diagnostics


def _classify_llm_span(case_id: str, span: dict[str, Any], names: Sequence[str]) -> ClassifiedSpan:
    model = span.get("model")
    if not isinstance(model, str) or not model:
        msg = f"{case_id}: llm span {span.get('id')!r} has no model"
        raise ArtifactError(msg)
    usage = span.get("usage")
    if not isinstance(usage, dict):
        msg = f"{case_id}: llm span {span.get('id')!r} has no usage dict"
        raise ArtifactError(msg)
    metadata = span.get("metadata")
    reasoning = metadata.get("reasoning") if isinstance(metadata, dict) else None
    effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    return ClassifiedSpan(
        case_id=case_id,
        span_id=str(span.get("id")),
        kind=classify_span_kind(names),
        model=model,
        effort=effort if isinstance(effort, str) else None,
        usage=usage,
    )


def scan_combo_spans(combo: ComboArtifacts) -> ComboScan:
    """Classify every llm span in every packet captured for one combo.

    Args:
        combo: The loaded combo.

    Returns:
        The classified spans and any ancestry diagnostics.

    Raises:
        ArtifactError: A referenced packet is missing or malformed.
    """
    spans: list[ClassifiedSpan] = []
    diagnostics: list[str] = []
    for result in combo.results:
        path = resolve_packet_path(combo.run_dir, result.case_id, result.packet_path, combo.key)
        if path is None:
            continue
        packet = load_json_object(path, f"{combo.key} packet {result.case_id}")
        case_spans, case_notes = _scan_packet(result.case_id, packet)
        spans.extend(case_spans)
        diagnostics.extend(case_notes)
    return ComboScan(spans=spans, diagnostics=diagnostics)


def _scan_packet(case_id: str, packet: dict[str, Any]) -> tuple[list[ClassifiedSpan], list[str]]:
    spans: list[ClassifiedSpan] = []
    diagnostics: list[str] = []
    for trace in packet_traces(packet):
        trace_spans, trace_notes = classify_trace_spans(case_id, trace)
        spans.extend(trace_spans)
        diagnostics.extend(trace_notes)
    return spans, diagnostics


def took_effect_violations(combo: ComboArtifacts, spans: Sequence[ClassifiedSpan]) -> list[str]:
    """Check that the injected hub combo is what actually ran.

    Non-hub spans get no effort assertion: per-agent overrides are shipped
    product configuration, not a benchmark leak.

    A combo with no hub span at all is a violation too: the per-span checks
    pass vacuously, the run costs $0.00 and would win every cost tiebreak on
    evidence that was never read.

    Args:
        combo: The combo the spans were captured under.
        spans: Every classified llm span of that combo.

    Returns:
        One line per violation, empty when the combo took effect.
    """
    allowed = {combo.model} | SHIPPED_NON_HUB_MODELS
    violations: list[str] = []
    if not any(span.kind == "hub" for span in spans):
        violations.append(
            f"{combo.key}: no hub llm span was captured across {len(combo.results)} case(s); "
            "its packets or their trace evidence are missing, so the pin cannot be verified "
            "and its cost would be understated as $0.00"
        )
    for span in spans:
        if span.model not in allowed:
            violations.append(
                f"{combo.key} {span.case_id} span {span.span_id} ({span.kind}): model "
                f"{span.model!r} is outside the allowlist {sorted(allowed)}"
            )
        if span.kind != "hub":
            continue
        violations.extend(_hub_span_violations(combo, span))
    return violations


def _hub_span_violations(combo: ComboArtifacts, span: ClassifiedSpan) -> list[str]:
    violations: list[str] = []
    if span.model != combo.model:
        violations.append(
            f"{combo.key} {span.case_id} span {span.span_id}: hub model is "
            f"{span.model!r}, expected {combo.model!r}"
        )
    if span.effort != combo.effort:
        violations.append(
            f"{combo.key} {span.case_id} span {span.span_id}: hub reasoning effort is "
            f"{span.effort!r}, expected {combo.effort!r}"
        )
    return violations


def enforce_took_effect(combos: Sequence[ComboArtifacts], scans: dict[str, ComboScan]) -> None:
    """Fail the report when any combo's captured spans contradict its config.

    Args:
        combos: Every loaded combo.
        scans: Classified spans keyed by combo key.

    Raises:
        FairnessError: Any combo did not take effect.
    """
    violations: list[str] = []
    for combo in combos:
        violations.extend(took_effect_violations(combo, scans[combo.key].spans))
    if violations:
        msg = "benchmarked combos did not take effect:\n  " + "\n  ".join(violations)
        raise FairnessError(msg)


# ---------------------------------------------------------------------------
# dollars
# ---------------------------------------------------------------------------


def load_prices(prices_path: Path) -> dict[str, Any]:
    """Load the per-model price table.

    Args:
        prices_path: Path to ``model_prices.yaml``.

    Returns:
        The ``prices`` mapping.

    Raises:
        ArtifactError: The file is missing, unreadable, or has no prices map.
    """
    if not prices_path.is_file():
        msg = f"missing price table: {prices_path}"
        raise ArtifactError(msg)
    try:
        raw = yaml.safe_load(prices_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read price table {prices_path}: {exc}"
        raise ArtifactError(msg) from exc
    prices = raw.get("prices") if isinstance(raw, dict) else None
    if not isinstance(prices, dict):
        msg = f"price table {prices_path} must contain a prices mapping"
        raise ArtifactError(msg)
    return prices


def model_rates(
    model: str, prices: dict[str, Any], prices_path: Path
) -> tuple[float, float, float]:
    """Return the (input, cached_input, output) USD-per-1M rates for a model.

    Args:
        model: Model name observed in a span.
        prices: The loaded price mapping.
        prices_path: Path used in error messages.

    Returns:
        The three rates.

    Raises:
        ArtifactError: The row is missing or any rate is null.
    """
    row = prices.get(model)
    if not isinstance(row, dict):
        msg = f"no price row for observed model {model!r}; add it to {prices_path}"
        raise ArtifactError(msg)
    rates: list[float] = []
    for name in ("input", "cached_input", "output"):
        value = row.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool):
            msg = f"price {name} for observed model {model!r} is not set in {prices_path}"
            raise ArtifactError(msg)
        rates.append(float(value))
    return rates[0], rates[1], rates[2]


def _token_count(span: ClassifiedSpan, keys: Sequence[str], default: int | None = None) -> int:
    for key in keys:
        value = span.usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    if default is not None:
        return default
    msg = f"{span.case_id}: llm span {span.span_id} usage has none of {list(keys)}"
    raise ArtifactError(msg)


def span_cost_usd(span: ClassifiedSpan, prices: dict[str, Any], prices_path: Path) -> float:
    """Price one llm span, splitting cached from uncached input tokens.

    Args:
        span: The classified span.
        prices: The loaded price mapping.
        prices_path: Path used in error messages.

    Returns:
        The span's cost in USD.

    Raises:
        ArtifactError: A rate or a usage counter is missing.
    """
    input_rate, cached_rate, output_rate = model_rates(span.model, prices, prices_path)
    input_tokens = _token_count(span, ("original_usage.input_tokens", "prompt_tokens"))
    cached_tokens = _token_count(
        span, ("original_usage.input_tokens_details.cached_tokens",), default=0
    )
    output_tokens = _token_count(span, ("original_usage.output_tokens", "completion_tokens"))
    if cached_tokens > input_tokens:
        msg = (
            f"{span.case_id}: llm span {span.span_id} reports {cached_tokens} cached of "
            f"{input_tokens} input tokens"
        )
        raise ArtifactError(msg)
    billed = (
        (input_tokens - cached_tokens) * input_rate
        + cached_tokens * cached_rate
        + output_tokens * output_rate
    )
    return billed / 1e6


def combo_cost_usd(scan: ComboScan, prices: dict[str, Any], prices_path: Path) -> float:
    """Sum every llm span's cost for one combo.

    Args:
        scan: The combo's classified spans.
        prices: The loaded price mapping.
        prices_path: Path used in error messages.

    Returns:
        The combo's run cost in USD.
    """
    return sum(span_cost_usd(span, prices, prices_path) for span in scan.spans)


# ---------------------------------------------------------------------------
# scoring and ranking
# ---------------------------------------------------------------------------


def decided_case_ids(results: Sequence[CaseResult]) -> set[str]:
    """Return the cases a run actually decided.

    Args:
        results: One combo's reviewed case results.

    Returns:
        Case ids whose verdict is pass, pass_degraded, or fail_product.
    """
    return {result.case_id for result in results if result.verdict in DECIDED_VERDICTS}


def score_combo(combo: ComboArtifacts, intersection: set[str], cost_usd: float) -> ComboScore:
    """Score one combo over the cases every combo decided.

    Args:
        combo: The loaded combo.
        intersection: Case ids decided by every combo.
        cost_usd: The combo's run cost.

    Returns:
        The combo's scoreboard row.
    """
    inside = [result for result in combo.results if result.case_id in intersection]
    latencies = [result.latency_ms for result in inside if result.latency_ms is not None]
    excluded = [
        {"case_id": result.case_id, "verdict": result.verdict}
        for result in combo.results
        if result.case_id not in intersection
    ]
    disqualified = any(
        result.verdict == FAIL_VERDICT and GUARDRAIL_FEATURE_MARKER in result.feature.lower()
        for result in combo.results
    )
    strict = sum(1 for result in inside if result.verdict == STRICT_VERDICT)
    degraded = sum(1 for result in inside if result.verdict == DEGRADED_VERDICT)
    return ComboScore(
        key=combo.key,
        strict=strict,
        total=strict + degraded,
        cost_usd=cost_usd,
        median_latency_ms=statistics.median(latencies) if latencies else None,
        disqualified=disqualified,
        excluded=sorted(excluded, key=lambda row: row["case_id"]),
    )


def _rank_key(score: ComboScore) -> tuple[int, int, float, float]:
    latency = math.inf if score.median_latency_ms is None else score.median_latency_ms
    return (-score.strict, -score.total, score.cost_usd, latency)


def rank_scores(scores: Sequence[ComboScore]) -> tuple[list[ComboScore], list[list[str]]]:
    """Order combos lexicographically and report the pairs that stayed tied.

    Args:
        scores: One row per combo.

    Returns:
        The ranked rows and every adjacent still-tied pair of combo keys.
    """
    ranked = sorted(scores, key=_rank_key)
    ties = [
        [ranked[index].key, ranked[index + 1].key]
        for index in range(len(ranked) - 1)
        if _rank_key(ranked[index]) == _rank_key(ranked[index + 1])
    ]
    return ranked, ties


def podium_assessment(ranked: Sequence[ComboScore], cases_outside: int) -> dict[str, Any]:
    """Decide whether the measured cases can carry a recommendation.

    When at least as many cases sit outside the decided intersection as the
    strict-score gap between the top two combos, those unmeasured cases could
    reorder the podium, so the ranking is reported but not acted on.

    Two refinements of the contract's literal wording, both surfaced rather
    than silent: the gap is measured between the top two combos that are
    *eligible* for a recommendation (a safety-disqualified rank 1 can never be
    recommended, so its score cannot support one either), and a session that
    left no case outside the intersection is always decision-grade — with
    nothing unmeasured there is nothing that could reorder the podium, and the
    literal ``outside >= gap`` rule would otherwise suppress every
    fully-measured cost tiebreak, which is the comparison this skill exists to
    make.

    Args:
        ranked: Combos in rank order.
        cases_outside: Cases not decided by every combo.

    Returns:
        The arithmetic and the decision-grade flag.
    """
    eligible = [score for score in ranked if not score.disqualified]
    if len(eligible) < 2:
        return {
            "cases_outside_intersection": cases_outside,
            "strict_gap": None,
            "decision_grade": True,
            "explanation": (
                f"fewer than two recommendable combos ({len(eligible)}): nothing to compare"
            ),
        }
    gap = eligible[0].strict - eligible[1].strict
    decision_grade = cases_outside == 0 or cases_outside < gap
    explanation = (
        f"{cases_outside} case(s) outside the decided intersection vs a strict-score gap of "
        f"{gap} ({eligible[0].key} {eligible[0].strict} - {eligible[1].key} {eligible[1].strict})"
    )
    return {
        "cases_outside_intersection": cases_outside,
        "strict_gap": gap,
        "decision_grade": decision_grade,
        "explanation": explanation,
    }


def recommend(ranked: Sequence[ComboScore], decision_grade: bool) -> str | None:
    """Pick the top ranked combo that is not safety-disqualified.

    Args:
        ranked: Combos in rank order.
        decision_grade: Whether the podium arithmetic supports a call.

    Returns:
        The recommended combo key, or None when none can be recommended.
    """
    if not decision_grade:
        return None
    for score in ranked:
        if not score.disqualified:
            return score.key
    return None


# ---------------------------------------------------------------------------
# final mode
# ---------------------------------------------------------------------------


def build_final_report(
    session_dir: Path,
    session: dict[str, Any],
    combos: Sequence[ComboArtifacts],
    scans: dict[str, ComboScan],
    costs: dict[str, float],
) -> dict[str, Any]:
    """Assemble the full benchmark report structure.

    Args:
        session_dir: Benchmark session directory.
        session: The session manifest.
        combos: Every loaded combo.
        scans: Classified spans keyed by combo key.
        costs: Run cost in USD keyed by combo key.

    Returns:
        The report mapping written to ``benchmark.json``.
    """
    decided = {combo.key: decided_case_ids(combo.results) for combo in combos}
    all_cases = {result.case_id for combo in combos for result in combo.results}
    intersection = set.intersection(*decided.values()) if decided else set()
    scores = [score_combo(combo, intersection, costs[combo.key]) for combo in combos]
    ranked, ties = rank_scores(scores)
    podium = podium_assessment(ranked, len(all_cases - intersection))
    by_key = {combo.key: combo for combo in combos}
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "final",
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "session_id": session.get("session_id"),
        "session_dir": str(session_dir),
        "tier": session.get("tier"),
        "incumbent": session.get("incumbent"),
        "combos": [_combo_row(by_key[s.key], s, scans[s.key]) for s in ranked],
        "intersection": sorted(intersection),
        "intersection_size": len(intersection),
        "outside_intersection": sorted(all_cases - intersection),
        "ranking": [score.key for score in ranked],
        "ties": ties,
        "podium": podium,
        "decision_grade": bool(podium["decision_grade"]),
        "recommended": recommend(ranked, bool(podium["decision_grade"])),
        "case_matrix": _case_matrix(combos),
        "fairness": {
            "cases_snapshot_sha256": combos[0].manifest["cases_snapshot_sha256"],
            "suite_fingerprint": combos[0].manifest["suite_fingerprint"],
            "git_sha": combos[0].manifest["git"]["sha"],
            "git_dirty": combos[0].manifest["git"]["dirty"],
            "source_digest": combos[0].source_digest,
        },
        "warnings": fairness_warnings(combos, session),
        "diagnostics": [note for combo in combos for note in scans[combo.key].diagnostics],
    }


def _combo_row(combo: ComboArtifacts, score: ComboScore, scan: ComboScan) -> dict[str, Any]:
    return {
        "key": score.key,
        "model": combo.model,
        "effort": combo.effort,
        "run_dir": str(combo.run_dir),
        "source_digest": combo.source_digest,
        "strict": score.strict,
        "total": score.total,
        "cost_usd": score.cost_usd,
        "median_latency_ms": score.median_latency_ms,
        "disqualified": score.disqualified,
        "excluded": score.excluded,
        "llm_span_count": len(scan.spans),
    }


def _case_matrix(combos: Sequence[ComboArtifacts]) -> dict[str, dict[str, str]]:
    matrix: dict[str, dict[str, str]] = {}
    for combo in combos:
        for result in combo.results:
            matrix.setdefault(result.case_id, {})[combo.key] = result.verdict
    return dict(sorted(matrix.items()))


def _fmt_latency(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def render_final_markdown(report: dict[str, Any]) -> str:
    """Render the human-facing scoreboard.

    Args:
        report: The report structure from :func:`build_final_report`.

    Returns:
        Markdown text for ``benchmark.md``.
    """
    lines = [
        "# OBaI hub model benchmark",
        "",
        f"Session `{report['session_id']}` · tier `{report['tier']}` · "
        f"generated {report['generated_at']}",
        "",
        "## Scoreboard",
        "",
        "| Rank | Combo | Flag | Strict | Total | Cost (USD) | Median latency (ms) | Excluded |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, combo in enumerate(report["combos"], start=1):
        flag = "DISQUALIFIED" if combo["disqualified"] else ""
        lines.append(
            f"| {rank} | {combo['key']} | {flag} | {combo['strict']} | {combo['total']} | "
            f"{combo['cost_usd']:.4f} | {_fmt_latency(combo['median_latency_ms'])} | "
            f"{len(combo['excluded'])} |"
        )
    lines += [
        "",
        f"Scored over {report['intersection_size']} case(s) decided by every combo: "
        f"{', '.join(report['intersection']) or 'none'}.",
        "",
    ]
    lines += _markdown_ties(report)
    lines += _markdown_warnings(report)
    lines += _markdown_excluded(report)
    lines += _markdown_matrix(report)
    lines += _markdown_recommendation(report)
    return "\n".join(lines) + "\n"


def _markdown_ties(report: dict[str, Any]) -> list[str]:
    if not report["ties"]:
        return []
    pairs = "; ".join(" tie ".join(pair) for pair in report["ties"])
    return [f"Unbroken tie after every criterion: {pairs}.", ""]


def _markdown_warnings(report: dict[str, Any]) -> list[str]:
    entries = list(report["warnings"]) + list(report["diagnostics"])
    if not entries:
        return []
    return ["## Warnings", "", *[f"- {entry}" for entry in entries], ""]


def _markdown_excluded(report: dict[str, Any]) -> list[str]:
    lines = ["## Excluded cases", ""]
    for combo in report["combos"]:
        rendered = ", ".join(f"{row['case_id']} ({row['verdict']})" for row in combo["excluded"])
        lines.append(f"- **{combo['key']}**: {rendered or 'none'}")
    lines.append("")
    return lines


def _markdown_matrix(report: dict[str, Any]) -> list[str]:
    keys = report["ranking"]
    lines = ["## Per-case verdicts", "", "| Case | " + " | ".join(keys) + " |"]
    lines.append("| --- |" + " --- |" * len(keys))
    for case_id, cells in report["case_matrix"].items():
        rendered = " | ".join(cells.get(key, "—") for key in keys)
        lines.append(f"| {case_id} | {rendered} |")
    lines.append("")
    return lines


def _markdown_recommendation(report: dict[str, Any]) -> list[str]:
    lines = ["## Recommendation", "", f"Podium check: {report['podium']['explanation']}.", ""]
    if not report["decision_grade"]:
        lines += [
            "This ranking is **not decision-grade**: the cases outside the decided "
            "intersection could reorder the podium, so no combo is recommended.",
            "",
            "Rerun the excluded cases listed above for every combo (same tier, same "
            "snapshot) and regenerate this report before choosing a default.",
            "",
        ]
        return lines
    if report["recommended"] is None:
        lines += ["Every ranked combo is safety-disqualified; no recommendation.", ""]
        return lines
    incumbent = report.get("incumbent") or {}
    incumbent_key = f"{incumbent.get('model')}:{incumbent.get('effort')}"
    lines.append(f"Recommended combo: **{report['recommended']}**.")
    if report["recommended"] != incumbent_key:
        lines += [
            "",
            f"This differs from the incumbent `{incumbent_key}`. Confirmation repeats of "
            "both combos are required before changing the shipped default; this report "
            "recommends, it does not change configuration.",
        ]
    lines.append("")
    return lines


def append_ledger(ledger_path: Path, report: dict[str, Any]) -> None:
    """Append one append-only ledger line for this benchmark session.

    Args:
        ledger_path: Path to ``ledger.jsonl``; parent dirs are created.
        report: The final report structure.

    Raises:
        ArtifactError: The ledger could not be written.
    """
    entry = {
        "ts": report["generated_at"],
        "session_id": report["session_id"],
        "tier": report["tier"],
        "ranking": report["ranking"],
        "recommended": report["recommended"],
        "session_dir": report["session_dir"],
    }
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        msg = f"cannot append to ledger {ledger_path}: {exc}"
        raise ArtifactError(msg) from exc


def write_text(path: Path, text: str) -> None:
    """Write a report file.

    Args:
        path: Destination file.
        text: Contents to write.

    Raises:
        ArtifactError: The file could not be written.
    """
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write {path}: {exc}"
        raise ArtifactError(msg) from exc


def run_final(
    session_dir: Path,
    *,
    prices_path: Path = DEFAULT_PRICES_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> int:
    """Run the fairness gates, score the combos, and write the final report.

    Args:
        session_dir: Benchmark session directory.
        prices_path: Price table to cost the captured spans with.
        ledger_path: Append-only benchmark ledger.

    Returns:
        ``EXIT_SUCCESS`` once both report files and the ledger line are written.

    Raises:
        ArtifactError: An artifact or price rate is missing or malformed.
        FairnessError: The combos are not comparable or did not take effect.
    """
    session = load_session(session_dir)
    combos = [load_combo(session_dir, entry) for entry in session["combos"]]
    check_fairness(combos)
    scans = {combo.key: scan_combo_spans(combo) for combo in combos}
    enforce_took_effect(combos, scans)
    prices = load_prices(prices_path)
    costs = {combo.key: combo_cost_usd(scans[combo.key], prices, prices_path) for combo in combos}
    report = build_final_report(session_dir, session, combos, scans, costs)
    write_text(session_dir / "benchmark.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_text(session_dir / "benchmark.md", render_final_markdown(report))
    append_ledger(ledger_path, report)
    for warning in report["warnings"] + report["diagnostics"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"ranking: {' > '.join(report['ranking'])}")
    print(f"recommended: {report['recommended'] or 'none (see benchmark.md)'}")
    print(f"wrote {session_dir / 'benchmark.json'} and {session_dir / 'benchmark.md'}")
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# audit mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditCombo:
    """One combo's pre-finalize artifacts."""

    key: str
    run_dir: Path
    results: list[CaseResult]
    reviews: dict[str, dict[str, str]]


def load_audit_combo(session_dir: Path, entry: dict[str, Any]) -> AuditCombo:
    """Load one combo's preliminary results and any draft semantic reviews.

    Args:
        session_dir: Benchmark session directory.
        entry: The session manifest entry for this combo.

    Returns:
        The combo's audit inputs.

    Raises:
        ArtifactError: ``results.json`` or a present draft review is missing
            or malformed.
    """
    key = combo_key(entry)
    run_dir = session_dir / str(entry["run_dir"])
    results = parse_case_results(
        load_json_object(run_dir / "results.json", f"{key} results.json"), f"{key} results.json"
    )
    reviews_path = run_dir / "semantic_reviews.json"
    reviews: dict[str, dict[str, str]] = {}
    if reviews_path.is_file():
        label = f"{key} semantic_reviews.json"
        reviews = _draft_statuses(label, load_json_object(reviews_path, label))
    return AuditCombo(key=key, run_dir=run_dir, results=results, reviews=reviews)


def _draft_statuses(label: str, payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Parse a draft ``semantic_reviews.json`` into per-case assertion statuses.

    A shape this loader cannot read is raised, never skipped: silently parsing
    zero statuses would make every combo look identical and the audit would
    report "no disagreements" for drafts it never actually compared.
    """
    rows = payload.get("reviews")
    if not isinstance(rows, list):
        msg = f"{label} needs a reviews list (the gate's semantic review schema)"
        raise ArtifactError(msg)
    statuses: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            msg = f"{label} reviews[{index}] must be an object"
            raise ArtifactError(msg)
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            msg = f"{label} reviews[{index}] has no case_id"
            raise ArtifactError(msg)
        statuses[case_id] = _assertion_statuses(f"{label} {case_id}", row.get("assertions"))
    return statuses


def _assertion_statuses(label: str, assertions: object) -> dict[str, str]:
    if not isinstance(assertions, list):
        msg = f"{label} needs an assertions list"
        raise ArtifactError(msg)
    statuses: dict[str, str] = {}
    for index, decision in enumerate(assertions):
        if not isinstance(decision, dict):
            msg = f"{label} assertions[{index}] must be an object"
            raise ArtifactError(msg)
        assertion = decision.get("assertion")
        status = decision.get("status")
        if not isinstance(assertion, str) or not isinstance(status, str):
            msg = f"{label} assertions[{index}] needs string assertion and status fields"
            raise ArtifactError(msg)
        statuses[assertion] = status
    return statuses


def build_audit_matrix(combos: Sequence[AuditCombo]) -> dict[str, dict[str, Any]]:
    """Build the per-case, per-combo deterministic verdict matrix.

    Args:
        combos: Every combo's audit inputs.

    Returns:
        ``{case_id: {combo_key: cell}}`` sorted by case id.
    """
    matrix: dict[str, dict[str, Any]] = {}
    for combo in combos:
        for result in combo.results:
            cell = {
                "verdict": result.deterministic_verdict,
                "packet_path": _audit_packet_path(combo, result),
                "semantic_statuses": combo.reviews.get(result.case_id, {}),
            }
            matrix.setdefault(result.case_id, {})[combo.key] = cell
    return dict(sorted(matrix.items()))


def _audit_packet_path(combo: AuditCombo, result: CaseResult) -> str | None:
    path = resolve_packet_path(combo.run_dir, result.case_id, result.packet_path, combo.key)
    return str(path) if path is not None else None


def audit_worklist(matrix: dict[str, dict[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """List every case whose verdict or draft statuses differ across combos.

    Args:
        matrix: The audit matrix.
        keys: Every combo key, in session order.

    Returns:
        One entry per disagreeing case, with each combo's packet path.
    """
    worklist: list[dict[str, Any]] = []
    for case_id, cells in matrix.items():
        signatures = {_audit_signature(cells.get(key)) for key in keys}
        if len(signatures) <= 1:
            continue
        worklist.append(
            {
                "case_id": case_id,
                "verdicts": {key: cell["verdict"] for key, cell in cells.items()},
                "semantic_statuses": {
                    key: cell["semantic_statuses"] for key, cell in cells.items()
                },
                "packet_paths": {key: cell["packet_path"] for key, cell in cells.items()},
            }
        )
    return worklist


def _audit_signature(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return "<absent>"
    return json.dumps([cell["verdict"], sorted(cell["semantic_statuses"].items())], sort_keys=True)


def render_audit_markdown(audit: dict[str, Any]) -> str:
    """Render the pre-finalize audit matrix and disagreement worklist.

    Args:
        audit: The audit structure.

    Returns:
        Markdown text for ``audit.md``.
    """
    keys = audit["combos"]
    lines = [
        "# Benchmark review audit",
        "",
        f"Session `{audit['session_id']}` · generated {audit['generated_at']}",
        "",
        "Deterministic verdicts (draft semantic statuses in parentheses). Settle the "
        "worklist below before running `finalize_review.py`.",
        "",
        "## Verdict matrix",
        "",
        "| Case | " + " | ".join(keys) + " |",
        "| --- |" + " --- |" * len(keys),
    ]
    for case_id, cells in audit["matrix"].items():
        rendered = " | ".join(_audit_cell(cells.get(key)) for key in keys)
        lines.append(f"| {case_id} | {rendered} |")
    lines += ["", "## Disagreement worklist", ""]
    if not audit["worklist"]:
        lines += ["No disagreements: every combo reached the same status on every case.", ""]
        return "\n".join(lines) + "\n"
    for entry in audit["worklist"]:
        lines.append(f"- **{entry['case_id']}**")
        lines += [
            f"  - `{key}`: {entry['verdicts'].get(key, 'absent')} — {entry['packet_paths'].get(key)}"
            for key in keys
        ]
    lines.append("")
    return "\n".join(lines) + "\n"


def _audit_cell(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return "—"
    statuses = cell["semantic_statuses"]
    if not statuses:
        return str(cell["verdict"])
    rendered = ", ".join(f"{status}" for _, status in sorted(statuses.items()))
    return f"{cell['verdict']} ({rendered})"


def run_audit(session_dir: Path) -> int:
    """Write the pre-finalize audit matrix and disagreement worklist.

    Args:
        session_dir: Benchmark session directory.

    Returns:
        ``EXIT_SUCCESS`` once ``audit.json`` and ``audit.md`` are written.

    Raises:
        ArtifactError: A required artifact is missing or malformed.
    """
    session = load_session(session_dir)
    combos = [load_audit_combo(session_dir, entry) for entry in session["combos"]]
    matrix = build_audit_matrix(combos)
    keys = [combo.key for combo in combos]
    audit = {
        "schema_version": SCHEMA_VERSION,
        "mode": "audit",
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "session_id": session.get("session_id"),
        "session_dir": str(session_dir),
        "combos": keys,
        "matrix": matrix,
        "worklist": audit_worklist(matrix, keys),
    }
    write_text(session_dir / "audit.json", json.dumps(audit, indent=2, sort_keys=True) + "\n")
    write_text(session_dir / "audit.md", render_audit_markdown(audit))
    print(f"audit: {len(matrix)} case(s), {len(audit['worklist'])} disagreement(s)")
    print(f"wrote {session_dir / 'audit.json'} and {session_dir / 'audit.md'}")
    return EXIT_SUCCESS


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        0 on success, 2 on a config/artifact error, 3 on a fairness or
        took-effect violation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Pre-finalize matrix of deterministic verdicts plus a disagreement worklist",
    )
    args = parser.parse_args(argv)
    try:
        if args.audit:
            return run_audit(args.session_dir)
        return run_final(args.session_dir)
    except ArtifactError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except FairnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAIRNESS


if __name__ == "__main__":
    raise SystemExit(main())
