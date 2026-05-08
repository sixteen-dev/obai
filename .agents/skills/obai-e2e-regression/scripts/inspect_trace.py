#!/usr/bin/env python3
"""Curated view of a local Opik trace.

Fetches a trace + its spans from the local Opik server and prints
the parts that matter for debugging the OBaI hub: the user input,
load_skill calls, every strategy_analysis call (its input plus
the operators and verdict from its output), and the hub's final
text output.

Usage:
    python inspect_trace.py <trace_id> [--project NAME] [--url URL] [--raw]

Defaults:
    --project obai-eval
    --url     http://localhost:5173
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request


def fetch(url: str, timeout: float = 5.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        sys.stderr.write(f"ERROR: failed to reach Opik at {url}: {e}\n")
        sys.exit(2)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: non-JSON response from {url}: {e}\n")
        sys.exit(2)


def extract_text(blob: object) -> str:
    """Extract plain text from Opik's nested input/output structures."""
    if isinstance(blob, str):
        return blob
    if isinstance(blob, list):
        return "\n".join(extract_text(item) for item in blob)
    if isinstance(blob, dict):
        if "text" in blob and isinstance(blob["text"], str):
            return blob["text"]
        if "content" in blob:
            return extract_text(blob["content"])
        if "input" in blob:
            return extract_text(blob["input"])
        if "output" in blob:
            return extract_text(blob["output"])
        return json.dumps(blob)
    return ""


def parse_strategy_input(raw_input: object) -> str:
    """The hub-to-strategy payload is a JSON-in-JSON string. Unwrap it."""
    text = extract_text(raw_input)
    try:
        wrapped = json.loads(text)
        if isinstance(wrapped, dict) and "input" in wrapped:
            return wrapped["input"]
    except json.JSONDecodeError:
        pass
    return text


def find_operators(text: str) -> list[str]:
    return re.findall(r'"operator"\s*:\s*"([^"]+)"', text)


def find_verdict(text: str) -> str | None:
    m = re.search(r"#### 1\.\s*Verdict\s*\n\s*-?\s*`?(\w+)", text)
    return m.group(1) if m else None


def find_trade_count(text: str) -> str | None:
    m = re.search(r"Total trades\s*\|?\s*(\d+)\s*\|\s*(\d+)", text)
    if m:
        return f"{m.group(1)} train | {m.group(2)} full"
    m = re.search(r"Total trades[^\d]*(\d+)", text)
    return m.group(1) if m else None


def truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[:n].rstrip() + f"\n... [truncated, full length {len(text)}]"


def render_trace(trace_id: str, project: str, base_url: str, raw: bool) -> None:
    trace_url = f"{base_url}/api/v1/private/traces/{trace_id}"
    spans_url = f"{base_url}/api/v1/private/spans?trace_id={trace_id}&project_name={project}&size=200"

    trace = fetch(trace_url)
    spans_resp = fetch(spans_url)
    spans = spans_resp.get("content", [])

    if raw:
        print(json.dumps({"trace": trace, "spans": spans}, indent=2))
        return

    name = trace.get("name", "?")
    start = trace.get("start_time", "?")
    duration_ms = trace.get("duration", 0)
    span_count = trace.get("span_count", len(spans))

    print(f"=== Trace {trace_id} ===")
    print(f"Workflow: {name}")
    print(f"Started: {start}   Duration: {duration_ms / 1000:.1f}s   Spans: {span_count}")

    # User input (the hub's input message)
    user_text = extract_text(trace.get("input", {}).get("input", trace.get("input", "")))
    print()
    print("--- USER QUERY ---")
    print(truncate(user_text.strip(), 1500))

    # load_skill calls
    skill_loads = [s for s in spans if s.get("name") == "load_skill"]
    print()
    print(f"--- SKILL LOADS ({len(skill_loads)}) ---")
    if not skill_loads:
        print("(none)")
    for s in skill_loads:
        inp = extract_text(s.get("input", {}))
        out = extract_text(s.get("output", {}))
        skill_name_match = re.search(r'"skill_name"\s*:\s*"([^"]+)"', inp)
        status_match = re.search(r'"status"\s*:\s*"([^"]+)"', out)
        skill_name = skill_name_match.group(1) if skill_name_match else "?"
        status = status_match.group(1) if status_match else "?"
        print(f"- {skill_name}: {status}")

    # strategy_analysis calls
    strategy_calls = [s for s in spans if s.get("name") == "strategy_analysis"]
    print()
    print(f"--- STRATEGY_ANALYSIS CALLS ({len(strategy_calls)}) ---")
    if not strategy_calls:
        print("(none)")
    for i, s in enumerate(strategy_calls, 1):
        print(f"\nCall {i}:")
        hub_input = parse_strategy_input(s.get("input", {}))
        print("  Hub-to-strategy input:")
        for line in truncate(hub_input.strip(), 1200).split("\n"):
            print(f"    {line}")
        out_text = extract_text(s.get("output", {}))
        ops = find_operators(out_text)
        verdict = find_verdict(out_text)
        trades = find_trade_count(out_text)
        if ops:
            print(f"  Operators in final JSON: {ops}")
        if verdict:
            print(f"  Verdict: {verdict}")
        if trades:
            print(f"  Total trades: {trades}")

    # prediction_market_analysis calls (mirror)
    pred_calls = [s for s in spans if s.get("name") == "prediction_market_analysis"]
    if pred_calls:
        print()
        print(f"--- PREDICTION_MARKET_ANALYSIS CALLS ({len(pred_calls)}) ---")
        for i, s in enumerate(pred_calls, 1):
            print(f"\nCall {i}:")
            hub_input = parse_strategy_input(s.get("input", {}))
            print("  Hub-to-prediction input:")
            for line in truncate(hub_input.strip(), 800).split("\n"):
                print(f"    {line}")

    # Hub final output
    hub_out = extract_text(trace.get("output", {}).get("output", trace.get("output", "")))
    print()
    print("--- HUB FINAL OUTPUT ---")
    print(truncate(hub_out.strip(), 2000))


def main() -> None:
    ap = argparse.ArgumentParser(description="Curated view of a local Opik trace.")
    ap.add_argument("trace_id", help="Opik trace UUID")
    ap.add_argument("--project", default="obai-eval", help="Opik project (default: obai-eval)")
    ap.add_argument("--url", default="http://localhost:5173", help="Opik base URL")
    ap.add_argument("--raw", action="store_true", help="Dump raw trace + spans as JSON")
    args = ap.parse_args()
    render_trace(args.trace_id, args.project, args.url, args.raw)


if __name__ == "__main__":
    main()
