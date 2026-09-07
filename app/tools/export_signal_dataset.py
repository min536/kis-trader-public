"""CLI: export_signal_dataset

Exports a flat, analysis-ready signal dataset from candidate outcome logs,
enriched with score_components (trend_alignment_score, macd_momentum_score,
pullback_pct, etc.) joined from cycle snapshots.

All work is offline — no API calls, no live code paths touched.

Usage:
    python3 -m app.tools.export_signal_dataset \\
        --date 20260407 --account mock_12345678_01

    python3 -m app.tools.export_signal_dataset \\
        --date 20260407 --account mock_12345678_01 \\
        --session REGULAR --deep-eval-only

    python3 -m app.tools.export_signal_dataset \\
        --date 20260407 --account mock_12345678_01 \\
        --format jsonl --output /tmp/signals.jsonl

    python3 -m app.tools.export_signal_dataset \\
        --date 20260407 --account mock_12345678_01 \\
        --format parquet --output /tmp/signals.parquet

    # include pre_gate and shallow stages too
    python3 -m app.tools.export_signal_dataset \\
        --date 20260407 --account mock_12345678_01 \\
        --all-stages

Note: default behaviour is --deep-eval-only (equivalent to not passing --all-stages).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from app.tools.parquet_utils import read_flat_parquet, write_flat_parquet

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Column spec — order determines CSV column order
# ---------------------------------------------------------------------------

# Taken directly from candidate_outcome rows
_OUTCOME_COLS: tuple[str, ...] = (
    "ts",
    "cycle_id",
    "account_signature",
    "symbol",
    "symbol_name",
    "session",
    "regime",
    "selection_bucket",
    "selection_profile",
    "stage_reached",
    "pre_gate_passed",
    "shallow_selected",
    "deep_evaluated",
    "score_shallow",
    "score_deep",
    "passed_count_deep",
    "strategy_pass_pattern",
    "passes_profit_buffer",
    "rejection_reason",
    "selection_outcome",
    "final_candidate",
    "buy_signal",
    "executed",
    "buy_signal_executed",
    "daily_pnl_brake_state",
    "already_holding",
    "cooldown_blocked",
)

# Sourced from score_components in cycle snapshots (joined on cycle_id + symbol)
_SCORE_COMPONENT_COLS: tuple[str, ...] = (
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "pullback_pct",
    "rebound_pct",
    "gap_up_open_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
)

# Core shadow fields — may not exist in older logs; handled gracefully
_CORE_SHADOW_COLS: tuple[str, ...] = (
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed_count",
    "core_shadow_pattern",
    "core_shadow_passed",
)

_CORE_RESCUE_COLS: tuple[str, ...] = (
    "core_rescue_applied",
    "core_rescue_selected_symbol",
    "core_rescue_selected_score",
    "core_rescue_replaced_symbol",
    "core_rescue_reason",
)

ALL_COLS: tuple[str, ...] = (
    _OUTCOME_COLS
    + _SCORE_COMPONENT_COLS
    + _CORE_RESCUE_COLS
    + _CORE_SHADOW_COLS
)

_SENTINEL = object()

_BOOL_COLS: set[str] = {
    "pre_gate_passed",
    "shallow_selected",
    "deep_evaluated",
    "passes_profit_buffer",
    "final_candidate",
    "buy_signal",
    "executed",
    "buy_signal_executed",
    "already_holding",
    "cooldown_blocked",
    "core_rescue_applied",
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed",
}
_INT_COLS: set[str] = {
    "passed_count_deep",
    "core_shadow_passed_count",
}
_FLOAT_COLS: set[str] = {
    "score_shallow",
    "score_deep",
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "pullback_pct",
    "rebound_pct",
    "gap_up_open_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
    "core_rescue_selected_score",
}
_TIMESTAMP_COLS: set[str] = {"ts"}


# ---------------------------------------------------------------------------
# File path helpers
# ---------------------------------------------------------------------------

def _candidate_outcomes_path(account: str, date: str) -> Path:
    p = _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"
    if p.exists():
        return p
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{date}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    p = _PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if p.exists():
        return p
    return _PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _default_output_path(account: str, date: str, fmt: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.{fmt}"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


# ---------------------------------------------------------------------------
# Score-components index
# cycle_snapshots → {(cycle_id, symbol): score_components_dict}
# Covers both scanner_candidates_top and selected_buy_candidate.
# ---------------------------------------------------------------------------

def _build_score_components_index(
    snapshots: list[dict[str, Any]],
    date_prefix: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snapshots:
        ts = str(snap.get("timestamp") or snap.get("ts") or "")
        if date_prefix and not ts.startswith(date_prefix):
            continue
        cycle_id = str(snap.get("cycle_id") or "").strip()
        if not cycle_id:
            continue

        def _add(sym: str, sc: Any) -> None:
            if sym and isinstance(sc, dict) and (cycle_id, sym) not in index:
                index[(cycle_id, sym)] = sc

        sel = snap.get("selected_buy_candidate")
        if isinstance(sel, dict):
            _add(
                str(sel.get("symbol") or "").strip(),
                sel.get("score_components"),
            )

        for cand in snap.get("scanner_candidates_top") or []:
            if isinstance(cand, dict):
                _add(
                    str(cand.get("symbol") or "").strip(),
                    cand.get("score_components"),
                )

    return index


def _build_core_rescue_index(
    snapshots: list[dict[str, Any]],
    date_prefix: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snapshots:
        ts = str(snap.get("timestamp") or snap.get("ts") or "")
        if date_prefix and not ts.startswith(date_prefix):
            continue

        cycle_id = str(snap.get("cycle_id") or "").strip()
        if not cycle_id:
            continue

        staged = ((snap.get("selection_details") or {}).get("staged_scan") or {})
        if not isinstance(staged, dict):
            continue

        if not staged.get("core_rescue_applied"):
            continue

        selected_symbol = str(staged.get("core_rescue_selected_symbol") or "").strip()
        if not selected_symbol:
            continue

        index[(cycle_id, selected_symbol)] = {
            "core_rescue_applied": staged.get("core_rescue_applied"),
            "core_rescue_selected_symbol": selected_symbol,
            "core_rescue_selected_score": staged.get("core_rescue_selected_score"),
            "core_rescue_replaced_symbol": staged.get("core_rescue_replaced_symbol"),
            "core_rescue_reason": staged.get("core_rescue_reason"),
        }

    return index


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_row(
    outcome: dict[str, Any],
    sc_index: dict[tuple[str, str], dict[str, Any]],
    core_rescue_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cycle_id = str(outcome.get("cycle_id") or "").strip()
    symbol = str(outcome.get("symbol") or "").strip()
    sc = sc_index.get((cycle_id, symbol)) or {}
    rescue = core_rescue_index.get((cycle_id, symbol)) or {}

    row: dict[str, Any] = {}

    # --- outcome columns (direct pass-through) ---
    for col in _OUTCOME_COLS:
        row[col] = outcome.get(col)
    if row.get("buy_signal") is None:
        row["buy_signal"] = outcome.get("final_candidate")
    if row.get("buy_signal_executed") is None:
        row["buy_signal_executed"] = outcome.get("executed")

    # --- score_component columns (from cycle snapshot join) ---
    for col in _SCORE_COMPONENT_COLS:
        # prefer snapshot join; fall back to outcome row if present
        val = sc.get(col, _SENTINEL)
        if val is _SENTINEL:
            val = outcome.get(col)
        row[col] = val

    for col in _CORE_RESCUE_COLS:
        row[col] = rescue.get(col)

    # --- core shadow columns (graceful: absent → None) ---
    for col in _CORE_SHADOW_COLS:
        row[col] = outcome.get(col)

    return row


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_signal_dataset(
    *,
    account: str,
    date: str,
    session: str = "",
    deep_eval_only: bool = True,
    fmt: str = "csv",
    output: Path | None = None,
) -> tuple[Path, int]:
    """Build and write the signal dataset. Returns (output_path, row_count)."""
    date_prefix = f"{date[:4]}-{date[4:6]}-{date[6:]}"

    outcome_rows = _read_jsonl(_candidate_outcomes_path(account, date))
    snapshots = _read_jsonl(_cycle_snapshots_path(account))
    sc_index = _build_score_components_index(snapshots, date_prefix)
    core_rescue_index = _build_core_rescue_index(snapshots, date_prefix)

    rows: list[dict[str, Any]] = []
    for outcome in outcome_rows:
        if session and str(outcome.get("session") or "").upper() != session.upper():
            continue
        if deep_eval_only and not outcome.get("deep_evaluated"):
            continue
        rows.append(_build_row(outcome, sc_index, core_rescue_index))

    rows.sort(key=lambda r: str(r.get("ts") or ""))

    out_path = output or _default_output_path(account, date, fmt)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        with out_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str))
                fh.write("\n")
    elif fmt == "parquet":
        write_flat_parquet(
            rows=rows,
            path=out_path,
            ordered_columns=ALL_COLS,
            bool_cols=_BOOL_COLS,
            int_cols=_INT_COLS,
            float_cols=_FLOAT_COLS,
            timestamp_cols=_TIMESTAMP_COLS,
        )
    else:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(ALL_COLS), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col) for col in ALL_COLS})

    return out_path, len(rows)


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"
_W = 68


def _header(t: str) -> str:
    return f"{_BOLD}{_CYAN}{t}{_RESET}"


def _ok(t: str) -> str:
    return f"{_GREEN}{t}{_RESET}"


def _is_truthy(val: Any) -> bool:
    """Handle both native bool (from jsonl) and string bool (from csv re-read)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() == "true"
    return bool(val)


