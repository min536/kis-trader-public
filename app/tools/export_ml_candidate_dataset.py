"""CLI: export_ml_candidate_dataset

Build a flat offline dataset for later supervised-learning experiments from
existing signal exports and cycle snapshot logs.

This is research-only infrastructure. It does not train models or touch live
execution paths.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

from app.auth.settings import PROJECT_ROOT
from app.backtest.reconstruct import enrich_signal_rows_with_post_entry_outcomes
from app.tools.export_signal_dataset import export_signal_dataset
from app.tools.ml_research_common import (
    LABEL_DEFINITIONS,
    ML_COST_COLS,
    ML_DATASET_COLUMNS,
    ML_DECISION_FEATURE_COLS,
    ML_IDENTIFIER_COLS,
    ML_OUTCOME_COLS,
    ML_POLICY_CONTEXT_COLS,
    build_row_id,
    classify_time_of_day,
    load_rows,
    non_missing_count,
    normalize_null,
    parse_trade_date,
    resolve_output_path,
    write_json,
    write_rows,
)
from app.tools.ml_research_common import read_jsonl as read_jsonl_rows

_SIGNAL_OUTPUT_SUFFIXES: tuple[str, ...] = ("csv", "jsonl", "parquet")
_SNAPSHOT_COST_FIELDS: tuple[str, ...] = (
    "expected_total_cost_krw",
    "expected_cost_bps",
    "net_edge_bps",
    "net_profit_buffer_bps",
    "cost_quality_score",
    "expected_cost_penalty",
    "cost_block_reason",
)
_SNAPSHOT_MARKET_FIELDS: tuple[str, ...] = (
    "current_price",
    "open_price",
    "low_price",
    "prev_day_change_pct",
)


def _signal_dataset_path(account: str, date: str) -> Path | None:
    for suffix in _SIGNAL_OUTPUT_SUFFIXES:
        path = PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date}.{suffix}"
        if path.exists():
            return path
    return None


def _signal_outcomes_path(account: str, date: str) -> Path | None:
    for suffix in _SIGNAL_OUTPUT_SUFFIXES:
        path = PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.{suffix}"
        if path.exists():
            return path
    return None


def _cycle_snapshots_path(account: str) -> Path:
    account_path = PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _default_output_path(account: str, date: str, fmt: str) -> Path:
    return PROJECT_ROOT / "logs" / f"ml_candidate_dataset_{account}_{date}.{fmt}"


def _default_manifest_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.manifest.json")


def _default_note_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.research.md")


def _date_prefix(date: str) -> str:
    if len(date) != 8 or not date.isdigit():
        return ""
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def _pick_candidate_value(candidate: dict[str, Any], key: str) -> Any:
    if key in candidate:
        return candidate.get(key)
    score_components = candidate.get("score_components") or {}
    if isinstance(score_components, dict) and key in score_components:
        return score_components.get(key)
    market_snapshot = candidate.get("market_snapshot") or {}
    if isinstance(market_snapshot, dict) and key in market_snapshot:
        return market_snapshot.get(key)
    return None


def _build_snapshot_feature_index(
    snapshots: list[dict[str, Any]],
    *,
    date: str = "",
) -> dict[tuple[str, str], dict[str, Any]]:
    prefix = _date_prefix(date)
    index: dict[tuple[str, str], dict[str, Any]] = {}

    def _record(candidate: dict[str, Any], cycle_id: str, *, prefer_existing: bool) -> None:
        symbol = str(candidate.get("symbol") or "").strip()
        if not cycle_id or not symbol:
            return
        key = (cycle_id, symbol)
        if prefer_existing and key in index:
            return
        index[key] = {
            field: _pick_candidate_value(candidate, field)
            for field in (_SNAPSHOT_COST_FIELDS + _SNAPSHOT_MARKET_FIELDS)
        }

    for snapshot in snapshots:
        snapshot_ts = str(snapshot.get("timestamp") or snapshot.get("ts") or "").strip()
        if prefix and not snapshot_ts.startswith(prefix):
            continue
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        if not cycle_id:
            continue

        for candidate in snapshot.get("scanner_candidates_top") or []:
            if isinstance(candidate, dict):
                _record(candidate, cycle_id, prefer_existing=True)

        selected = snapshot.get("selected_buy_candidate")
        if isinstance(selected, dict):
            _record(selected, cycle_id, prefer_existing=False)

    return index


def _has_outcome_coverage(rows: list[dict[str, Any]]) -> bool:
    return any(
        normalize_null(row.get("ret_eod_bps")) is not None
        or normalize_null(row.get("entry_price")) is not None
        for row in rows
    )


def _normalize_row(
    row: dict[str, Any],
    *,
    source_dataset: str,
    snapshot_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cycle_id = str(normalize_null(row.get("cycle_id")) or "").strip()
    symbol = str(normalize_null(row.get("symbol")) or "").strip()
    snapshot_features = snapshot_index.get((cycle_id, symbol)) or {}

    normalized: dict[str, Any] = {}
    for column in ML_DATASET_COLUMNS:
        normalized[column] = normalize_null(row.get(column))

    normalized["source_dataset"] = source_dataset
    normalized["trade_date"] = parse_trade_date(row.get("ts"))
    normalized["time_of_day_bucket"] = classify_time_of_day(row.get("ts"))

    for column in (_SNAPSHOT_COST_FIELDS + _SNAPSHOT_MARKET_FIELDS):
        if normalized.get(column) is None:
            normalized[column] = normalize_null(snapshot_features.get(column))

    normalized["row_id"] = build_row_id(normalized)
    return normalized


def _render_research_note(manifest: dict[str, Any]) -> str:
    safe_features = manifest["feature_policy"]["decision_time_safe_feature_columns"]
    labels = manifest["supported_labels"]
    leakage_risks = manifest["feature_policy"]["leakage_risks"]
    lines = [
        "# Offline ML Candidate Dataset",
        "",
        "## What This Is For",
        "",
        "- Offline supervised-learning research only.",
        "- Good fits: candidate quality prediction, score calibration, deep-eval reranking assistance, core quality filtering, rescue/shadow analysis.",
        "- Not for live parity claims and not for direct production wiring.",
        "",
        "## Safe Feature Use",
        "",
        "- Use only decision-time fields for feature matrices.",
        "- Recommended feature columns:",
    ]
    lines.extend(f"- `{column}`" for column in safe_features)
    lines.extend(
        [
            "",
            "## Labels Available",
            "",
        ]
    )
    lines.extend(f"- `{name}`: {definition}" for name, definition in labels.items())
    lines.extend(
        [
            "",
            "## Leakage Risks To Avoid",
            "",
        ]
    )
    lines.extend(f"- {risk}" for risk in leakage_risks)
    lines.extend(
        [
            "",
            "## Validation Rules",
            "",
            "- Preserve temporal order.",
            "- Use walk-forward or forward-chaining validation only.",
            "- Random CV is disallowed because regime drift, intraday dependence, and policy changes can leak future information.",
            "- Inspect precision, recall, F1, positive-rate, and calibration before accuracy.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_manifest(
    *,
    rows: list[dict[str, Any]],
    input_path: str | None,
    output_path: Path,
    manifest_path: Path,
    note_path: Path,
    account: str,
    date: str,
    session: str,
    source_dataset: str,
) -> dict[str, Any]:
    coverage_cols = (
        "score_deep",
        "trend_alignment_score",
        "core_shadow_evaluated",
        "core_rescue_applied",
        "expected_cost_bps",
        "net_profit_buffer_bps",
        "ret_5m_bps",
        "ret_30m_bps",
        "ret_eod_bps",
        "final_candidate",
        "executed",
    )
    coverage = {
        column: {
            "present": non_missing_count(rows, column),
            "total": len(rows),
        }
        for column in coverage_cols
    }

    return {
        "dataset_type": "ml_candidate_dataset",
        "created_at": datetime.now().isoformat(),
        "input_path": input_path,
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "note_path": str(note_path),
        "account": account or None,
        "date": date or None,
        "session": session or None,
        "source_dataset": source_dataset,
        "row_count": len(rows),
        "column_count": len(ML_DATASET_COLUMNS),
        "column_groups": {
            "identifiers": list(ML_IDENTIFIER_COLS),
            "decision_features": list(ML_DECISION_FEATURE_COLS),
            "cost_fields": list(ML_COST_COLS),
            "policy_context": list(ML_POLICY_CONTEXT_COLS),
            "outcomes": list(ML_OUTCOME_COLS),
        },
        "coverage": coverage,
        "feature_policy": {
            "decision_time_safe_feature_columns": list(
                ML_DECISION_FEATURE_COLS + ML_COST_COLS + ("time_of_day_bucket",)
            ),
            "target_or_outcome_columns": list(ML_OUTCOME_COLS),
            "policy_imitation_columns": [
                "final_candidate",
                "executed",
                "rejection_reason",
                "selection_outcome",
            ],
            "leakage_risks": [
                "Do not train on entry_ts, entry_price, or future returns when predicting future returns.",
                "Treat final_candidate and executed as policy outcomes, not causal market features.",
                "Keep any feature engineering aligned to information available at ts only.",
            ],
        },
        "supported_labels": LABEL_DEFINITIONS,
        "split_policy": "time_ordered_walk_forward_only",
        "random_cv_disallowed": True,
        "missing_value_policy": (
            "JSONL and Parquet keep nulls. CSV writes empty cells for missing values."
        ),
        "research_readiness": (
            "ready for offline supervised-learning experiments with decision-time "
            "features and walk-forward validation"
        ),
        "notes": [
            "This export is research-only and does not imply live parity.",
            "Class imbalance is expected; inspect precision, recall, F1, and positive-rate.",
            "Cost-aware labels should use expected_cost_bps when present.",
        ],
    }


def _load_base_rows(
    *,
    input_path: str,
    account: str,
    date: str,
    session: str,
    include_all_stages: bool,
) -> tuple[list[dict[str, Any]], str | None, str]:
    if input_path:
        path = Path(input_path)
        if not path.exists():
            print(f"Input file not found: {path}", file=sys.stderr)
            sys.exit(1)
        rows = load_rows(path)
        return rows, str(path), path.stem

    if not account or not date:
        print("Provide either --input or both --account and --date.", file=sys.stderr)
        sys.exit(1)

    signal_outcomes_path = _signal_outcomes_path(account, date)
    if signal_outcomes_path is not None:
        return load_rows(signal_outcomes_path), str(signal_outcomes_path), signal_outcomes_path.stem

    signal_dataset_path = _signal_dataset_path(account, date)
    if signal_dataset_path is not None:
        return load_rows(signal_dataset_path), str(signal_dataset_path), signal_dataset_path.stem

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "signal_dataset.jsonl"
        export_signal_dataset(
            account=account,
            date=date,
            session=session,
            deep_eval_only=not include_all_stages,
            fmt="jsonl",
            output=temp_path,
        )
        rows = load_rows(temp_path)
    return rows, None, "generated_from_candidate_logs"


def export_ml_candidate_dataset(
    *,
    input_path: str = "",
    account: str = "",
    date: str = "",
    session: str = "",
    cycle_snapshots_path: str = "",
    include_all_stages: bool = False,
    fmt: str = "jsonl",
    output: str = "",
    manifest_output: str = "",
    note_output: str = "",
) -> tuple[Path, Path, Path, int]:
    base_rows, resolved_input_path, source_dataset = _load_base_rows(
        input_path=input_path,
        account=account,
        date=date,
        session=session,
        include_all_stages=include_all_stages,
    )

    if session:
        base_rows = [
            row
            for row in base_rows
            if str(row.get("session") or "").upper() == session.upper()
        ]

    snapshots: list[dict[str, Any]] = []
    resolved_cycle_path: str | None = None
    if cycle_snapshots_path:
        snapshot_path = Path(cycle_snapshots_path)
        if not snapshot_path.exists():
            print(f"Cycle snapshot file not found: {snapshot_path}", file=sys.stderr)
            sys.exit(1)
        snapshots = read_jsonl_rows(snapshot_path)
        resolved_cycle_path = str(snapshot_path)
    elif account:
        auto_snapshot_path = _cycle_snapshots_path(account)
        if auto_snapshot_path.exists():
            snapshots = read_jsonl_rows(auto_snapshot_path)
            resolved_cycle_path = str(auto_snapshot_path)

    if snapshots and not _has_outcome_coverage(base_rows):
        base_rows = enrich_signal_rows_with_post_entry_outcomes(base_rows, snapshots)

    snapshot_index = _build_snapshot_feature_index(snapshots, date=date)
    rows = [
        _normalize_row(
            row,
            source_dataset=source_dataset,
            snapshot_index=snapshot_index,
        )
        for row in base_rows
    ]
    rows.sort(key=lambda row: (str(row.get("ts") or ""), str(row.get("symbol") or "")))

    output_path = resolve_output_path(
        output,
        fmt,
        default_path=_default_output_path(account or "from_file", date or "from_file", fmt),
    )
    manifest_path = Path(manifest_output) if manifest_output else _default_manifest_path(output_path)
    note_path = Path(note_output) if note_output else _default_note_path(output_path)

    write_rows(rows=rows, path=output_path, fmt=fmt, ordered_columns=ML_DATASET_COLUMNS)
    manifest = _build_manifest(
        rows=rows,
        input_path=resolved_input_path,
        output_path=output_path,
        manifest_path=manifest_path,
        note_path=note_path,
        account=account,
        date=date,
        session=session,
        source_dataset=source_dataset,
    )
    if resolved_cycle_path:
        manifest["cycle_snapshots_path"] = resolved_cycle_path
    write_json(manifest_path, manifest)
    note_path.write_text(_render_research_note(manifest) + "\n", encoding="utf-8")
    return output_path, manifest_path, note_path, len(rows)


def _print_report(
    *,
    output_path: Path,
    manifest_path: Path,
    note_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    total = len(rows)
    print()
    print("export_ml_candidate_dataset")
    print(f"  rows        : {total}")
    print(f"  ret_eod     : {non_missing_count(rows, 'ret_eod_bps')}/{total}")
    print(f"  cost_bps    : {non_missing_count(rows, 'expected_cost_bps')}/{total}")
    print(f"  final_flag  : {non_missing_count(rows, 'final_candidate')}/{total}")
    print(f"  output      : {output_path}")
    print(f"  manifest    : {manifest_path}")
    print(f"  research_md : {note_path}")
    print("  read        : research-ready for walk-forward offline experiments")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a compact ML-ready candidate dataset from offline logs."
    )
    parser.add_argument("--input", default="", help="Existing signal dataset/outcome file")
    parser.add_argument("--account", default="", help="Account identifier for auto-discovery")
    parser.add_argument("--date", default="", help="Date YYYYMMDD for auto-discovery")
    parser.add_argument("--session", default="", help="Optional session filter")
    parser.add_argument(
        "--all-stages",
        action="store_true",
        help="When auto-building from candidate logs, include all stages instead of deep_eval+ only.",
    )
    parser.add_argument(
        "--cycle-snapshots",
        default="",
        help="Optional cycle snapshot JSONL for outcome/cost joins",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "parquet"),
        default="jsonl",
        help="Output format for the exported dataset.",
    )
    parser.add_argument("--output", default="", help="Dataset output path")
    parser.add_argument("--manifest-output", default="", help="Optional manifest JSON path")
    parser.add_argument("--note-output", default="", help="Optional research note Markdown path")
    args = parser.parse_args(argv)

    output_path, manifest_path, note_path, _ = export_ml_candidate_dataset(
        input_path=args.input,
        account=args.account,
        date=args.date,
        session=args.session,
        cycle_snapshots_path=args.cycle_snapshots,
        include_all_stages=args.all_stages,
        fmt=args.format,
        output=args.output,
        manifest_output=args.manifest_output,
        note_output=args.note_output,
    )
    rows = load_rows(output_path)
    _print_report(
        output_path=output_path,
        manifest_path=manifest_path,
        note_path=note_path,
        rows=rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
