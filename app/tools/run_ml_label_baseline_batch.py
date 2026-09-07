"""CLI: run_ml_label_baseline_batch

Run the existing offline baseline experiment across multiple labeled datasets
and summarize how stable one label looks across windows.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import glob
from pathlib import Path
import sys
from typing import Any

from app.tools.ml_research_common import LABEL_DEFINITIONS, load_rows, parse_bool, resolve_output_path, write_json
from app.tools.run_ml_baseline_experiment import run_experiment


def _resolve_input_paths(inputs: list[str], glob_pattern: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    for raw in inputs:
        path = Path(raw)
        key = str(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)

    if glob_pattern:
        for raw in sorted(glob.glob(glob_pattern)):
            path = Path(raw)
            key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)

    return paths


def _label_coverage(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    positive = 0
    negative = 0
    missing = 0
    for row in rows:
        parsed = parse_bool(row.get(label))
        if parsed is None:
            missing += 1
        elif parsed:
            positive += 1
        else:
            negative += 1
    available = positive + negative
    positive_rate = (positive / available) if available else None
    return {
        "total_rows": len(rows),
        "positive_count": positive,
        "negative_count": negative,
        "missing_count": missing,
        "available_count": available,
        "positive_rate": round(positive_rate, 4) if positive_rate is not None else None,
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _mean(values: list[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    return round(sum(items) / len(items), 4)


def _std(values: list[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    if len(items) == 1:
        return 0.0
    mean = sum(items) / len(items)
    variance = sum((value - mean) ** 2 for value in items) / len(items)
    return round(variance ** 0.5, 4)


def _window_stability_read(windows: list[dict[str, Any]], *, label: str) -> tuple[str, list[str]]:
    successful = [window for window in windows if window["status"] == "ok"]
    failed = [window for window in windows if window["status"] != "ok"]
    notes: list[str] = []

    if not successful:
        return "still too sparse / low-confidence", [
            "No window produced a usable baseline result.",
        ]

    class_balances = [window["coverage"].get("positive_rate") for window in successful]
    macro_f1 = [window.get("macro_f1") for window in successful]
    precision = [window.get("macro_precision") for window in successful]
    recall = [window.get("macro_recall") for window in successful]
    pr_auc = [window.get("macro_pr_auc") for window in successful]
    evaluated_folds = sum(int(window.get("evaluated_folds") or 0) for window in successful)
    per_window_folds = [int(window.get("evaluated_folds") or 0) for window in successful]
    research_reads = [str(window.get("research_read") or "") for window in successful]
    mean_balance = _mean(class_balances) or 0.0
    mean_pr_auc = _mean(pr_auc)
    mean_f1 = _mean(macro_f1) or 0.0
    mean_precision = _mean(precision) or 0.0
    mean_recall = _mean(recall) or 0.0
    f1_std = _std(macro_f1) or 0.0
    uplift = None if mean_pr_auc is None else round(mean_pr_auc - mean_balance, 4)

    if evaluated_folds <= 1 or len(successful) == 1:
        notes.append("Only one successful window is available so stability evidence is weak.")
        return "still too sparse / low-confidence", notes

    if evaluated_folds < 3 or all(folds <= 1 for folds in per_window_folds):
        notes.append("Each window still carries too few evaluated folds for a strong stability claim.")
        return "still too sparse / low-confidence", notes

    if research_reads and sum(1 for read in research_reads if read == "too little positive coverage") >= max(1, len(research_reads) // 2):
        notes.append("Most successful windows were already flagged as low-coverage baselines.")
        return "still too sparse / low-confidence", notes

    if mean_balance < 0.02:
        notes.append("Positive coverage stays very low across windows.")
        return "still too sparse / low-confidence", notes

    if mean_precision >= 0.5 and mean_recall <= 0.2:
        notes.append("Precision is usable but recall stays weak across windows.")
        return "decent precision, weak recall", notes

    if uplift is not None and uplift >= 0.05 and mean_f1 >= 0.15 and f1_std <= 0.08 and not failed:
        notes.append("PR-AUC stays meaningfully above class balance across windows.")
        notes.append("Macro F1 is modest but stable enough to justify the next offline ML step.")
        return "stable enough for next ML step", notes

    if uplift is not None and uplift >= 0.02 and mean_f1 >= 0.1:
        notes.append("There is some offline lift but it varies across windows.")
        if failed:
            notes.append("A few windows failed or were too short, which weakens confidence.")
        return "promising but unstable", notes

    notes.append(f"Signal remains weak for `{label}` across the current windows.")
    return "weak across windows", notes


def build_batch_report(
    *,
    input_paths: list[Path],
    label: str,
    model: str,
    features: str,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []

    for path in input_paths:
        rows = load_rows(path)
        coverage = _label_coverage(rows, label)
        window_result: dict[str, Any] = {
            "window": path.stem,
            "input_path": str(path),
            "coverage": coverage,
        }
        try:
            result = run_experiment(
                input_path=str(path),
                label=label,
                model_name=model,
                requested_features=features,
                train_days=train_days,
                validation_days=validation_days,
                test_days=test_days,
                step_days=step_days,
            )
            aggregate = result.get("aggregate") or {}
            macro = aggregate.get("macro_average") or {}
            window_result.update(
                {
                    "status": "ok",
                    "evaluated_folds": int(_safe_float(aggregate.get("evaluated_folds")) or 0),
                    "skipped_folds": int(_safe_float(aggregate.get("skipped_folds")) or 0),
                    "class_balance": _safe_float(aggregate.get("class_balance")),
                    "macro_precision": _safe_float(macro.get("precision")),
                    "macro_recall": _safe_float(macro.get("recall")),
                    "macro_f1": _safe_float(macro.get("f1")),
                    "macro_pr_auc": _safe_float(macro.get("pr_auc")),
                    "macro_roc_auc": _safe_float(macro.get("roc_auc")),
                    "positive_prediction_rate": _safe_float(macro.get("positive_prediction_rate")),
                    "research_read": aggregate.get("research_read"),
                    "result": result,
                }
            )
        except Exception as exc:
            window_result.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "evaluated_folds": 0,
                    "skipped_folds": 0,
                    "class_balance": coverage.get("positive_rate"),
                    "macro_precision": None,
                    "macro_recall": None,
                    "macro_f1": None,
                    "macro_pr_auc": None,
                    "macro_roc_auc": None,
                    "positive_prediction_rate": None,
                    "research_read": "failed window",
                }
            )
        windows.append(window_result)

    successful = [window for window in windows if window["status"] == "ok"]
    failed = [window for window in windows if window["status"] != "ok"]
    stability_read, stability_notes = _window_stability_read(windows, label=label)

    aggregate = {
        "windows_requested": len(windows),
        "windows_succeeded": len(successful),
        "windows_failed": len(failed),
        "total_evaluated_folds": sum(int(window.get("evaluated_folds") or 0) for window in successful),
        "mean_class_balance": _mean([window.get("class_balance") for window in successful]),
        "mean_macro_precision": _mean([window.get("macro_precision") for window in successful]),
        "mean_macro_recall": _mean([window.get("macro_recall") for window in successful]),
        "mean_macro_f1": _mean([window.get("macro_f1") for window in successful]),
        "mean_macro_pr_auc": _mean([window.get("macro_pr_auc") for window in successful]),
        "mean_macro_roc_auc": _mean([window.get("macro_roc_auc") for window in successful]),
        "mean_positive_prediction_rate": _mean([window.get("positive_prediction_rate") for window in successful]),
        "std_macro_f1": _std([window.get("macro_f1") for window in successful]),
        "std_macro_pr_auc": _std([window.get("macro_pr_auc") for window in successful]),
        "research_read_counts": dict(Counter(str(window.get("research_read") or "n/a") for window in successful)),
        "stability_read": stability_read,
        "stability_notes": stability_notes,
    }

    return {
        "generated_at": datetime.now().isoformat(),
        "label": label,
        "label_definition": LABEL_DEFINITIONS.get(label),
        "model": model,
        "features": features,
        "random_cv_disallowed": True,
        "validation_policy": "walk_forward_only",
        "parameters": {
            "train_days": train_days,
            "validation_days": validation_days,
            "test_days": test_days,
            "step_days": step_days,
        },
        "windows": windows,
        "aggregate": aggregate,
    }


def render_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# ML Label Baseline Batch Report",
        "",
        "## Context",
        "",
        f"- Label: `{report['label']}`",
        f"- Model: `{report['model']}`",
        f"- Features: `{report['features']}`",
        f"- Random CV disallowed: `{report['random_cv_disallowed']}`",
        "",
        "## Per Window",
        "",
    ]
    for window in report["windows"]:
        coverage = window["coverage"]
        if window["status"] != "ok":
            lines.append(
                f"- `{window['window']}`: failed, positives={coverage['positive_count']}, "
                f"missing={coverage['missing_count']}, error={window.get('error')}"
            )
            continue
        lines.append(
            f"- `{window['window']}`: folds={window['evaluated_folds']}, "
            f"pos_rate={coverage['positive_rate']}, f1={window['macro_f1']}, "
            f"pr_auc={window['macro_pr_auc']}, roc_auc={window['macro_roc_auc']}, "
            f"read={window['research_read']}"
        )
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Windows succeeded: `{aggregate['windows_succeeded']}` / `{aggregate['windows_requested']}`",
            f"- Total evaluated folds: `{aggregate['total_evaluated_folds']}`",
            f"- Mean class balance: `{aggregate['mean_class_balance']}`",
            f"- Mean precision: `{aggregate['mean_macro_precision']}`",
            f"- Mean recall: `{aggregate['mean_macro_recall']}`",
            f"- Mean macro F1: `{aggregate['mean_macro_f1']}`",
            f"- Mean PR-AUC: `{aggregate['mean_macro_pr_auc']}`",
            f"- Mean ROC-AUC: `{aggregate['mean_macro_roc_auc']}`",
            f"- Stability read: `{aggregate['stability_read']}`",
        ]
    )
    for note in aggregate["stability_notes"]:
        lines.append(f"- Note: {note}")
    lines.append("")
    return "\n".join(lines)


def _print_report(report: dict[str, Any]) -> None:
    print()
    print("run_ml_label_baseline_batch")
    print("  Windows")
    print("  window                          pos_rate  folds   f1     pr_auc roc_auc read")
    for window in report["windows"]:
        coverage = window["coverage"]
        pos_rate = coverage.get("positive_rate")
        pos_rate_text = "n/a" if pos_rate is None else f"{pos_rate * 100:.2f}%"
        if window["status"] != "ok":
            print(
                f"  {window['window']:<30} {pos_rate_text:>8} "
                f"{str(window.get('evaluated_folds')):>5} {'-':>6} {'-':>6} {'-':>7} failed"
            )
            continue
        print(
            f"  {window['window']:<30} {pos_rate_text:>8} "
            f"{str(window.get('evaluated_folds')):>5} "
            f"{str(window.get('macro_f1')):>6} "
            f"{str(window.get('macro_pr_auc')):>6} "
            f"{str(window.get('macro_roc_auc')):>7} "
            f"{str(window.get('research_read') or 'n/a')}"
        )
    print()
    aggregate = report["aggregate"]
    print("  Aggregate")
    print(f"  windows_succeeded : {aggregate['windows_succeeded']}/{aggregate['windows_requested']}")
    print(f"  total_folds       : {aggregate['total_evaluated_folds']}")
    print(f"  mean_macro_f1     : {aggregate['mean_macro_f1']}")
    print(f"  mean_macro_pr_auc : {aggregate['mean_macro_pr_auc']}")
    print(f"  mean_macro_roc_auc: {aggregate['mean_macro_roc_auc']}")
    print(f"  class_balance     : {aggregate['mean_class_balance']}")
    print(f"  stability_read    : {aggregate['stability_read']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the same offline baseline across multiple labeled datasets."
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Repeatable labeled dataset path",
    )
    parser.add_argument(
        "--glob",
        default="",
        help="Glob for labeled dataset files",
    )
    parser.add_argument(
        "--label",
        default="label_positive_30m_net_cost",
        choices=sorted(LABEL_DEFINITIONS.keys()),
        help="Label to evaluate across windows",
    )
    parser.add_argument(
        "--model",
        default="logistic",
        choices=("logistic", "random_forest"),
        help="Baseline model to reuse",
    )
    parser.add_argument(
        "--features",
        default="auto",
        help="Feature mode passed through to run_ml_baseline_experiment",
    )
    parser.add_argument("--output", default="", help="JSON report output path")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown report path")
    parser.add_argument("--train-days", type=int, default=20, help="Train dates per window")
    parser.add_argument("--validation-days", type=int, default=5, help="Validation dates per window")
    parser.add_argument("--test-days", type=int, default=5, help="Test dates per window")
    parser.add_argument("--step-days", type=int, default=5, help="Walk-forward step days")
    args = parser.parse_args(argv)

    input_paths = _resolve_input_paths(args.input, args.glob)
    if not input_paths:
        print("No labeled dataset inputs matched the requested --input/--glob.", file=sys.stderr)
        return 1

    report = build_batch_report(
        input_paths=input_paths,
        label=args.label,
        model=args.model,
        features=args.features,
        train_days=args.train_days,
        validation_days=args.validation_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )

    output_path = resolve_output_path(
        args.output,
        "json",
        default_path=Path("logs") / f"{args.label}_batch_report.json",
    )
    markdown_path = Path(args.markdown_output) if args.markdown_output else output_path.with_suffix(".md")
    write_json(output_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
