#!/usr/bin/env -S uv run python
"""Render an e2e regression run as a structured HTML report.

Reads immutable run artifacts from a finished run:
  * <run_dir>/reviewed-results.json when present, otherwise results.json
    — verdict + latency + trace ids per case
  * <run_dir>/manifest.json  — exact materialized case snapshots used by the run

Legacy report.md/cases.yaml inputs remain a fallback for older runs.

Writes two human-readable reports from the same structured data:
  * <run_dir>/report.md  — a greppable markdown dashboard: one
    ``| ID | Feature | Verdict | Reason | Trace | Latency |`` row per case,
    plus a compact evidence block under each non-pass case.
  * <run_dir>/report.html — stat cards, verdict distribution bar, then one
    card per case (query always shown; expected contract + structured failure
    evidence shown for non-pass cases). Disabled cases listed at the end.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = SKILL_DIR / "cases" / "cases.yaml"

VERDICTS = (
    "pass",
    "pass_degraded",
    "fail_product",
    "needs_semantic_review",
    "inconclusive_provider",
    "inconclusive_harness",
    "inconclusive_missing_evidence",
    "skipped_dependency",
)
COLOR = {
    "pass": "#1b6e23",
    "pass_degraded": "#477a2f",
    "fail_product": "#b3261e",
    "needs_semantic_review": "#a86a00",
    "inconclusive_provider": "#6b6a64",
    "inconclusive_harness": "#5b6472",
    "inconclusive_missing_evidence": "#755b7e",
    "skipped_dependency": "#6b6a64",
}
BG = {
    "pass": "#cfe7d3",
    "pass_degraded": "#deebd5",
    "fail_product": "#fbdcd9",
    "needs_semantic_review": "#f6e3b8",
    "inconclusive_provider": "#e8e6dc",
    "inconclusive_harness": "#e2e6ec",
    "inconclusive_missing_evidence": "#ece1ef",
    "skipped_dependency": "#efeee9",
}


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text())
    cases = raw.get("test_cases", []) if isinstance(raw, dict) else []
    return {c["id"]: c for c in cases if "id" in c}


def load_manifest_cases(run_dir: Path) -> tuple[dict[str, dict[str, Any]], str, dict[str, Any]]:
    """Load immutable case snapshots; never substitute current YAML for history."""
    path = run_dir / "manifest.json"
    if not path.exists():
        return {}, "", {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json root must be an object")
    cases: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("cases", []):
        if not isinstance(entry, dict):
            continue
        snapshot = entry.get("snapshot")
        case_id = entry.get("id")
        if isinstance(case_id, str) and isinstance(snapshot, dict):
            cases[case_id] = snapshot
    created_at = manifest.get("created_at")
    return cases, created_at if isinstance(created_at, str) else "", manifest


def parse_detail_blocks(md: str) -> dict[str, dict[str, str]]:
    """Pull `**<ID> — <verdict>**` blocks (4 dash-prefixed fields) into a dict."""
    out: dict[str, dict[str, str]] = {}
    pat = re.compile(r"^\*\*([A-Z0-9][A-Z0-9_.-]*) — ([^*]+)\*\*$", re.MULTILINE)
    matches = list(pat.finditer(md))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[m.end() : end].strip()
        fields: dict[str, str] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            key, val = line[2:].split(":", 1)
            fields[key.strip().lower()] = val.strip()
        out[m.group(1)] = fields
    return out


def parse_timestamp(md: str) -> str:
    m = re.search(r"^# OBaI E2E Regression — (.+)$", md, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _one_line(value: Any) -> str:
    """Collapse whitespace so a value fits on a single markdown/table line."""
    return " ".join(str(value).split())


def _expected_contract(snapshot: dict[str, Any]) -> str:
    """Human-readable routing contract from an immutable case snapshot.

    The legacy pipeline had the judging agent hand-write an ``expected`` line
    into report.md. The current pipeline captures the contract structurally, so
    rebuild the same line from it instead of leaving the field blank.
    """
    parts: list[str] = []
    tools = snapshot.get("expected_tools")
    if isinstance(tools, list) and tools:
        parts.append("tools: " + ", ".join(map(str, tools)))
    sequence = snapshot.get("expected_sequence")
    if isinstance(sequence, list) and sequence:
        parts.append("seq: " + " → ".join(map(str, sequence)))
    skills = snapshot.get("expected_skills")
    if isinstance(skills, list) and skills:
        parts.append("skills: " + ", ".join(map(str, skills)))
    if snapshot.get("expect_rejection") is True:
        parts.append("guardrail rejection")
    return "; ".join(parts)


def build_details(
    results: dict[str, Any],
    cases: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Reconstruct per-case detail fields from structured run evidence.

    Only ``expected`` is derived here: the trace-observed deviation and the
    judgment already surface as the case's own ``reason`` / ``checks_failed`` /
    ``semantic_review`` fields, so re-deriving them would duplicate. Never
    invents a field that the run did not capture.
    """
    details: dict[str, dict[str, str]] = {}
    for case in results.get("results", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("id") or "")
        snapshot = cases.get(case_id, {})
        if not case_id or not isinstance(snapshot, dict):
            continue
        expected = _expected_contract(snapshot)
        if expected:
            details[case_id] = {"expected": expected}
    return details


def fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def fmt_latency(case: dict[str, Any]) -> str:
    parts: list[str] = []
    primary = case.get("latency_ms")
    if primary is not None:
        parts.append(fmt_ms(primary))
    if case.get("rerun_latency_ms") is not None:
        parts.append(f"→ {fmt_ms(case['rerun_latency_ms'])}")
    wait = case.get("followup_wait_seconds")
    if case.get("followup_latency_ms") is not None:
        prefix = f"+{wait}s+" if wait else "+"
        parts.append(f"{prefix}{fmt_ms(case['followup_latency_ms'])}")
    return " ".join(parts) if parts else "—"


def fmt_trace(case: dict[str, Any]) -> str:
    ids: list[str] = []
    for key in ("trace_id", "rerun_trace_id", "followup_trace_id"):
        tid = case.get(key)
        if tid:
            ids.append(tid[:8] if key == "trace_id" else f"→ {tid[:8]}")
    return " ".join(ids) if ids else "—"


def render_case(case: dict[str, Any], yaml_case: dict[str, Any], detail: dict[str, str]) -> str:
    verdict = case.get("verdict", "inconclusive_harness")
    if verdict not in COLOR:
        verdict = "inconclusive_harness"
    label = verdict
    if case.get("rerun_trace_id"):
        label = f"{verdict} · rerun"
    raw_case_id = case.get("case_id") or case.get("id") or ""
    cid = html.escape(str(raw_case_id))
    feature = html.escape(case.get("feature") or yaml_case.get("feature") or "")
    query = yaml_case.get("query", "")
    badge = (
        f'<span class="badge" style="background:{BG[verdict]};color:{COLOR[verdict]};">'
        f"{html.escape(label)}</span>"
    )
    header = (
        f'<div class="chead">'
        f"{badge}"
        f'<span class="cid">{cid}</span>'
        f'<span class="cfeat">{feature}</span>'
        f'<span class="cmeta">{html.escape(fmt_trace(case))} · {html.escape(fmt_latency(case))}</span>'
        f"</div>"
    )
    body_parts: list[str] = []
    if query:
        body_parts.append(f'<div class="query">{html.escape(query)}</div>')
    semantic_review = case.get("semantic_review")
    if verdict != "pass" or isinstance(semantic_review, dict):
        reason = case.get("reason")
        if reason:
            body_parts.append(_field("reason", reason))
        body_parts.extend(
            _field(k, detail[k]) for k in ("expected", "observed", "judgment") if k in detail
        )
        for key in ("checks_failed", "missing_evidence", "unexecuted_assertions"):
            values = case.get(key)
            if isinstance(values, list) and values:
                body_parts.append(_field(key.replace("_", " "), "; ".join(map(str, values))))
        if isinstance(semantic_review, dict) and semantic_review.get("summary"):
            body_parts.append(_field("semantic review", str(semantic_review["summary"])))
        body_parts.extend(
            _field(k, v)
            for k, v in detail.items()
            if k not in {"query", "expected", "observed", "judgment"}
        )
    body = f'<div class="cbody">{"".join(body_parts)}</div>' if body_parts else ""
    return f'<section class="card-row" style="border-left-color:{COLOR[verdict]};">{header}{body}</section>'


def _field(label: str, value: str) -> str:
    return (
        f'<div class="field">'
        f'<span class="flabel">{html.escape(label)}</span>'
        f'<span class="fval">{html.escape(value)}</span>'
        f"</div>"
    )


