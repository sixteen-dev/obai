"""Re-evaluate engine_fit + approximation_notes per entry based on signal_inputs.

The original generator blanket-labeled every OpenAP entry `approximate`. This
script applies a per-entry rule grounded in the entry's own `signal_inputs`:
fundamentals/analyst/event/holdings/options data → `reference_only` (engine
does not ingest those data types); price-only / trading-only signals stay
`approximate` (engine runs per-symbol; cross-sectional ranking needs proxy).

Run with --dry-run to preview changes without writing.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_index import parse_markdown  # noqa: E402

REF_ONLY_NOTE_BY_DATA = {
    "accounting": (
        "Signal requires firm-level accounting data (balance sheet, income statement, "
        "cash-flow items) that the OBaI backtest engine does not ingest. The engine "
        "consumes OHLCV bars on daily/intraday timeframes only. Use as routing "
        "reference; do not attempt backtest execution."
    ),
    "analyst": (
        "Signal requires sell-side analyst data (consensus estimates, recommendation "
        "changes, target prices, IBES-style fields) that the OBaI backtest engine "
        "does not ingest. Use as routing reference; do not attempt backtest execution."
    ),
    "event": (
        "Signal requires corporate-event data (earnings announcement dates, IPOs, "
        "spinoffs, mergers) and event-window logic that the OBaI backtest engine does "
        "not support natively. Use as routing reference; do not attempt backtest execution."
    ),
    "13f": (
        "Signal requires institutional-holdings (13F) data that the OBaI backtest engine "
        "does not ingest. Use as routing reference; do not attempt backtest execution."
    ),
    "options": (
        "Signal requires options-chain data (implied volatility, open interest, put-call "
        "ratios) and the OBaI backtest engine has no options-chain integration. Use as "
        "routing reference; do not attempt backtest execution."
    ),
    "other": (
        "Signal requires specialized data inputs (short interest, lending fees, or other "
        "alternative datasets) that the OBaI backtest engine does not ingest. Use as "
        "routing reference; do not attempt backtest execution."
    ),
}

APPROXIMATE_NOTE_PRICE = (
    "Signal is computed cross-sectionally across a broad equity universe and ranked "
    "into portfolios. The OBaI backtest engine supports per-symbol technical signals "
    "on OHLCV bars but not native cross-sectional ranking with universe rebalance. To "
    "approximate, the hub may pre-select a fixed universe subset and apply the signal "
    "per-symbol — directionally informative, not a faithful OpenAP replication."
)

APPROXIMATE_NOTE_TRADING = (
    "Signal uses price and volume cross-sectionally. The OBaI engine has per-symbol "
    "volume indicators but does not natively rank a universe. Hub-provided fixed-universe "
    "pre-selection plus per-symbol rules can approximate the long-short spread; do not "
    "treat the result as a verbatim OpenAP replication."
)

# signal_inputs keyword (lowercased) -> (new_engine_fit, note_key_or_text)
RULES: list[tuple[str, str, str]] = [
    ("openap accounting data", "reference_only", "accounting"),
    ("openap analyst data",    "reference_only", "analyst"),
    ("openap event data",      "reference_only", "event"),
    ("openap 13f data",        "reference_only", "13f"),
    ("openap options data",    "reference_only", "options"),
    ("openap other data",      "reference_only", "other"),
    ("openap trading data",    "approximate",    "trading"),
    ("openap price data",      "approximate",    "price"),
]


def classify(signal_inputs: Iterable[str]) -> tuple[str, str] | None:
    """Return (new_engine_fit, new_approximation_notes) from signal_inputs, or None."""
    haystack = " | ".join(s.lower() for s in signal_inputs)
    for keyword, fit, note_key in RULES:
        if keyword in haystack:
            if fit == "reference_only":
                return fit, REF_ONLY_NOTE_BY_DATA[note_key]
            if note_key == "price":
                return fit, APPROXIMATE_NOTE_PRICE
            if note_key == "trading":
                return fit, APPROXIMATE_NOTE_TRADING
    return None


# Regex matches the engine_fit line and the approximation_notes block (folded scalar)
# Starts at `engine_fit:` line, ends at the line before `signal_inputs:`.
BLOCK_RE = re.compile(
    r"^engine_fit:[^\n]*\napproximation_notes:[^\n]*(?:\n[ \t]+[^\n]*)*\n(?=signal_inputs:)",
    re.MULTILINE,
)


def rewrite_block(content: str, engine_fit: str, approximation_notes: str) -> str:
    """Replace the engine_fit + approximation_notes lines while keeping YAML valid."""
    # Use yaml.dump for the notes so any quoting / wrapping is correct
    notes_yaml = yaml.dump({"approximation_notes": approximation_notes},
                            default_flow_style=False, width=78, sort_keys=False)
    new_block = f"engine_fit: {engine_fit}\n{notes_yaml}"
    new_content, n = BLOCK_RE.subn(new_block, content, count=1)
    if n != 1:
        raise RuntimeError("did not find exactly one engine_fit/approximation_notes block to replace")
    return new_content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to disk. Without it, preview-only.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N entries (useful for sampling).")
    args = parser.parse_args()

    public_dir = ROOT / "corpus" / "strategies"
    counts_before: Counter[str] = Counter()
    counts_after: Counter[str] = Counter()
    sample_changes: list[tuple[str, str, str, str]] = []
    n_changed = 0
    n_total = 0

    for path in sorted(public_dir.rglob("openap_*.md")):
        if args.limit and n_total >= args.limit:
            break
        n_total += 1
        fm, _, _ = parse_markdown(path)
        old_fit = str(fm.get("engine_fit", ""))
        counts_before[old_fit] += 1
        result = classify(fm.get("signal_inputs") or [])
        if result is None:
            counts_after[old_fit] += 1
            continue
        new_fit, new_notes = result
        if new_fit == old_fit:
            counts_after[old_fit] += 1
            continue
        counts_after[new_fit] += 1
        n_changed += 1
        if len(sample_changes) < 5:
            sample_changes.append((fm["id"], old_fit, new_fit, new_notes[:80] + "..."))
        if args.apply:
            text = path.read_text(encoding="utf-8")
            new_text = rewrite_block(text, new_fit, new_notes)
            path.write_text(new_text, encoding="utf-8")

    print(f"Audited {n_total} entries; {n_changed} would change engine_fit")
    print()
    print(f"{'engine_fit':<20} {'before':>8} {'after':>8}")
    for fit in sorted(set(counts_before) | set(counts_after)):
        print(f"{fit:<20} {counts_before.get(fit, 0):>8} {counts_after.get(fit, 0):>8}")
    print()
    print("Sample changes:")
    for eid, old, new, note in sample_changes:
        print(f"  {eid:<35} {old} -> {new}")
        print(f"    note: {note}")

    if not args.apply:
        print()
        print("(dry run; re-run with --apply to write changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
