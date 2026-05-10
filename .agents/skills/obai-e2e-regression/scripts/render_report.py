#!/usr/bin/env -S uv run python
"""Render an e2e regression run as a structured HTML report.

Reads three sources from a finished run:
  * <run_dir>/results.json   — verdict + latency + trace ids per case
  * <run_dir>/report.md      — header timestamp + detail blocks for non-pass
  * cases/cases.yaml         — verbatim query for every case

Writes <run_dir>/report.html: stat cards, verdict distribution bar, then one
card per case (query always shown; expected/observed/judgment shown for
non-pass cases). Disabled cases listed at the end.
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

VERDICTS = ("pass", "fail", "needs_review", "inconclusive")
COLOR = {
    "pass": "#1b6e23",
    "fail": "#b3261e",
    "needs_review": "#a86a00",
    "inconclusive": "#6b6a64",
}
BG = {
    "pass": "#cfe7d3",
    "fail": "#fbdcd9",
    "needs_review": "#f6e3b8",
    "inconclusive": "#e8e6dc",
}


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text())
    cases = raw.get("test_cases", []) if isinstance(raw, dict) else []
    return {c["id"]: c for c in cases if "id" in c}


def parse_detail_blocks(md: str) -> dict[str, dict[str, str]]:
    """Pull `**<ID> — <verdict>**` blocks (4 dash-prefixed fields) into a dict."""
    out: dict[str, dict[str, str]] = {}
    pat = re.compile(r"^\*\*([A-Z0-9]+) — ([^*]+)\*\*$", re.MULTILINE)
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
    verdict = case.get("verdict", "inconclusive")
    label = verdict
    if case.get("rerun_trace_id"):
        label = f"{verdict} · rerun"
    cid = html.escape(case.get("id", ""))
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
    if verdict != "pass":
        reason = case.get("reason")
        if reason:
            body_parts.append(_field("reason", reason))
        body_parts.extend(
            _field(k, detail[k]) for k in ("expected", "observed", "judgment") if k in detail
        )
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
        counts[c.get("verdict", "inconclusive")] = (
            counts.get(c.get("verdict", "inconclusive"), 0) + 1
        )
        for key in ("latency_ms", "rerun_latency_ms", "followup_latency_ms"):
            ms = c.get(key)
            if ms:
                total_ms += ms
    total = len(cases_out)
    pass_pct = (counts["pass"] / total * 100) if total else 0.0

    cards = "".join(
        f'<div class="card" style="border-left-color:{COLOR[v]};">'
        f'<div class="cn" style="color:{COLOR[v]};">{counts[v]}</div>'
        f'<div class="cl">{v.replace("_", " ")}</div>'
        f"</div>"
        for v in VERDICTS
    )

    bar_segs = []
    for v in ("pass", "needs_review", "fail", "inconclusive"):
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
        + "</div>"
    )

    case_html = "".join(
        render_case(c, cases.get(c.get("id", ""), {}), details.get(c.get("id", ""), {}))
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
    return head + cards_block(cards) + bar + meta + case_html + disabled_html + FOOT


def cards_block(cards: str) -> str:
    return f'<div class="cards">{cards}</div>'


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
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr);
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

    results_path = run_dir / "results.json"
    if not results_path.exists():
        sys.stderr.write(f"results.json not found in {run_dir}\n")
        return 2

    report_md = (run_dir / "report.md").read_text() if (run_dir / "report.md").exists() else ""
    results = json.loads(results_path.read_text())
    cases = load_cases(args.cases)
    details = parse_detail_blocks(report_md)
    timestamp = parse_timestamp(report_md)

    out = args.out or (run_dir / "report.html")
    out.write_text(render(results, cases, details, timestamp, run_dir))
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