def render(
    results: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    details: dict[str, dict[str, str]],
    timestamp: str,
    run_dir: Path,
) -> str:
    cases_out = results.get("results", [])
    disabled = results.get("disabled", [])

    counts = dict.fromkeys(VERDICTS, 0)
    total_ms = 0
    for c in cases_out:
        verdict = c.get("verdict", "inconclusive_harness")
        if verdict not in counts:
            verdict = "inconclusive_harness"
        counts[verdict] = counts.get(verdict, 0) + 1
        for key in ("latency_ms", "rerun_latency_ms", "followup_latency_ms"):
            ms = c.get(key)
            if ms:
                total_ms += ms
    total = len(cases_out)
    pass_total = counts["pass"] + counts["pass_degraded"]
    pass_pct = (pass_total / total * 100) if total else 0.0

    cards = "".join(
        f'<div class="card" style="border-left-color:{COLOR[v]};">'
        f'<div class="cn" style="color:{COLOR[v]};">{counts[v]}</div>'
        f'<div class="cl">{v.replace("_", " ")}</div>'
        f"</div>"
        for v in VERDICTS
    )

    bar_segs = []
    for v in VERDICTS:
        n = counts[v]
        if n == 0 or total == 0:
            continue
        bar_segs.append(
            f'<span style="width:{n / total * 100:.2f}%;background:{COLOR[v]};" '
            f'title="{v}: {n}"></span>'
        )
    bar = f'<div class="bar">{"".join(bar_segs)}</div>'

    meta = (
        f'<div class="meta-row">'
        f"<span><b>{total}</b> cases</span><span>·</span>"
        f"<span><b>{pass_pct:.0f}%</b> pass</span><span>·</span>"
        f"<span>runtime <b>{total_ms / 1000:.0f}s</b></span>"
        + (f"<span>·</span><span><b>{len(disabled)}</b> disabled</span>" if disabled else "")
        + (
            f"<span>·</span><span>minimum estimated model requests "
            f"<b>{html.escape(str(results.get('estimated_model_requests', results.get('estimated_api_calls'))))}</b></span>"
            if results.get("estimated_model_requests", results.get("estimated_api_calls"))
            is not None
            else ""
        )
        + (
            f"<span>·</span><span>observed model requests "
            f"<b>{html.escape(str(results.get('observed_model_requests')))}</b></span>"
            if results.get("observed_model_requests") is not None
            else ""
        )
        + (
            f"<span>·</span><span>between-case start limit "
            f"<b>{html.escape(str(results.get('between_case_model_request_limit')))}</b></span>"
            if results.get("between_case_model_request_limit") is not None
            else ""
        )
        + "</div>"
    )

    case_html = "".join(
        render_case(
            c,
            cases.get(str(c.get("case_id") or c.get("id") or ""), {}),
            details.get(str(c.get("case_id") or c.get("id") or ""), {}),
        )
        for c in cases_out
    )

    disabled_html = ""
    if disabled:
        items = "".join(
            f'<li><span class="mono">{html.escape(d.get("id", ""))}</span> — '
            f"{html.escape(d.get('reason', ''))}</li>"
            for d in disabled
        )
        disabled_html = f'<h2>Disabled ({len(disabled)})</h2><ul class="disabled">{items}</ul>'

    title = f"OBaI E2E Regression — {html.escape(timestamp or 'run')}"
    subtitle = html.escape(timestamp) if timestamp else "run"
    head = HEAD.format(title=title, subtitle=subtitle, run_dir=html.escape(str(run_dir)))
    abort_reason = results.get("abort_reason")
    abort_html = (
        f'<div class="abort"><b>Run stopped:</b> {html.escape(str(abort_reason))}</div>'
        if abort_reason
        else ""
    )
    harness_failures = results.get("harness_failures") or []
    # Contained per-case harness failures no longer stop the run, so surface them
    # explicitly; silence would read as "every case produced trustworthy evidence".
    if isinstance(harness_failures, list) and harness_failures:
        contained = ", ".join(
            f"{item.get('case_id')} ({item.get('harness_status')})"
            for item in harness_failures
            if isinstance(item, dict)
        )
        abort_html += (
            '<div class="abort"><b>Contained harness failures:</b> '
            f"{html.escape(contained)}</div>"
        )
    cost_warning_html = (
        '<div class="abort"><b>Cost boundary:</b> the between-case start limit is not '
        "a hard cap; an in-flight nested agent can overshoot it.</div>"
        if results.get("hard_model_request_cap_enforced") is False
        else ""
    )
    return (
        head
        + cards_block(cards)
        + bar
        + meta
        + cost_warning_html
        + abort_html
        + case_html
        + disabled_html
        + FOOT
    )


