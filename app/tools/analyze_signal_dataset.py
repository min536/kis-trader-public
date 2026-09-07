"""CLI: analyze_signal_dataset

Reads a flat signal dataset (CSV or JSONL) produced by export_signal_dataset
and prints compact diagnostic summaries:

  1. Bucket comparison     — feature stats per core/rotating/exploration
  2. Outcome comparison    — final_candidate vs deep_eval_rejected feature deltas
  3. Rejection breakdown   — per-reason row counts and feature medians
  4. Time-of-day analysis  — compact counts by market-session bucket
  5. Time x bucket         — finals / rejection reasons by time window
  6. Budget-limited        — trade_budget_limited distribution and quality
  7. Bottleneck summary    — blocked finals / core shortlist / core misses
  8. Core rescue           — rescued-core rows and funnel progress

All work is offline — no API calls, no live code paths touched.

Usage:
    # auto-locate from date + account (looks for logs/signal_dataset_<account>_<date>.csv)
    python3 -m app.tools.analyze_signal_dataset --date 20260407 --account mock_12345678_01

    # explicit file path
    python3 -m app.tools.analyze_signal_dataset --file logs/signal_dataset_mock_12345678_01_20260407.csv

    # JSONL input
    python3 -m app.tools.analyze_signal_dataset --file /tmp/signals.jsonl

    # focus on one bucket
    python3 -m app.tools.analyze_signal_dataset --date 20260407 --account mock_12345678_01 --bucket core
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from app.tools.signal_dataset_sections import (
    _W,
    _h, _ok, _warn, _bad, _dim,
    _fval, _bool_val, _fvals,
    _median, _mean, _pct, _fmt, _delta_fmt,
    _minute_of_day, _time_bucket_label,
    _bucket_order, _bucket_code, _bucket_count_text,
    _short_bucket_counts, _cycle_shortlist_cutoffs,
    _section_bucket, _section_outcome, _section_rejection,
    _section_time_of_day, _section_time_bucket_crosstab,
    _section_trade_budget_limited, _section_bottleneck_summary,
    _section_core_rescue, _section_core_shadow,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    rows: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except json.JSONDecodeError:
                    pass
    else:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    return rows


def _locate(account: str, date: str) -> Path:
    p = _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.csv"
    if p.exists():
        return p
    p2 = _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.jsonl"
    if p2.exists():
        return p2
    return p  # let _load report missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print compact diagnostic summaries from an exported signal dataset."
    )
    parser.add_argument("--file",    default="", help="Path to signal dataset CSV or JSONL.")
    parser.add_argument("--date",    default="", help="Date YYYYMMDD (used to locate default file).")
    parser.add_argument("--account", default="", help="Account identifier.")
    parser.add_argument("--bucket",  default="", help="Filter to a single bucket.")
    parser.add_argument("--session", default="", help="Filter by session (e.g. REGULAR).")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
    elif args.date and args.account:
        path = _locate(args.account, args.date)
    else:
        parser.error("Provide either --file or both --date and --account.")

    rows = _load(path)
    if not rows:
        # P0-c: an empty-but-valid dataset (e.g. lane-mode with no candidate
        # outcomes) is not a failure — report and exit 0.
        print(f"No rows loaded from {path.name} — empty dataset, nothing to analyze.", file=sys.stderr)
        return

    # filters
    if args.session:
        rows = [r for r in rows if str(r.get("session") or "").upper() == args.session.upper()]
    if args.bucket:
        rows = [r for r in rows if r.get("selection_bucket") == args.bucket]

    print()
    print(_h("═" * _W))
    print(_h("  analyze_signal_dataset"))
    print(_h("═" * _W))
    print(f"  file    : {path.name}")
    print(f"  rows    : {len(rows)}")
    filter_info = " | ".join(
        p for p in [
            f"session={args.session}" if args.session else "",
            f"bucket={args.bucket}"   if args.bucket  else "",
        ] if p
    )
    if filter_info:
        print(f"  filters : {filter_info}")
    print()

    _section_bucket(rows)
    _section_outcome(rows)
    _section_rejection(rows)
    _section_time_of_day(rows)
    _section_time_bucket_crosstab(rows)
    _section_trade_budget_limited(rows)
    _section_bottleneck_summary(rows)
    _section_core_rescue(rows)
    _section_core_shadow(rows)

    print(_h("═" * _W))
    print()


if __name__ == "__main__":
    main()