def _is_set(val: Any) -> bool:
    """True if the value is present and not a null-like string."""
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip() not in ("", "None")
    return True


def _print_report(
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    account: str,
    date: str,
    session: str,
    deep_eval_only: bool,
    fmt: str,
) -> None:
    total = len(rows)
    deep = sum(1 for r in rows if _is_truthy(r.get("deep_evaluated")))
    final = sum(1 for r in rows if _is_truthy(r.get("final_candidate")))
    executed = sum(1 for r in rows if _is_truthy(r.get("executed")))
    with_sc = sum(1 for r in rows if _is_set(r.get("trend_alignment_score")))
    with_shadow = sum(1 for r in rows if _is_set(r.get("core_shadow_evaluated")))

    by_bucket: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for r in rows:
        b = str(r.get("selection_bucket") or "?")
        s = str(r.get("stage_reached") or "?")
        by_bucket[b] = by_bucket.get(b, 0) + 1
        by_stage[s] = by_stage.get(s, 0) + 1

    print()
    print(_header("═" * _W))
    print(_header("  export_signal_dataset"))
    print(_header("═" * _W))
    print(f"  account   : {account}")
    print(f"  date      : {date}")
    print(f"  session   : {session or 'ALL'}")
    print(f"  stages    : {'deep_eval+' if deep_eval_only else 'all'}")
    print(f"  format    : {fmt}")
    print()
    print(_header("─" * _W))
    print(_header("  Rows exported"))
    print(_header("─" * _W))
    print(f"  Total rows          : {total}")
    print(f"  deep_evaluated      : {deep}")
    print(f"  final_candidate     : {final}")
    print(f"  executed            : {executed}")
    sc_pct = round(with_sc / total * 100, 1) if total else 0.0
    sc_text = f"{with_sc} / {total} ({sc_pct}%)"
    print(f"  score_components    : {_ok(sc_text) if sc_pct >= 50 else _YELLOW + sc_text + _RESET}")
    print(f"  core_shadow fields  : {with_shadow} rows (None if logs pre-date feature)")
    print()
    print(_header("─" * _W))
    print(_header("  By bucket"))
    print(_header("─" * _W))
    for b, n in sorted(by_bucket.items()):
        print(f"  {b:<16} {n:>5}")
    print()
    print(_header("─" * _W))
    print(_header("  By stage_reached"))
    print(_header("─" * _W))
    for s, n in sorted(by_stage.items(), key=lambda x: -x[1]):
        print(f"  {s:<28} {n:>5}")
    print()
    print(_header("─" * _W))
    print(_header("  Output"))
    print(_header("─" * _W))
    print(f"  {_ok('✓')} {out_path}")
    print(f"  Columns : {len(ALL_COLS)}")
    print()
    print(_header("═" * _W))
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a flat signal dataset for offline analysis / event study.",
    )
    parser.add_argument("--date", required=True, help="Date YYYYMMDD")
    parser.add_argument("--account", required=True, help="Account identifier (e.g. mock_12345678_01)")
    parser.add_argument("--session", default="", help="Filter by session (e.g. REGULAR). Default: all.")
    parser.add_argument(
        "--all-stages",
        action="store_true",
        help="Include all pipeline stages, not only deep_eval+ rows.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "jsonl", "parquet"],
        default="csv",
        help="Output format. Default: csv.",
    )
    parser.add_argument("--output", default="", help="Output file path. Default: logs/signal_dataset_{account}_{date}.{format}")
    parser.add_argument("--json", action="store_true", help="Print JSON summary to stdout instead of terminal report.")
    args = parser.parse_args()

    deep_eval_only = not args.all_stages
    output = Path(args.output) if args.output else None

    out_path, row_count = export_signal_dataset(
        account=args.account,
        date=args.date,
        session=args.session,
        deep_eval_only=deep_eval_only,
        fmt=args.format,
        output=output,
    )

    if row_count == 0:
        # P0-c: emit the empty-but-valid dataset (header already written) and warn,
        # rather than hard-failing the EOD chain when candidate_outcomes is absent.
        print(
            f"No rows found for account={args.account} date={args.date} "
            f"session={args.session or 'ALL'} — wrote empty dataset to {out_path}.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps({"rows": 0, "output": str(out_path), "columns": len(ALL_COLS)}, indent=2))
        return

    if args.json:
        print(json.dumps({"rows": row_count, "output": str(out_path), "columns": len(ALL_COLS)}, indent=2))
        return

    # Re-read for the report (cheap, already written)
    if args.format == "jsonl":
        rows = _read_jsonl(out_path)
    elif args.format == "parquet":
        rows = read_flat_parquet(out_path)
    else:
        rows = _read_csv_rows(out_path)
    _print_report(
        rows,
        out_path,
        account=args.account,
        date=args.date,
        session=args.session,
        deep_eval_only=deep_eval_only,
        fmt=args.format,
    )


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


if __name__ == "__main__":
    main()