def cards_block(cards: str) -> str:
    return f'<div class="cards">{cards}</div>'


def _md_cell(value: Any) -> str:
    """Escape a value for a single markdown table cell."""
    if value is None:
        return "—"
    text = _one_line(value)
    if not text:
        return "—"
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _markdown_detail_block(
    case: dict[str, Any],
    snapshot: dict[str, Any],
    detail: dict[str, str],
) -> str:
    """Compact evidence block for one non-pass case — dashboard, not prose."""
    case_id = str(case.get("case_id") or case.get("id") or "")
    verdict = str(case.get("verdict", "inconclusive_harness"))
    parts = [f"**{case_id} — {verdict}**"]
    query = snapshot.get("query", "") if isinstance(snapshot, dict) else ""
    if query:
        parts.append(f"- query: {_one_line(query)[:120]}")
    if detail.get("expected"):
        parts.append(f"- expected: {_one_line(detail['expected'])}")
    reason = case.get("reason")
    if reason:
        parts.append(f"- reason: {_one_line(reason)}")
    for key, label in (
        ("checks_failed", "checks failed"),
        ("missing_evidence", "missing evidence"),
        ("unexecuted_assertions", "unexecuted assertions"),
    ):
        values = case.get(key)
        if isinstance(values, list) and values:
            parts.append(f"- {label}: {'; '.join(_one_line(v) for v in values)}")
    review = case.get("semantic_review")
    if isinstance(review, dict) and review.get("summary"):
        parts.append(f"- semantic review: {_one_line(review['summary'])}")
    return "\n".join(parts)


def render_markdown(
    results: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    details: dict[str, dict[str, str]],
    timestamp: str,
) -> str:
    """Render the greppable markdown regression dashboard.

    One table row per case, then a compact evidence block under each non-pass
    case. Sourced entirely from structured results — no agent-authored prose.
    """
    lines = [
        f"# OBaI E2E Regression — {timestamp or 'run'}",
        "",
        "| ID | Feature | Verdict | Reason | Trace | Latency |",
        "|---|---|---|---|---|---|",
    ]
    blocks: list[str] = []
    for case in results.get("results", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("id") or "")
        snapshot = cases.get(case_id, {})
        feature = case.get("feature") or (
            snapshot.get("feature") if isinstance(snapshot, dict) else ""
        )
        verdict = str(case.get("verdict", "inconclusive_harness"))
        lines.append(
            f"| {_md_cell(case_id)} | {_md_cell(feature)} | {_md_cell(verdict)} "
            f"| {_md_cell(case.get('reason'))} | {_md_cell(fmt_trace(case))} "
            f"| {_md_cell(fmt_latency(case))} |"
        )
        if verdict != "pass":
            blocks.append(_markdown_detail_block(case, snapshot, details.get(case_id, {})))

    disabled = results.get("disabled", [])
    if isinstance(disabled, list) and disabled:
        ids = ", ".join(_one_line(d.get("id", "")) for d in disabled if isinstance(d, dict))
        lines.append("")
        lines.append(f"_Skipped {len(disabled)} disabled case(s): {ids}_")
    if blocks:
        lines.append("")
        lines.append("\n\n".join(blocks))
    return "\n".join(lines).rstrip() + "\n"


HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         background: #faf9f5; color: #141413; padding: 28px;
         max-width: 1100px; margin: 0 auto; line-height: 1.4; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 600; }}
  h2 {{ font-size: 14px; margin: 28px 0 10px; font-weight: 600;
       text-transform: uppercase; letter-spacing: 0.06em; color: #6b6a64; }}
  .meta {{ color: #6b6a64; font-size: 12px; margin: 0 0 20px;
          font-family: ui-monospace, "SF Mono", Menlo, monospace; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
           gap: 10px; margin-bottom: 14px; }}
  .card {{ background: white; border: 1px solid #e8e6dc; border-left: 3px solid;
          border-radius: 6px; padding: 12px 14px; }}
  .cn {{ font-size: 26px; font-weight: 600; line-height: 1; }}
  .cl {{ font-size: 11px; color: #6b6a64; text-transform: uppercase;
        letter-spacing: 0.06em; margin-top: 4px; }}
  .meta-row {{ display: flex; gap: 8px; align-items: center;
              font-size: 13px; color: #6b6a64; margin-bottom: 14px; }}
  .meta-row b {{ color: #141413; }}
  .bar {{ display: flex; height: 6px; border-radius: 3px;
         overflow: hidden; margin-bottom: 24px; background: #e8e6dc; }}
  .bar span {{ display: block; height: 100%; }}
  .abort {{ background: #fbdcd9; border: 1px solid #e4aaa4; color: #7f1d17;
            padding: 10px 12px; border-radius: 6px; margin-bottom: 14px;
            font-size: 13px; }}
  .card-row {{ background: white; border: 1px solid #e8e6dc;
              border-left: 3px solid; border-radius: 6px;
              padding: 12px 16px; margin: 0 0 8px; }}
  .chead {{ display: flex; align-items: center; gap: 10px;
           font-size: 13px; flex-wrap: wrap; }}
  .badge {{ display: inline-block; padding: 2px 9px; border-radius: 10px;
           font-size: 10px; font-weight: 600; text-transform: uppercase;
           letter-spacing: 0.04em; white-space: nowrap; }}
  .cid {{ font-family: ui-monospace, "SF Mono", Menlo, monospace;
         font-weight: 600; }}
  .cfeat {{ color: #6b6a64; }}
  .cmeta {{ margin-left: auto; font-family: ui-monospace, "SF Mono", Menlo, monospace;
           font-size: 11px; color: #6b6a64; }}
  .cbody {{ margin-top: 10px; padding-top: 10px;
           border-top: 1px solid #f0eee6; display: flex;
           flex-direction: column; gap: 6px; }}
  .query {{ font-family: ui-monospace, "SF Mono", Menlo, monospace;
           font-size: 12px; color: #2a2a28; word-break: break-word;
           background: #f6f4ec; padding: 8px 10px; border-radius: 4px; }}
  .field {{ display: grid; grid-template-columns: 90px 1fr; gap: 12px;
           font-size: 13px; }}
  .flabel {{ font-size: 10px; color: #6b6a64; text-transform: uppercase;
            letter-spacing: 0.06em; padding-top: 3px; }}
  .fval {{ word-break: break-word; }}
  .disabled {{ font-size: 13px; color: #6b6a64; padding-left: 18px; }}
  .disabled li {{ margin: 3px 0; }}
  .mono {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; }}
</style>
</head>
<body>
<h1>OBaI E2E Regression</h1>
<p class="meta">{subtitle} · {run_dir}</p>
"""

FOOT = "</body></html>\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Run directory containing results.json and report.md.",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        help="Path to cases.yaml (default: skill's cases/cases.yaml).",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Output HTML path (default: <run-dir>/report.html)."
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the rendered report in the default browser after writing.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        sys.stderr.write(f"run-dir not found: {run_dir}\n")
        return 2

    reviewed_path = run_dir / "reviewed-results.json"
    results_path = reviewed_path if reviewed_path.exists() else run_dir / "results.json"
    if not results_path.exists():
        sys.stderr.write(f"results.json not found in {run_dir}\n")
        return 2

    report_md = (run_dir / "report.md").read_text() if (run_dir / "report.md").exists() else ""
    results = json.loads(results_path.read_text())
    try:
        manifest_cases, manifest_timestamp, _manifest = load_manifest_cases(run_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid manifest.json in {run_dir}: {exc}\n")
        return 2
    cases = manifest_cases or load_cases(args.cases)
    # Structured evidence is authoritative; legacy report.md blocks only fill
    # fields (e.g. an old agent-authored judgment) that a pre-structured run
    # cannot reconstruct.
    legacy_details = parse_detail_blocks(report_md)
    structured_details = build_details(results, cases)
    details = {
        case_id: {**legacy_details.get(case_id, {}), **structured_details.get(case_id, {})}
        for case_id in set(legacy_details) | set(structured_details)
    }
    timestamp = manifest_timestamp or parse_timestamp(report_md)

    report_md_out = run_dir / "report.md"
    report_md_out.write_text(render_markdown(results, cases, details, timestamp))
    out = args.out or (run_dir / "report.html")
    out.write_text(render(results, cases, details, timestamp, run_dir))
    sys.stdout.write(f"Wrote {report_md_out}\n")
    sys.stdout.write(f"Wrote {out}\n")
    if args.open_browser:
        opened = webbrowser.open(out.resolve().as_uri())
        if not opened:
            sys.stderr.write(
                f"Could not launch a browser; open manually: {out.resolve().as_uri()}\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
