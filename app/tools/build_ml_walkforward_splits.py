"""CLI: build_ml_walkforward_splits

Create chronological train/validation/test folds for offline ML research.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from app.tools.ml_research_common import ML_DATASET_COLUMNS, ML_LABEL_COLS, load_rows, parse_trade_date, write_json, write_rows


def _default_output_dir(input_path: Path) -> Path:
    return input_path.parent / f"{input_path.stem}_walkforward"


def _build_fold_ranges(
    unique_dates: list[str],
    *,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> list[dict[str, list[str]]]:
    span = train_days + validation_days + test_days
    if len(unique_dates) < span:
        return []

    folds: list[dict[str, list[str]]] = []
    start_index = 0
    while start_index + span <= len(unique_dates):
        train = unique_dates[start_index : start_index + train_days]
        validation = unique_dates[start_index + train_days : start_index + train_days + validation_days]
        test = unique_dates[
            start_index + train_days + validation_days : start_index + span
        ]
        folds.append(
            {
                "train": train,
                "validation": validation,
                "test": test,
            }
        )
        start_index += step_days
    return folds


def _filter_rows_by_dates(rows: list[dict[str, Any]], dates: set[str]) -> list[dict[str, Any]]:
    filtered = [
        row
        for row in rows
        if parse_trade_date(row.get("ts")) in dates
    ]
    filtered.sort(key=lambda row: (str(row.get("ts") or ""), str(row.get("symbol") or "")))
    return filtered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create chronological walk-forward dataset splits."
    )
    parser.add_argument("--input", required=True, help="Input ML dataset or labeled ML dataset")
    parser.add_argument("--output-dir", default="", help="Directory to write folds into")
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "parquet"),
        default="jsonl",
        help="Output format for fold files.",
    )
    parser.add_argument("--train-days", type=int, default=20, help="Number of unique dates in each train window")
    parser.add_argument(
        "--validation-days",
        type=int,
        default=5,
        help="Number of unique dates in each validation window",
    )
    parser.add_argument("--test-days", type=int, default=5, help="Number of unique dates in each test window")
    parser.add_argument("--step-days", type=int, default=5, help="How many dates to advance between folds")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rows = load_rows(input_path)
    rows = [row for row in rows if parse_trade_date(row.get("ts")) is not None]
    unique_dates = sorted({parse_trade_date(row.get("ts")) for row in rows if parse_trade_date(row.get("ts"))})

    folds = _build_fold_ranges(
        unique_dates,
        train_days=args.train_days,
        validation_days=args.validation_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    if not folds:
        print(
            "Not enough unique dates for the requested walk-forward window.",
            file=sys.stderr,
        )
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "format": args.format,
        "split_policy": "walk_forward_time_ordered",
        "random_cv_disallowed": True,
        "parameters": {
            "train_days": args.train_days,
            "validation_days": args.validation_days,
            "test_days": args.test_days,
            "step_days": args.step_days,
        },
        "folds": [],
    }

    ordered_columns = ML_DATASET_COLUMNS + ML_LABEL_COLS
    for fold_index, fold in enumerate(folds, start=1):
        fold_name = f"fold_{fold_index:03d}"
        fold_dir = output_dir / fold_name
        fold_dir.mkdir(parents=True, exist_ok=True)

        train_rows = _filter_rows_by_dates(rows, set(fold["train"]))
        validation_rows = _filter_rows_by_dates(rows, set(fold["validation"]))
        test_rows = _filter_rows_by_dates(rows, set(fold["test"]))

        train_path = fold_dir / f"train.{args.format}"
        validation_path = fold_dir / f"validation.{args.format}"
        test_path = fold_dir / f"test.{args.format}"

        write_rows(rows=train_rows, path=train_path, fmt=args.format, ordered_columns=ordered_columns)
        write_rows(
            rows=validation_rows,
            path=validation_path,
            fmt=args.format,
            ordered_columns=ordered_columns,
        )
        write_rows(rows=test_rows, path=test_path, fmt=args.format, ordered_columns=ordered_columns)

        manifest["folds"].append(
            {
                "fold": fold_name,
                "train_dates": fold["train"],
                "validation_dates": fold["validation"],
                "test_dates": fold["test"],
                "counts": {
                    "train": len(train_rows),
                    "validation": len(validation_rows),
                    "test": len(test_rows),
                },
                "paths": {
                    "train": str(train_path),
                    "validation": str(validation_path),
                    "test": str(test_path),
                },
            }
        )

    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)

    print()
    print("build_ml_walkforward_splits")
    print(f"  folds       : {len(manifest['folds'])}")
    print(f"  output_dir  : {output_dir}")
    print(f"  manifest    : {manifest_path}")
    print("  read        : chronological folds only, no random shuffle")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
