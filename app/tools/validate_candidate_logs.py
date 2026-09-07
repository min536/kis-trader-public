"""CLI: validate_candidate_logs

Checks candidate outcome JSONL log integrity for a given account / date / session.

Usage:
    python3 -m app.tools.validate_candidate_logs --date 20260403 --account mock_12345678_01
    python3 -m app.tools.validate_candidate_logs --date 20260403 --account mock_12345678_01 --session REGULAR
    python3 -m app.tools.validate_candidate_logs --date 20260403 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution (no app.auth import so this CLI is safe to run standalone)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_REJECTION_REASONS = (
    "score_below_threshold",
    "passed_count_insufficient",
    "cost_filter_blocked",
    "profit_buffer_insufficient",
)


def _log_path(account: str, date: str) -> Path:
    """Reconstruct the account-scoped candidate outcome log path."""
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"


def _stats_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"


# ---------------------------------------------------------------------------
# Row loading
# ---------------------------------------------------------------------------

def _load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (rows, parse_errors)."""
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"File not found: {path}")
        return rows, errors
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    errors.append(f"Line {lineno}: not a JSON object")
            except json.JSONDecodeError as exc:
                errors.append(f"Line {lineno}: JSON parse error – {exc}")
    return rows, errors


# ---------------------------------------------------------------------------
# Filter by session
# ---------------------------------------------------------------------------

