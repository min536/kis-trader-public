"""CLI: analyze_candidate_logs

A comprehensive daily report tool for REGULAR-session trading analysis
based on candidate outcome logs.

Usage:
    python3 -m app.tools.analyze_candidate_logs --date 20260403 --account mock_12345678_01
    python3 -m app.tools.analyze_candidate_logs --date 20260403 --account mock_12345678_01 --session REGULAR
    python3 -m app.tools.analyze_candidate_logs --date 20260403 --account mock_12345678_01 --bucket core --last-n-cycles 50
    python3 -m app.tools.analyze_candidate_logs --date 20260403 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

STAGE_ORDER = (
    "pre_gate_rejected",
    "shallow_ranked",
    "shallow_selected",
    "deep_eval",
    "final_candidate",
    "executed",
)

KNOWN_BUCKETS = ("core", "rotating", "exploration")
SEMANTIC_REJECTION_REASONS = (
    "score_below_threshold",
    "passed_count_insufficient",
    "cost_filter_blocked",
    "profit_buffer_insufficient",
)

# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

def _c(color: str, text: str) -> str: return f"{color}{text}{_RESET}"
def _header(text: str) -> str: return _c(_BOLD + _CYAN, text)
def _subheader(text: str) -> str: return _c(_BOLD, text)
def _ok(text: str) -> str: return _c(_GREEN, text)
def _warn(text: str) -> str: return _c(_YELLOW, text)
def _bad(text: str) -> str: return _c(_RED, text)
def _dim(text: str) -> str: return _c(_DIM, text)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _log_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"

def _stats_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"

def _load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"File not found: {path}")
        return rows, errors
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw: continue
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict): rows.append(obj)
            except json.JSONDecodeError as exc:
                errors.append(f"Line {lineno}: JSON parse error – {exc}")
    return rows, errors

def _filter_session(rows: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    if not session: return rows
    target = session.upper()
    return [r for r in rows if str(r.get("session") or "").upper() == target]

def _filter_bucket(rows: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    if not bucket: return rows
    return [r for r in rows if r.get("selection_bucket") == bucket]

def _filter_last_n_cycles(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if n <= 0: return rows
    cycle_ids = []
    seen = set()
    for r in reversed(rows):
        cid = r.get("cycle_id")
        if cid and cid not in seen:
            seen.add(cid)
            cycle_ids.append(cid)
            if len(cycle_ids) == n: break
    keep_cids = set(cycle_ids)
    return [r for r in rows if r.get("cycle_id") in keep_cids]

def _recent_cycle_ids(rows: list[dict[str, Any]], n: int) -> list[str]:
    if n <= 0:
        return []
    cycle_ids: list[str] = []
    seen: set[str] = set()
    for r in reversed(rows):
        cid = str(r.get("cycle_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        cycle_ids.append(cid)
        if len(cycle_ids) == n:
            break
    cycle_ids.reverse()
    return cycle_ids

def _filter_cycle_ids(rows: list[dict[str, Any]], cycle_ids: set[str]) -> list[dict[str, Any]]:
    if not cycle_ids:
        return rows
    return [r for r in rows if str(r.get("cycle_id") or "").strip() in cycle_ids]


def _missing_or_null(row: dict[str, Any], key: str) -> bool:
    return key not in row or row.get(key) is None


def _sample_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": row.get("ts"),
        "cycle_id": row.get("cycle_id"),
        "symbol": row.get("symbol"),
        "symbol_name": row.get("symbol_name"),
        "stage_reached": row.get("stage_reached"),
        "rejection_reason": row.get("rejection_reason"),
        "selection_outcome": row.get("selection_outcome"),
        "score_deep": row.get("score_deep"),
        "selection_bucket": row.get("selection_bucket"),
    }


def _count_flag(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key) is True)

# ---------------------------------------------------------------------------
# Analysis logic
# ---------------------------------------------------------------------------
def _pct(part: int, total: int) -> str:
    if total == 0: return "0.0%"
    return f"{100.0 * part / total:.1f}%"

def _bar(value: int, total: int, width: int = 20) -> str:
    if total == 0: return " " * width
    filled = max(1, round(width * value / total)) if value > 0 else 0
    return "█" * filled + "░" * (width - filled)

def run_analysis(
    account: str, date: str, session: str, bucket: str, last_n: int
) -> dict[str, Any]:
    path = _log_path(account, date)
    stats_path = _stats_path(account, date)
    all_rows, errors = _load_rows(path)
    all_stats_rows, stats_errors = _load_rows(stats_path)
    rows = _filter_session(all_rows, session)
    stats_rows = _filter_session(all_stats_rows, session)
    selected_cycle_ids: list[str] = []
    if last_n > 0:
        selected_cycle_ids = _recent_cycle_ids(stats_rows, last_n)
        if not selected_cycle_ids:
            selected_cycle_ids = _recent_cycle_ids(rows, last_n)
        keep_cycle_ids = set(selected_cycle_ids)
        rows = _filter_cycle_ids(rows, keep_cycle_ids)
        stats_rows = _filter_cycle_ids(stats_rows, keep_cycle_ids)
    all_errors = list(errors)
    if last_n > 0 or stats_path.exists():
        all_errors.extend(stats_errors)
    
    active_rows = _filter_bucket(rows, bucket)

    # 1. Funnel summary
    total = len(active_rows)
    stages = Counter(r.get("stage_reached") for r in active_rows)
    final_candidate_count = _count_flag(active_rows, "final_candidate")
    executed_count = _count_flag(active_rows, "executed")
    
    # 2. Distributions
    pre_gate_reasons = Counter(r.get("rejection_reason") for r in active_rows if r.get("stage_reached") == "pre_gate_rejected")
    # Only count rows that actually have a rejection reason (None means no rejection occurred)
    rejection_reasons = Counter(
        r.get("rejection_reason") for r in active_rows if r.get("rejection_reason") is not None
    )
    selection_outcomes = Counter(r.get("selection_outcome") for r in active_rows if r.get("selection_outcome"))
    semantic_rejections: dict[str, dict[str, Any]] = {}
    for reason in SEMANTIC_REJECTION_REASONS:
        matches = [r for r in active_rows if r.get("rejection_reason") == reason]
        semantic_rejections[reason] = {
            "count": len(matches),
            "sample": _sample_row(matches[0]) if matches else None,
        }

    # 3. Bucket level summary (across the rows pre-bucket filter, if we want to show all buckets context)
    bucket_summary = {}
    for b in KNOWN_BUCKETS:
        b_rows = _filter_bucket(rows, b)
        bs = Counter(r.get("stage_reached") for r in b_rows)
        score_deep_vals = [r.get("score_deep") for r in b_rows if r.get("score_deep") is not None]
        avg_score = sum(score_deep_vals)/len(score_deep_vals) if score_deep_vals else 0
        bucket_summary[b] = {
            "total": len(b_rows),
            "stages": dict(bs),
            "final_candidate_count": _count_flag(b_rows, "final_candidate"),
            "executed_count": _count_flag(b_rows, "executed"),
            "avg_deep_score": avg_score
        }

    # 4. Bad patterns (residual mismatch, missing reasons, deep=other, etc)
    bad_patterns = []
    
    rc1 = len([r for r in active_rows if r.get("rejection_reason") == "residual_position_present" and not r.get("residual_position_present")])
    if rc1 > 0: bad_patterns.append({"name": "residual_reason_mismatch", "count": rc1})

    rc2 = len([r for r in active_rows if r.get("stage_reached") == "deep_eval" and r.get("rejection_reason") == "other"])
    if rc2 > 0: bad_patterns.append({"name": "deep_eval_other_reason", "count": rc2})

    rc3 = len([r for r in active_rows if r.get("stage_reached") == "pre_gate_rejected" and not r.get("rejection_reason")])
    if rc3 > 0: bad_patterns.append({"name": "pre_gate_missing_reason", "count": rc3})

    rc4 = len([r for r in active_rows if r.get("final_candidate") and r.get("score_deep") is None])
    if rc4 > 0: bad_patterns.append({"name": "final_candidate_missing_score", "count": rc4})

    rc5 = len(
        [
            r for r in active_rows
            if r.get("stage_reached") == "deep_eval"
            and _missing_or_null(r, "selection_outcome")
        ]
    )
    if rc5 > 0:
        bad_patterns.append({"name": "deep_eval_missing_selection_outcome", "count": rc5})

    rc6 = len(
        [
            r for r in active_rows
            if r.get("deep_evaluated") is True
            and r.get("final_candidate") is not True
            and r.get("rejection_reason") is None
        ]
    )
    if rc6 > 0:
        bad_patterns.append({"name": "deep_eval_loser_missing_rejection_reason", "count": rc6})


    return {
        "meta": {
            "account": account,
            "date": date,
            "session": session or "ALL",
            "bucket_filter": bucket or "ALL",
            "last_n_cycles": last_n,
            "file": str(path),
            "cycle_stats_file": str(stats_path),
            "file_exists": path.exists(),
            "cycle_stats_file_exists": stats_path.exists(),
            "total_rows": len(all_rows),
            "total_cycle_rows": len(all_stats_rows),
            "active_rows": total,
            "active_cycle_rows": len(stats_rows),
            "selected_cycle_ids": selected_cycle_ids,
            "errors": all_errors,
        },
        "funnel": {
            "total": total,
            "stages": dict(stages),
            "final_candidate_count": final_candidate_count,
            "executed_count": executed_count,
        },
        "distributions": {
            "pre_gate_reasons": dict(pre_gate_reasons),
            "rejection_reasons": dict(rejection_reasons),
            "selection_outcomes": dict(selection_outcomes)
        },
        "semantic_rejections": semantic_rejections,
        "bucket_summary": bucket_summary,
        "bad_patterns": bad_patterns
    }

# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------
_W = 72
def _sep() -> str: return _header("─" * _W)

def print_terminal_report(result: dict[str, Any]) -> None:
    m = result["meta"]
    print()
    print(_header("═" * _W))
    print(_header("  analyze_candidate_logs (Daily Report)"))
    print(_header("═" * _W))
    print(f"  account : {m['account']}")
    print(f"  date    : {m['date']}")
    print(f"  session : {m['session']}")
    print(f"  bucket  : {m['bucket_filter']}")
    print(f"  last_n  : {m['last_n_cycles'] if m['last_n_cycles'] > 0 else 'ALL'}")
    print(f"  rows    : {m['total_rows']} total -> {m['active_rows']} matching filter")
    if m["cycle_stats_file_exists"]:
        print(f"  cycles  : {m['total_cycle_rows']} total -> {m['active_cycle_rows']} matching filter")
    if m["selected_cycle_ids"]:
        joined_cycle_ids = ", ".join(m["selected_cycle_ids"])
        print(f"  cycle_ids: {joined_cycle_ids}")
    if not m["file_exists"]:
        print(_bad("\n  ✗ File not found."))
        return
    print()

    # 1. Funnel
    f = result["funnel"]
    print(_sep())
    print(_header("  § 1  FUNNEL SUMMARY"))
    print(_sep())
    tot = f["total"]
    if tot == 0:
         print(_warn("  No data."))
    else:
         for s in STAGE_ORDER:
             if s == "final_candidate":
                 cnt = int(f.get("final_candidate_count", f["stages"].get(s, 0)) or 0)
             elif s == "executed":
                 cnt = int(f.get("executed_count", f["stages"].get(s, 0)) or 0)
             else:
                 cnt = f["stages"].get(s, 0)
             print(f"  {s:<20} {cnt:>6}  {_pct(cnt, tot):>7}  {_bar(cnt, tot)}")
    print()

    # 2. Distributions
    d = result["distributions"]
    print(_sep())
    print(_header("  § 2  DISTRIBUTIONS (Pre-gate / Rejection / Outcome)"))
    print(_sep())
    
    print(_subheader("  Pre-gate Rejections:"))
    if not d["pre_gate_reasons"]: print(_dim("    None"))
    for rsn, cnt in sorted(d["pre_gate_reasons"].items(), key=lambda x: -x[1]):
        print(f"    {str(rsn):<40} {cnt:>5}")
    
    print(_subheader("\n  All Rejection Reasons:"))
    if not d["rejection_reasons"]: print(_dim("    None"))
    for rsn, cnt in sorted(d["rejection_reasons"].items(), key=lambda x: -x[1]):
        print(f"    {str(rsn):<40} {cnt:>5}")

    print(_subheader("\n  Selection Outcomes:"))
    if not d["selection_outcomes"]: print(_dim("    None"))
    for out, cnt in sorted(d["selection_outcomes"].items(), key=lambda x: -x[1]):
        print(f"    {str(out):<40} {cnt:>5}")
    print()

    # 3. Semantic rejection summary
    sr = result["semantic_rejections"]
    print(_sep())
    print(_header("  § 3  NEW REJECTION SEMANTICS"))
    print(_sep())
    found_semantic_rejections = False
    for reason in SEMANTIC_REJECTION_REASONS:
        payload = sr.get(reason) or {}
        count = int(payload.get("count", 0) or 0)
        sample = payload.get("sample")
        status = _ok("seen") if count > 0 else _dim("none")
        print(f"  {reason:<32} {count:>5}  {status}")
        if sample:
            found_semantic_rejections = True
            print(
                _dim(
                    "    sample: "
                    f"{sample.get('symbol')}({sample.get('symbol_name') or ''}) | "
                    f"cycle={sample.get('cycle_id')} | "
                    f"stage={sample.get('stage_reached')} | "
                    f"outcome={sample.get('selection_outcome') or '-'} | "
                    f"score={sample.get('score_deep')}"
                )
            )
    if not found_semantic_rejections and not any(
        int((sr.get(reason) or {}).get("count", 0) or 0) > 0
        for reason in SEMANTIC_REJECTION_REASONS
    ):
        print(_dim("  No newly-emitted semantic rejection reasons detected in this slice."))
    print()

    # 4. Bucket summary
    bs = result["bucket_summary"]
    print(_sep())
    print(_header("  § 4  BUCKET-LEVEL SUMMARY (Across Session)"))
    print(_sep())
    print(f"  {'bucket':<12} {'total':>6} {'pre_rej':>8} {'deep':>6} {'final':>6} {'exec':>6}  {'avg_score':>9}")
    print(_dim("  " + "─" * 68))
    for b in KNOWN_BUCKETS:
        data = bs.get(b, {})
        btot = data.get("total", 0)
        stg = data.get("stages", {})
        pre = stg.get("pre_gate_rejected", 0)
        deep = stg.get("deep_eval", 0)
        fin = int(data.get("final_candidate_count", stg.get("final_candidate", 0)) or 0)
        exe = int(data.get("executed_count", stg.get("executed", 0)) or 0)
        avg = data.get("avg_deep_score", 0.0)
        print(f"  {b:<12} {btot:>6} {pre:>8} {deep:>6} {fin:>6} {exe:>6}  {avg:>9.2f}")
    print()

    # 5. Bad patterns
    bp = result["bad_patterns"]
    print(_sep())
    print(_header("  § 5  BAD PATTERNS DETECTED"))
    print(_sep())
    if not bp:
         print(_ok("  ✓ No bad patterns detected."))
    else:
         for p in bp:
              print(_bad(f"  ✗ {p['name']:<30} {p['count']} occurrences"))
    print()
    print(_header("═" * _W))
    print()


def print_json_report(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False, default=lambda x: str(x) if math.isnan(x) else x))

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comprehensive Daily Report CLI for Candidate Logs"
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--last-n-cycles", type=int, default=0, help="Analyze last N cycles only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_analysis(
        account=args.account,
        date=args.date,
        session=args.session,
        bucket=args.bucket,
        last_n=args.last_n_cycles
    )

    if args.json:
        print_json_report(result)
    else:
        print_terminal_report(result)
    sys.exit(0)

if __name__ == "__main__":
    main()
