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
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request

from preflight import normalize_opik_url, redact_sensitive_text


def fetch(url: str, timeout: float = 5.0) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        sys.stderr.write("ERROR: refusing an invalid or credential-bearing Opik URL\n")
        raise SystemExit(2)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(redact_sensitive_text(f"ERROR: failed to reach Opik at {url}: {e}") + "\n")
        sys.exit(2)
    except json.JSONDecodeError as e:
        sys.stderr.write(redact_sensitive_text(f"ERROR: non-JSON response from {url}: {e}") + "\n")
        sys.exit(2)


def fetch_all_spans(
    *,
    trace_id: str,
    project: str,
    base_url: str,
    page_size: int = 200,
    max_pages: int = 100,
    expected_count: int | None = None,
    minimum_count: int = 1,
    consistency_retries: int = 4,
) -> list[dict]:
    """Fetch a complete, eventually-consistent span snapshot.

    Opik may expose the trace before its spans are queryable.  A short or empty
    page is therefore not proof that collection is complete when either the
    span endpoint's ``total`` or the trace's ``span_count`` says otherwise.
    """
    base_url = normalize_opik_url(base_url)
    if consistency_retries < 0:
        raise ValueError("consistency_retries must be non-negative")
    if minimum_count < 0:
        raise ValueError("minimum_count must be non-negative")

    last_count = 0
    last_expected = expected_count or 0
    last_signature: str | None = None
    stable_snapshots = 0
    required_stable_snapshots = 3
    for attempt in range(consistency_retries + 1):
        spans: list[dict] = []
        snapshot_complete = False
        declared_total: int | None = None

        for page in range(1, max_pages + 1):
            query = urllib.parse.urlencode(
                {
                    "trace_id": trace_id,
                    "project_name": project,
                    "page": page,
                    "size": page_size,
                }
            )
            payload = fetch(f"{base_url}/api/v1/private/spans?{query}")
            content = payload.get("content") or []
            if not isinstance(content, list):
                raise RuntimeError("Opik span page has non-list content")
            spans.extend(span for span in content if isinstance(span, dict))

            total = payload.get("total")
            if isinstance(total, int) and total >= 0:
                declared_total = total
            required = max(minimum_count, expected_count or 0, declared_total or 0)

            if declared_total is not None and len(spans) >= required:
                snapshot_complete = True
                break
            if len(content) < page_size:
                # The server says this snapshot has no more pages.  It is only
                # complete if it also satisfies every available count signal.
                snapshot_complete = len(spans) >= required
                break
        else:
            raise RuntimeError(
                f"Opik span lookup exceeded {max_pages} pages; refusing partial evidence"
            )

        if snapshot_complete:
            span_ids = [span.get("id") for span in spans]
            if any(not isinstance(span_id, str) or not span_id for span_id in span_ids):
                raise RuntimeError("Opik returned a span without a stable id")
            if len(set(span_ids)) != len(span_ids):
                raise RuntimeError("Opik returned duplicate span ids; refusing ambiguous evidence")
            canonical_spans = sorted(spans, key=lambda span: str(span["id"]))
            signature = hashlib.sha256(
                json.dumps(canonical_spans, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if signature == last_signature:
                stable_snapshots += 1
            else:
                last_signature = signature
                stable_snapshots = 1
            # Count metadata can be self-consistent while the index is still
            # adding spans or populating outputs.  Accept only after the full
            # ID+payload snapshot is unchanged across three observations.
            if stable_snapshots >= required_stable_snapshots:
                return spans
        else:
            last_signature = None
            stable_snapshots = 0

        last_count = len(spans)
        last_expected = max(minimum_count, expected_count or 0, declared_total or 0)
        if attempt < consistency_retries:
            time.sleep(min(0.5 * (2**attempt), 4.0))

    raise RuntimeError(
        "Opik spans remained incomplete or unstable after consistency retries "
        f"(received {last_count}, expected at least {last_expected}, "
        f"stable snapshots {stable_snapshots}/{required_stable_snapshots})"
    )


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


def format_blob(blob: object) -> str:
    """Render nested payloads without discarding fields used for provenance."""
    if isinstance(blob, (dict, list)):
        return json.dumps(blob, indent=2, sort_keys=True, default=str)
    if blob is None:
        return ""
    return str(blob)


def nested_payload(blob: object, key: str) -> object:
    """Unwrap a named payload when present without assuming a mapping shape."""
    if isinstance(blob, dict):
        return blob.get(key, blob)
    return blob


def format_duration_seconds(duration_ms: object) -> str:
    """Render Opik duration defensively; malformed display metadata is not evidence."""
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        return f"{duration_ms / 1000:.1f}s"
    return "unknown"


def render_trace(trace_id: str, project: str, base_url: str, raw: bool) -> None:
    base_url = normalize_opik_url(base_url)
    trace_url = f"{base_url}/api/v1/private/traces/{trace_id}"

    trace = fetch(trace_url)
    trace_span_count = trace.get("span_count")
    spans = fetch_all_spans(
        trace_id=trace_id,
        project=project,
        base_url=base_url,
        expected_count=trace_span_count if isinstance(trace_span_count, int) else None,
    )

    if raw:
        print(json.dumps({"trace": trace, "spans": spans}, indent=2))
        return

    name = trace.get("name", "?")
    start = trace.get("start_time", "?")
    duration_ms = trace.get("duration")
    span_count = trace.get("span_count", len(spans))

    print(f"=== Trace {trace_id} ===")
    print(f"Workflow: {name}")
    print(
        f"Started: {start}   Duration: {format_duration_seconds(duration_ms)}   Spans: {span_count}"
    )
    if trace.get("error_info"):
        print(f"Trace error: {format_blob(trace['error_info'])}")

    # User input (the hub's input message)
    user_text = extract_text(nested_payload(trace.get("input"), "input"))
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

    # Lossless outer-specialist evidence. The summaries above remain useful
    # for scanning, while this section preserves the payloads required for
    # provenance, failure, and handoff checks across every specialist.
    specialist_calls = [
        span
        for span in spans
        if str(span.get("name", "")).endswith("_analysis") or span.get("name") == "screener_lookup"
    ]
    print()
    print(f"--- SPECIALIST CALL EVIDENCE ({len(specialist_calls)}) ---")
    if not specialist_calls:
        print("(none)")
    for i, span in enumerate(specialist_calls, 1):
        span_name = str(span.get("name", "unknown_analysis"))
        print(f"\nCall {i}: {span_name.upper()}")
        print(f"  span_id: {span.get('id', '?')}")
        print(f"  parent_span_id: {span.get('parent_span_id', '?')}")
        print(f"  start_time: {span.get('start_time', '?')}")
        print(f"  end_time: {span.get('end_time', '?')}")
        print(f"  duration: {span.get('duration', '?')}")
        if span.get("error_info"):
            print("  error_info:")
            for line in format_blob(span["error_info"]).splitlines():
                print(f"    {line}")
        print("  input:")
        for line in format_blob(span.get("input")).splitlines() or ["(none)"]:
            print(f"    {line}")
        print("  output:")
        for line in format_blob(span.get("output")).splitlines() or ["(none)"]:
            print(f"    {line}")

    # Hub final output
    hub_out = extract_text(nested_payload(trace.get("output"), "output"))
    print()
    print("--- HUB FINAL OUTPUT ---")
    print(hub_out.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Curated view of a local Opik trace.")
    ap.add_argument("trace_id", help="Opik trace UUID")
    ap.add_argument("--project", default="obai-eval", help="Opik project (default: obai-eval)")
    ap.add_argument("--url", default="http://localhost:5173", help="Opik base URL")
    ap.add_argument("--raw", action="store_true", help="Dump raw trace + spans as JSON")
    args = ap.parse_args()
    try:
        render_trace(args.trace_id, args.project, args.url, args.raw)
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write(f"ERROR: {redact_sensitive_text(exc)}\n")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
