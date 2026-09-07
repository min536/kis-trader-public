"""CLI: export_signal_outcome_dataset

Attach post-entry outcome fields to a flat signal dataset using only existing
cycle snapshot logs.

This is an offline event-study helper, not a trading backtester.

Usage:
    python3 -m app.tools.export_signal_outcome_dataset \
        --date 20260408 --account mock_12345678_01

    python3 -m app.tools.export_signal_outcome_dataset \
        --file logs/signal_dataset_mock_12345678_01_20260408.csv \
        --account mock_12345678_01 --format jsonl --output /tmp/signal_outcomes.jsonl

    python3 -m app.tools.export_signal_outcome_dataset \
        --file logs/signal_dataset_mock_12345678_01_20260408.parquet \
        --account mock_12345678_01 --format parquet --output /tmp/signal_outcomes.parquet
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from app.auth.settings import PROJECT_ROOT
from app.backtest.reconstruct import enrich_signal_rows_with_post_entry_outcomes
from app.tools.parquet_utils import read_flat_parquet, write_flat_parquet

_PROJECT_ROOT = PROJECT_ROOT

_OUTCOME_COLS: tuple[str, ...] = (
    "entry_ts",
    "entry_price",
    "obs_ts_5m",
    "price_5m",
    "ret_5m_bps",
    "obs_ts_30m",
    "price_30m",
    "ret_30m_bps",
    "obs_ts_eod",
    "price_eod",
    "ret_eod_bps",
)

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
    "budget_rescue_enabled",
    "budget_rescue_applied",
    "core_rescue_applied",
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed",
}
_INT_COLS: set[str] = {
    "passed_count_deep",
    "core_shadow_passed_count",
    "budget_rescue_qty",
    "entry_price",
    "price_5m",
    "price_30m",
    "price_eod",
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
    "ret_5m_bps",
    "ret_30m_bps",
    "ret_eod_bps",
}
_TIMESTAMP_COLS: set[str] = {
    "ts",
    "entry_ts",
    "obs_ts_5m",
    "obs_ts_30m",
    "obs_ts_eod",
}


def _signal_dataset_path(account: str, date: str) -> Path:
    csv_path = _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.csv"
    if csv_path.exists():
        return csv_path
    jsonl_path = _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.jsonl"
    if jsonl_path.exists():
        return jsonl_path
    return _PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.parquet"


def _cycle_snapshots_path(account: str) -> Path:
    account_path = _PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _default_output_path(account: str, date: str, fmt: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.{fmt}"


def _load_signal_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    if path.suffix.lower() == ".parquet":
        return read_flat_parquet(path)
    if path.suffix.lower() == ".jsonl":
        return _read_jsonl(path)
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _write_rows(rows: list[dict[str, Any]], path: Path, *, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
        return
    if fmt == "parquet":
        fieldnames: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        write_flat_parquet(
            rows=rows,
            path=path,
            ordered_columns=fieldnames,
            bool_cols=_BOOL_COLS,
            int_cols=_INT_COLS,
            float_cols=_FLOAT_COLS,
            timestamp_cols=_TIMESTAMP_COLS,
        )
        return

    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _coverage_count(rows: list[dict[str, Any]], col: str) -> int:
    count = 0
    for row in rows:
        value = row.get(col)
        if value not in (None, "", "None"):
            count += 1
    return count


def _print_report(
    *,
    input_path: Path,
    output_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    total = len(rows)
    entry = _coverage_count(rows, "entry_price")
    h5 = _coverage_count(rows, "price_5m")
    h30 = _coverage_count(rows, "price_30m")
    eod = _coverage_count(rows, "price_eod")

    print()
    print("export_signal_outcome_dataset")
    print(f"  input  : {input_path.name}")
    print(f"  rows   : {total}")
    print(f"  entry  : {entry}/{total}")
    print(f"  +5m    : {h5}/{total}")
    print(f"  +30m   : {h30}/{total}")
    print(f"  eod*   : {eod}/{total}")
    print(f"  output : {output_path}")
    print()
    print("  * price_eod is the latest same-day observed price after entry,")
    print("    reconstructed from existing snapshots. It is not a guessed exchange close.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach post-entry outcome fields to a flat signal dataset"
    )
    parser.add_argument("--file", default="", help="Input signal dataset CSV or JSONL")
    parser.add_argument("--date", default="", help="Date YYYYMMDD for auto-locating the signal dataset")
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument("--session", default="", help="Optional session filter")
    parser.add_argument("--format", choices=("csv", "jsonl", "parquet"), default="csv", help="Output format")
    parser.add_argument("--output", default="", help="Explicit output path")
    args = parser.parse_args()

    if not args.file and not args.date:
        print("Provide either --file or --date.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.file) if args.file else _signal_dataset_path(args.account, args.date)
    signal_rows = _load_signal_rows(input_path)
    if args.session:
        signal_rows = [
            row
            for row in signal_rows
            if str(row.get("session") or "").upper() == args.session.upper()
        ]

    output_path = (
        Path(args.output)
        if args.output
        else _default_output_path(args.account, args.date or "from_file", args.format)
    )

    # P0-c: an empty signal dataset or missing snapshots yields an empty-but-valid
    # outcome dataset + warning, not a hard failure of the EOD chain.
    if not signal_rows:
        print(
            "No signal rows matched the requested input/filter "
            f"— wrote empty outcome dataset to {output_path}.",
            file=sys.stderr,
        )
        _write_rows([], output_path, fmt=args.format)
        _print_report(input_path=input_path, output_path=output_path, rows=[])
        return

    snapshots_path = _cycle_snapshots_path(args.account)
    cycle_snapshots = _read_jsonl(snapshots_path)
    if not cycle_snapshots:
        print(
            f"No cycle snapshots found: {snapshots_path} "
            f"— wrote empty outcome dataset to {output_path}.",
            file=sys.stderr,
        )
        _write_rows([], output_path, fmt=args.format)
        _print_report(input_path=input_path, output_path=output_path, rows=[])
        return

    rows = enrich_signal_rows_with_post_entry_outcomes(signal_rows, cycle_snapshots)
    _write_rows(rows, output_path, fmt=args.format)
    _print_report(input_path=input_path, output_path=output_path, rows=rows)


if __name__ == "__main__":
    main()
