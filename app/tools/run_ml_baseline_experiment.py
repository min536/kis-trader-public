"""CLI: run_ml_baseline_experiment

Run a small, explainable offline supervised-learning baseline using
chronological walk-forward folds only.

This tool is research-only. It does not alter live trading logic.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from app.tools.ml_baseline_core import (
    EncodedDataset,
    FoldData,
    PureLogisticRegression,
    _aggregate_feature_summary,
    _aggregate_results,
    _bool_int,
    _build_fold_ranges,
    _build_model,
    _choose_threshold,
    _classification_metrics,
    _encode_rows,
    _filter_rows_for_label,
    _fit_and_score_fold,
    _mean_metric,
    _precision_recall_curve_area,
    _roc_auc_score,
    _split_requested_features,
    _std_metric,
    _summarize_importances,
    _value_as_numeric,
)
from app.tools.ml_research_common import (
    LABEL_DEFINITIONS,
    load_rows,
    parse_trade_date,
    resolve_output_path,
    write_json,
)

def _load_folds_from_directory(input_dir: Path) -> list[FoldData]:
    manifest_path = input_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    folds_meta = manifest.get("folds") or []

    folds: list[FoldData] = []
    if folds_meta:
        for fold_meta in folds_meta:
            paths = fold_meta.get("paths") or {}
            train_path = Path(paths.get("train") or "")
            validation_path = Path(paths.get("validation") or "")
            test_path = Path(paths.get("test") or "")
            if not train_path.exists() or not validation_path.exists() or not test_path.exists():
                continue
            folds.append(
                FoldData(
                    fold_name=str(fold_meta.get("fold") or train_path.parent.name),
                    train_rows=load_rows(train_path),
                    validation_rows=load_rows(validation_path),
                    test_rows=load_rows(test_path),
                    train_dates=list(fold_meta.get("train_dates") or []),
                    validation_dates=list(fold_meta.get("validation_dates") or []),
                    test_dates=list(fold_meta.get("test_dates") or []),
                )
            )
        return folds

    for fold_dir in sorted(path for path in input_dir.iterdir() if path.is_dir() and path.name.startswith("fold_")):
        train_candidates = sorted(fold_dir.glob("train.*"))
        validation_candidates = sorted(fold_dir.glob("validation.*"))
        test_candidates = sorted(fold_dir.glob("test.*"))
        if not train_candidates or not validation_candidates or not test_candidates:
            continue
        train_path = train_candidates[0]
        validation_path = validation_candidates[0]
        test_path = test_candidates[0]
        folds.append(
            FoldData(
                fold_name=fold_dir.name,
                train_rows=load_rows(train_path),
                validation_rows=load_rows(validation_path),
                test_rows=load_rows(test_path),
                train_dates=[],
                validation_dates=[],
                test_dates=[],
            )
        )
    return folds


def _load_folds_from_labeled_dataset(
    input_file: Path,
    *,
    label: str,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> list[FoldData]:
    rows = _filter_rows_for_label(load_rows(input_file), label)
    unique_dates = sorted(
        {
            trade_date
            for trade_date in (parse_trade_date(row.get("ts")) for row in rows)
            if trade_date
        }
    )
    fold_ranges = _build_fold_ranges(
        unique_dates,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
    )
    folds: list[FoldData] = []
    for index, fold_range in enumerate(fold_ranges, start=1):
        train_dates = set(fold_range["train"])
        validation_dates = set(fold_range["validation"])
        test_dates = set(fold_range["test"])
        folds.append(
            FoldData(
                fold_name=f"fold_{index:03d}",
                train_rows=[row for row in rows if parse_trade_date(row.get("ts")) in train_dates],
                validation_rows=[row for row in rows if parse_trade_date(row.get("ts")) in validation_dates],
                test_rows=[row for row in rows if parse_trade_date(row.get("ts")) in test_dates],
                train_dates=sorted(train_dates),
                validation_dates=sorted(validation_dates),
                test_dates=sorted(test_dates),
            )
        )
    return folds


def render_markdown(result: dict[str, Any]) -> str:
    context = result["experiment_context"]
    aggregate = result["aggregate"]
    features = result["features"]
    lines = [
        "# ML Baseline Experiment",
        "",
        "## Experiment Context",
        "",
        f"- Label: `{context['label']}`",
        f"- Model requested: `{context['model_requested']}`",
        f"- Model used: `{context['model_used']}`",
        f"- Backend: `{context['backend']}`",
        f"- Input: `{context['input_path']}`",
        f"- Random CV disallowed: `{context['random_cv_disallowed']}`",
        "",
        "## Features Used",
        "",
        f"- Numeric features: {', '.join(features['used_numeric']) or 'none'}",
        f"- Categorical features: {', '.join(features['used_categorical']) or 'none'}",
        f"- Encoded feature count: `{features['encoded_feature_count']}`",
        "",
        "## Aggregate Metrics",
        "",
        f"- Evaluated folds: `{aggregate['evaluated_folds']}`",
        f"- Skipped folds: `{aggregate['skipped_folds']}`",
        f"- Class balance: `{aggregate['class_balance']}`",
        f"- Macro precision: `{aggregate['macro_average']['precision']}`",
        f"- Macro recall: `{aggregate['macro_average']['recall']}`",
        f"- Macro F1: `{aggregate['macro_average']['f1']}`",
        f"- Macro ROC-AUC: `{aggregate['macro_average']['roc_auc']}`",
        f"- Macro PR-AUC: `{aggregate['macro_average']['pr_auc']}`",
        f"- Research read: `{aggregate['research_read']}`",
        "",
        "## Per-Fold Metrics",
        "",
    ]
    for fold in result["folds"]:
        if fold.get("skipped"):
            lines.append(f"- {fold['fold']}: skipped ({fold.get('skip_reason')})")
            continue
        metrics = fold["test_metrics"]
        lines.append(
            f"- {fold['fold']}: precision={metrics['precision']}, recall={metrics['recall']}, "
            f"f1={metrics['f1']}, pr_auc={metrics['pr_auc']}, roc_auc={metrics['roc_auc']}, "
            f"threshold={fold['threshold']}"
        )
    lines.extend(
        [
            "",
            "## Top Features",
            "",
        ]
    )
    for feature in aggregate["top_features"]:
        lines.append(
            f"- `{feature['feature']}`: importance={feature['mean_importance']}, "
            f"signed={feature.get('mean_signed_weight')}"
        )
    if aggregate["skipped_fold_reasons"]:
        lines.extend(
            [
                "",
                "## Skipped Folds",
                "",
            ]
        )
        for row in aggregate["skipped_fold_reasons"]:
            lines.append(f"- {row['fold']}: {row['reason']}")
    return "\n".join(lines) + "\n"


def run_experiment(
    *,
    input_path: str,
    label: str,
    model_name: str,
    requested_features: str,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> dict[str, Any]:
    input_obj = Path(input_path)
    if input_obj.is_dir():
        folds = _load_folds_from_directory(input_obj)
    else:
        folds = _load_folds_from_labeled_dataset(
            input_obj,
            label=label,
            train_days=train_days,
            validation_days=validation_days,
            test_days=test_days,
            step_days=step_days,
        )

    if not folds:
        raise RuntimeError(
            "No chronological folds available. Provide a walk-forward fold directory "
            "or a labeled dataset with enough unique dates."
        )

    fold_results = [
        _fit_and_score_fold(
            fold,
            label=label,
            model_name=model_name,
            requested_features=requested_features,
        )
        for fold in folds
    ]

    evaluated = [fold for fold in fold_results if not fold.get("skipped")]
    if not evaluated:
        backend = "unavailable"
        model_used = model_name
    else:
        backend = evaluated[0].get("model_backend")
        model_used = evaluated[0].get("model_backend_name")

    first_evaluated = evaluated[0] if evaluated else {
        "features": {"used_numeric": [], "used_categorical": [], "encoded_feature_count": 0}
    }

    result = {
        "experiment_context": {
            "generated_at": datetime.now().isoformat(),
            "input_path": str(input_obj),
            "label": label,
            "label_definition": LABEL_DEFINITIONS.get(label),
            "model_requested": model_name,
            "model_used": model_used,
            "backend": backend,
            "features_mode": requested_features,
            "random_cv_disallowed": True,
            "validation_policy": "walk_forward_only",
        },
        "features": {
            "requested": requested_features,
            "used_numeric": first_evaluated["features"]["used_numeric"],
            "used_categorical": first_evaluated["features"]["used_categorical"],
            "encoded_feature_count": first_evaluated["features"]["encoded_feature_count"],
        },
        "folds": fold_results,
        "aggregate": _aggregate_results(fold_results, label=label),
        "notes": [
            "Offline research only. No live integration or live-parameter mutation.",
            "Only chronological walk-forward validation is used.",
            "Interpret results as baseline evidence, not live parity.",
        ],
    }
    return result


def _default_output_path(label: str, model_name: str) -> Path:
    safe_label = label.replace("label_", "")
    return Path("logs") / f"ml_baseline_{safe_label}_{model_name}.json"


def _print_summary(result: dict[str, Any], output_path: Path, markdown_path: Path) -> None:
    aggregate = result["aggregate"]
    features = result["features"]
    print()
    print("run_ml_baseline_experiment")
    print(f"  label       : {result['experiment_context']['label']}")
    print(f"  model       : {result['experiment_context']['model_used']}")
    print(f"  backend     : {result['experiment_context']['backend']}")
    print(f"  folds       : {aggregate['evaluated_folds']} evaluated / {aggregate['skipped_folds']} skipped")
    print(f"  macro_f1    : {aggregate['macro_average']['f1']}")
    print(f"  macro_pr    : {aggregate['macro_average']['pr_auc']}")
    print(f"  macro_auc   : {aggregate['macro_average']['roc_auc']}")
    print(f"  class_bal   : {aggregate['class_balance']}")
    print(f"  features    : {features['encoded_feature_count']} encoded")
    print(f"  read        : {aggregate['research_read']}")
    print(f"  json        : {output_path}")
    print(f"  md          : {markdown_path}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a small offline supervised-learning baseline on walk-forward folds."
    )
    parser.add_argument("--input", required=True, help="Labeled dataset file or walk-forward fold directory")
    parser.add_argument(
        "--label",
        required=True,
        choices=sorted(LABEL_DEFINITIONS.keys()),
        help="Target label to evaluate",
    )
    parser.add_argument(
        "--model",
        choices=("logistic", "random_forest"),
        default="logistic",
        help="Baseline model to use",
    )
    parser.add_argument(
        "--features",
        default="auto",
        help="`auto` or comma-separated feature columns",
    )
    parser.add_argument("--output", default="", help="JSON output path")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown output path")
    parser.add_argument("--train-days", type=int, default=20, help="When input is a file, number of train dates")
    parser.add_argument("--validation-days", type=int, default=5, help="When input is a file, number of validation dates")
    parser.add_argument("--test-days", type=int, default=5, help="When input is a file, number of test dates")
    parser.add_argument("--step-days", type=int, default=5, help="When input is a file, walk-forward step size")
    args = parser.parse_args(argv)

    try:
        result = run_experiment(
            input_path=args.input,
            label=args.label,
            model_name=args.model,
            requested_features=args.features,
            train_days=args.train_days,
            validation_days=args.validation_days,
            test_days=args.test_days,
            step_days=args.step_days,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path = resolve_output_path(
        args.output,
        "json",
        default_path=_default_output_path(args.label, args.model),
    )
    markdown_path = (
        Path(args.markdown_output)
        if args.markdown_output
        else output_path.with_suffix(".md")
    )
    write_json(output_path, result)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(result), encoding="utf-8")
    _print_summary(result, output_path, markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