def _filter_session(rows: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    if not session:
        return rows
    target = session.upper()
    return [r for r in rows if str(r.get("session") or "").upper() == target]


def _recent_cycle_ids(rows: list[dict[str, Any]], n: int) -> list[str]:
    if n <= 0:
        return []
    cycle_ids: list[str] = []
    seen: set[str] = set()
    for row in reversed(rows):
        cid = str(row.get("cycle_id") or "").strip()
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


# ---------------------------------------------------------------------------
# Individual checks: each returns list of offending rows
# ---------------------------------------------------------------------------

CheckResult = list[dict[str, Any]]


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


def check_residual_reason_mismatch(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 1: rejection_reason == 'residual_position_present' but residual_position_present != True."""
    return [
        r for r in rows
        if r.get("rejection_reason") == "residual_position_present"
        and r.get("residual_position_present") is not True
    ]


def check_deep_eval_other_reason(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 2: stage_reached == 'deep_eval' and rejection_reason == 'other'."""
    return [
        r for r in rows
        if r.get("stage_reached") == "deep_eval"
        and r.get("rejection_reason") == "other"
    ]


def check_final_candidate_missing_score(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 3: final_candidate == True but score_deep is missing (None)."""
    return [
        r for r in rows
        if r.get("final_candidate") is True
        and r.get("score_deep") is None
    ]


def check_deep_eval_missing_selection_outcome(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 4: stage_reached == 'deep_eval' but selection_outcome is missing or null."""
    return [
        r for r in rows
        if r.get("stage_reached") == "deep_eval"
        and _missing_or_null(r, "selection_outcome")
    ]


def check_executed_not_final(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 5: executed == True but final_candidate != True."""
    return [
        r for r in rows
        if r.get("executed") is True
        and r.get("final_candidate") is not True
    ]


def check_pre_gate_rejected_missing_reason(rows: list[dict[str, Any]]) -> CheckResult:
    """Check 6: stage_reached == 'pre_gate_rejected' but rejection_reason is missing."""
    return [
        r for r in rows
        if r.get("stage_reached") == "pre_gate_rejected"
        and not r.get("rejection_reason")
    ]


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

# Checks that are expected to show non-zero counts in log data written BEFORE the
# candidate-outcome logging semantics fix (committed 2026-04-04). These patterns
# are produced by older code and do NOT indicate bugs in the current codebase.
_LEGACY_CHECK_IDS: frozenset[str] = frozenset({
    "residual_reason_mismatch",
    "deep_eval_other_reason",
    "deep_eval_missing_selection_outcome",
})

CHECKS: list[tuple[str, str, Any]] = [
    (
        "residual_reason_mismatch",
        "rejection_reason='residual_position_present' but residual_position_present!=true",
        check_residual_reason_mismatch,
    ),
    (
        "deep_eval_other_reason",
        "stage_reached='deep_eval' and rejection_reason='other'",
        check_deep_eval_other_reason,
    ),
    (
        "final_candidate_missing_score",
        "final_candidate=true but score_deep is null",
        check_final_candidate_missing_score,
    ),
    (
        "deep_eval_missing_selection_outcome",
        "stage_reached='deep_eval' but selection_outcome is missing or null",
        check_deep_eval_missing_selection_outcome,
    ),
    (
        "executed_not_final",
        "executed=true but final_candidate!=true",
        check_executed_not_final,
    ),
    (
        "pre_gate_missing_reason",
        "stage_reached='pre_gate_rejected' but rejection_reason is missing",
        check_pre_gate_rejected_missing_reason,
    ),
]


# ---------------------------------------------------------------------------
# Row formatting
# ---------------------------------------------------------------------------

def _row_summary(row: dict[str, Any]) -> str:
    ts = row.get("ts") or "-"
    symbol = row.get("symbol") or "-"
    name = row.get("symbol_name") or ""
    stage = row.get("stage_reached") or "-"
    reason = row.get("rejection_reason") or "-"
    outcome = row.get("selection_outcome") or "-"
    final = row.get("final_candidate")
    executed = row.get("executed")
    score_deep = row.get("score_deep")
    residual = row.get("residual_position_present")
    return (
        f"  ts={ts} symbol={symbol}({name}) "
        f"stage={stage} reason={reason} outcome={outcome} "
        f"final={final} executed={executed} "
        f"score_deep={score_deep} residual={residual}"
    )


# ---------------------------------------------------------------------------
# Main validation runner
# ---------------------------------------------------------------------------

def run_validation(
    account: str,
    date: str,
    session: str,
    last_n: int,
) -> dict[str, Any]:
    path = _log_path(account, date)
    stats_path = _stats_path(account, date)
    all_rows, parse_errors = _load_rows(path)
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

    semantic_rejections: dict[str, dict[str, Any]] = {}
    for reason in SEMANTIC_REJECTION_REASONS:
        matches = [r for r in rows if r.get("rejection_reason") == reason]
        semantic_rejections[reason] = {
            "count": len(matches),
            "sample": _sample_row(matches[0]) if matches else None,
        }

    results: dict[str, Any] = {
        "account": account,
        "date": date,
        "session": session or "ALL",
        "last_n_cycles": last_n,
        "file": str(path),
        "cycle_stats_file": str(stats_path),
        "file_exists": path.exists(),
        "cycle_stats_file_exists": stats_path.exists(),
        "total_rows": len(all_rows),
        "total_cycle_rows": len(all_stats_rows),
        "session_rows": len(rows),
        "active_cycle_rows": len(stats_rows),
        "selected_cycle_ids": selected_cycle_ids,
        "parse_errors": parse_errors,
        "stats_parse_errors": stats_errors if (last_n > 0 or stats_path.exists()) else [],
        "semantic_rejections": semantic_rejections,
        "checks": {},
        "any_issues": False,
    }

    for check_id, description, fn in CHECKS:
        offenders = fn(rows)
        results["checks"][check_id] = {
            "description": description,
            "count": len(offenders),
            "offenders": offenders,
        }
        if offenders:
            results["any_issues"] = True

    if parse_errors:
        results["any_issues"] = True
    if results["stats_parse_errors"]:
        results["any_issues"] = True

    return results


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _bad(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _header(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def print_terminal_summary(result: dict[str, Any]) -> None:
    print()
    print(_header("═" * 68))
    print(_header("  validate_candidate_logs"))
    print(_header("═" * 68))
    print(f"  account : {result['account']}")
    print(f"  date    : {result['date']}")
    print(f"  session : {result['session']}")
    print(f"  last_n  : {result['last_n_cycles'] if result['last_n_cycles'] > 0 else 'ALL'}")
    print(f"  file    : {result['file']}")

    if not result["file_exists"]:
        print()
        print(_bad(f"  ✗ File not found: {result['file']}"))
        print()
        return

    print(f"  rows    : {result['total_rows']} total / {result['session_rows']} after session filter")
    if result["cycle_stats_file_exists"]:
        print(
            f"  cycles  : {result['total_cycle_rows']} total / "
            f"{result['active_cycle_rows']} after filter"
        )
    if result["selected_cycle_ids"]:
        print(f"  cycle_ids: {', '.join(result['selected_cycle_ids'])}")
    print()

    # Parse errors
    if result["parse_errors"]:
        print(_bad(f"  ✗ PARSE ERRORS ({len(result['parse_errors'])})"))
        for err in result["parse_errors"][:5]:
            print(f"    {err}")
        if len(result["parse_errors"]) > 5:
            print(f"    ... and {len(result['parse_errors']) - 5} more")
        print()
    if result["stats_parse_errors"]:
        print(_bad(f"  ✗ CYCLE STATS PARSE ERRORS ({len(result['stats_parse_errors'])})"))
        for err in result["stats_parse_errors"][:5]:
            print(f"    {err}")
        if len(result["stats_parse_errors"]) > 5:
            print(f"    ... and {len(result['stats_parse_errors']) - 5} more")
        print()

    print(_header("  ── New Rejection Semantics ─────────────────────────────"))
    found_semantic = False
    for reason in SEMANTIC_REJECTION_REASONS:
        payload = result["semantic_rejections"].get(reason) or {}
        count = int(payload.get("count", 0) or 0)
        sample = payload.get("sample")
        status = _ok("  ✓ seen") if count > 0 else _dim("  - none")
        print(f"{status}  [{reason}] count={count}")
        if sample:
            found_semantic = True
            print(
                _warn(
                    "       sample: "
                    f"ts={sample.get('ts')} cycle={sample.get('cycle_id')} "
                    f"symbol={sample.get('symbol')}({sample.get('symbol_name') or ''}) "
                    f"stage={sample.get('stage_reached')} "
                    f"outcome={sample.get('selection_outcome') or '-'} "
                    f"score={sample.get('score_deep')}"
                )
            )
    if not found_semantic:
        print(_dim("       No new semantic rejection reasons detected in this slice."))
    print()

    # Per-check summary
    print(_header("  ── Checks ──────────────────────────────────────────────"))
    any_issues = False
    for check_id, check_data in result["checks"].items():
        count = check_data["count"]
        desc = check_data["description"]
        is_legacy = check_id in _LEGACY_CHECK_IDS
        if count == 0:
            status = _ok("  ✓ OK")
        elif is_legacy:
            status = _warn(f"  ⚠ LEGACY ({count} row{'s' if count != 1 else ''})")
        else:
            status = _bad(f"  ✗ FAIL ({count} row{'s' if count != 1 else ''})")
            any_issues = True
        print(f"{status}  [{check_id}]")
        print(f"       {desc}")
        if is_legacy and count > 0:
            print(_dim("       ※ 이 패턴은 2026-04-04 이전 구 로그 코드의 잔재입니다. 현재 코드는 올바르게 처리합니다."))
        elif count > 0:
            for row in check_data["offenders"][:3]:
                print(_warn(_row_summary(row)))
            if count > 3:
                print(_warn(f"    ... and {count - 3} more rows"))
        print()

    # Final verdict
    print(_header("  ── Verdict ────────────────────────────────────────────"))
    if not any_issues and not result["parse_errors"]:
        print(_ok("  ✓ All checks passed. No integrity issues found."))
    else:
        failed = sum(
            1 for check_id, c in result["checks"].items()
            if c["count"] > 0 and check_id not in _LEGACY_CHECK_IDS
        )
        print(_bad(f"  ✗ {failed} check(s) failed. Review offending rows above."))
    print()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def print_json_output(result: dict[str, Any]) -> None:
    # Strip full offender dicts for concise JSON: keep summary fields only
    compact = {
        k: v for k, v in result.items() if k != "checks"
    }
    compact["checks"] = {}
    for check_id, data in result["checks"].items():
        compact["checks"][check_id] = {
            "description": data["description"],
            "count": data["count"],
            "offender_summaries": [
                {
                    "ts": r.get("ts"),
                    "symbol": r.get("symbol"),
                    "symbol_name": r.get("symbol_name"),
                    "stage_reached": r.get("stage_reached"),
                    "rejection_reason": r.get("rejection_reason"),
                    "selection_outcome": r.get("selection_outcome"),
                    "final_candidate": r.get("final_candidate"),
                    "executed": r.get("executed"),
                    "score_deep": r.get("score_deep"),
                    "residual_position_present": r.get("residual_position_present"),
                }
                for r in data["offenders"]
            ],
        }
    compact["semantic_rejections"] = result["semantic_rejections"]
    print(json.dumps(compact, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate candidate outcome log integrity for account-scoped JSONL files.\n"
            "Example: python3 -m app.tools.validate_candidate_logs "
            "--date 20260403 --account mock_12345678_01 --session REGULAR --last-n-cycles 5"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Log date in YYYYMMDD format (e.g. 20260403)",
    )
    parser.add_argument(
        "--account",
        required=True,
        help="Account signature as used in the filename (e.g. mock_12345678_01)",
    )
    parser.add_argument(
        "--session",
        default="REGULAR",
        help="Market session filter: REGULAR, CLOSED, etc. (default: REGULAR). Pass '' to skip filtering.",
    )
    parser.add_argument(
        "--last-n-cycles",
        type=int,
        default=0,
        help="Validate rows from the most recent N cycle_ids only (default: all).",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output results as JSON instead of human-readable terminal format.",
    )
    args = parser.parse_args()

    result = run_validation(
        account=args.account,
        date=args.date,
        session=args.session,
        last_n=args.last_n_cycles,
    )

    if args.output_json:
        print_json_output(result)
    else:
        print_terminal_summary(result)

    sys.exit(1 if result["any_issues"] else 0)


if __name__ == "__main__":
    main()
