"""CLI: compare_ml_label_viability

Compare offline ML label viability using labeled dataset coverage and optional
baseline experiment JSON outputs.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from app.tools.ml_research_common import LABEL_DEFINITIONS, load_rows, normalize_null, parse_bool, write_json

LABEL_ORDER: tuple[str, ...] = (
    "label_positive_eod_net_cost",
    "label_positive_30m_net_cost",
    "label_top_decile_eod",
    "label_top_decile_eod_net_cost",
    "label_final_candidate_vs_reject",
)


def _label_kind(label: str) -> str:
    if label == "label_final_candidate_vs_reject":
        return "policy"
    return "economic"


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


def _fmt_pct(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{rate * 100:.2f}%"


def _coverage_summary(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    total = len(rows)
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
        "label": label,
        "kind": _label_kind(label),
        "definition": LABEL_DEFINITIONS.get(label),
        "total_rows": total,
        "available_count": available,
        "positive_count": positive,
        "negative_count": negative,
        "missing_count": missing,
        "positive_rate": round(positive_rate, 4) if positive_rate is not None else None,
    }


def _load_baseline_reports(baseline_dir: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not baseline_dir.exists():
        return grouped

    for path in sorted(baseline_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        context = payload.get("experiment_context") or {}
        aggregate = payload.get("aggregate") or {}
        label = str(context.get("label") or "").strip()
        if not label or not isinstance(aggregate, dict):
            continue
        enriched = dict(payload)
        enriched["_path"] = str(path)
        grouped.setdefault(label, []).append(enriched)
    return grouped


def _baseline_sort_key(payload: dict[str, Any]) -> tuple[float, float, float, int]:
    aggregate = payload.get("aggregate") or {}
    macro = aggregate.get("macro_average") or {}
    pr_auc = _safe_float(macro.get("pr_auc")) or -1.0
    f1 = _safe_float(macro.get("f1")) or -1.0
    precision = _safe_float(macro.get("precision")) or -1.0
    folds = int(_safe_float(aggregate.get("evaluated_folds")) or 0)
    return (pr_auc, f1, precision, folds)


def _baseline_summary(experiments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not experiments:
        return None
    selected = max(experiments, key=_baseline_sort_key)
    aggregate = selected.get("aggregate") or {}
    macro = aggregate.get("macro_average") or {}
    micro = aggregate.get("micro_average") or {}
    context = selected.get("experiment_context") or {}
    return {
        "experiments_found": len(experiments),
        "selected_path": selected.get("_path"),
        "models_seen": sorted(
            {
                str((experiment.get("experiment_context") or {}).get("model_used") or "").strip()
                for experiment in experiments
                if str((experiment.get("experiment_context") or {}).get("model_used") or "").strip()
            }
        ),
        "backend": context.get("backend"),
        "model_used": context.get("model_used"),
        "model_requested": context.get("model_requested"),
        "evaluated_folds": int(_safe_float(aggregate.get("evaluated_folds")) or 0),
        "skipped_folds": int(_safe_float(aggregate.get("skipped_folds")) or 0),
        "class_balance": _safe_float(aggregate.get("class_balance")),
        "macro_precision": _safe_float(macro.get("precision")),
        "macro_recall": _safe_float(macro.get("recall")),
        "macro_f1": _safe_float(macro.get("f1")),
        "macro_pr_auc": _safe_float(macro.get("pr_auc")),
        "macro_roc_auc": _safe_float(macro.get("roc_auc")),
        "positive_prediction_rate": _safe_float(macro.get("positive_prediction_rate"))
        or _safe_float(micro.get("positive_prediction_rate")),
        "research_read": aggregate.get("research_read"),
    }


def _coverage_status(
    coverage: dict[str, Any],
    *,
    min_positive_count: int,
    min_available_count: int,
    min_positive_rate: float,
) -> str:
    if coverage.get("available_count") is None or coverage.get("positive_count") is None:
        return "unknown"
    if coverage["available_count"] < min_available_count:
        return "too_sparse_for_now"
    if coverage["positive_count"] < min_positive_count:
        return "too_sparse_for_now"
    positive_rate = coverage.get("positive_rate")
    if positive_rate is None or positive_rate < min_positive_rate:
        return "too_sparse_for_now"
    if coverage["positive_count"] < (min_positive_count * 2):
        return "borderline_but_usable"
    return "usable"


def _label_verdict(
    *,
    label: str,
    coverage: dict[str, Any],
    baseline: dict[str, Any] | None,
    min_positive_count: int,
    min_available_count: int,
    min_positive_rate: float,
) -> tuple[str, float, list[str]]:
    notes: list[str] = []
    kind = _label_kind(label)
    coverage_state = _coverage_status(
        coverage,
        min_positive_count=min_positive_count,
        min_available_count=min_available_count,
        min_positive_rate=min_positive_rate,
    )
    score = 0.0

    if kind == "policy":
        notes.append("policy-imitation label; keep separate from economic targets")
        score -= 0.1

    if coverage_state == "unknown":
        notes.append("dataset coverage was not supplied")
        score -= 0.05
    elif coverage_state == "too_sparse_for_now":
        notes.append("coverage is too sparse for a stable first target")
        return "too sparse for now", -1.0 if kind != "policy" else -1.2, notes
    elif coverage_state == "borderline_but_usable":
        notes.append("coverage is usable but still somewhat limited")
        score += 0.1
    else:
        notes.append("coverage looks sufficient for offline baseline work")
        score += 0.3

    if baseline is None:
        notes.append("no baseline result supplied yet")
        if kind == "policy":
            return "keep for later only", score - 0.1, notes
        return "coverage looks usable but baseline missing", score, notes

    pr_auc = baseline.get("macro_pr_auc")
    class_balance = baseline.get("class_balance")
    f1 = baseline.get("macro_f1")
    precision = baseline.get("macro_precision")
    recall = baseline.get("macro_recall")
    folds = int(baseline.get("evaluated_folds") or 0)
    research_read = str(baseline.get("research_read") or "").strip()

    uplift = None
    if pr_auc is not None and class_balance is not None:
        uplift = pr_auc - class_balance

    if folds <= 0:
        notes.append("baseline result does not contain evaluated folds")
        return "keep for later only", score - 0.2, notes
    if folds == 1:
        notes.append("only one evaluated fold; stability evidence is weak")
        score -= 0.1
    else:
        score += 0.1

    if research_read == "too little positive coverage":
        notes.append("baseline already flagged positive coverage as weak")
        return "too sparse for now", score - 0.6, notes
    if research_read == "unstable across folds":
        notes.append("baseline signal varies across folds")
        score += 0.05
        return "promising but unstable", score, notes

    if kind == "policy":
        notes.append("strong policy label can help diagnostics, but it is not the first economic ML target")
        if (f1 or 0.0) >= 0.15:
            score += 0.1
        return "keep for later only", score, notes

    if uplift is not None:
        notes.append(f"PR-AUC uplift vs class balance: {uplift:.4f}")
        score += uplift * 3.0
    if f1 is not None:
        score += f1
    if precision is not None and recall is not None and precision >= 0.6 and recall <= 0.2:
        notes.append("precision is decent but recall is still low")
        return "high precision but low recall", score, notes
    if uplift is not None and uplift >= 0.05 and (f1 or 0.0) >= 0.15:
        notes.append("simple logistic-style baseline shows usable offline lift")
        return "best first ML target candidate", score + 0.4, notes
    if uplift is not None and uplift >= 0.02 and (f1 or 0.0) >= 0.1:
        notes.append("baseline shows some lift but not yet robust")
        return "promising but unstable" if folds <= 1 else "keep for later only", score + 0.1, notes

    notes.append("baseline signal is weak relative to class balance")
    return "weak signal across folds", score - 0.2, notes


def build_report(
    *,
    dataset_path: str = "",
    baseline_dir: str = "",
    min_positive_count: int = 30,
    min_available_count: int = 100,
    min_positive_rate: float = 0.01,
) -> dict[str, Any]:
    if not dataset_path and not baseline_dir:
        raise ValueError("Provide at least one of --dataset or --baseline-dir.")

    dataset_rows: list[dict[str, Any]] = []
    if dataset_path:
        dataset_rows = load_rows(Path(dataset_path))

    baseline_groups = _load_baseline_reports(Path(baseline_dir)) if baseline_dir else {}
    labels = sorted(set(LABEL_ORDER) | set(baseline_groups.keys()))

    label_reports: list[dict[str, Any]] = []
    for label in labels:
        coverage = (
            _coverage_summary(dataset_rows, label=label)
            if dataset_rows
            else {
                "label": label,
                "kind": _label_kind(label),
                "definition": LABEL_DEFINITIONS.get(label),
                "total_rows": None,
                "available_count": None,
                "positive_count": None,
                "negative_count": None,
                "missing_count": None,
                "positive_rate": None,
            }
        )
        baseline = _baseline_summary(baseline_groups.get(label) or [])
        verdict, score, notes = _label_verdict(
            label=label,
            coverage=coverage,
            baseline=baseline,
            min_positive_count=min_positive_count,
            min_available_count=min_available_count,
            min_positive_rate=min_positive_rate,
        )
        label_reports.append(
            {
                "label": label,
                "kind": _label_kind(label),
                "coverage": coverage,
                "baseline": baseline,
                "verdict": verdict,
                "rank_score": round(score, 4),
                "notes": notes,
            }
        )

    economic_candidates = [
        item
        for item in label_reports
        if item["kind"] == "economic"
        and item["verdict"] not in {"too sparse for now", "weak signal across folds"}
    ]
    candidates_with_baseline = [
        item for item in economic_candidates if item.get("baseline") is not None
    ]
    candidates_without_baseline = [
        item for item in economic_candidates if item.get("baseline") is None
    ]
    candidates_with_baseline.sort(key=lambda item: item["rank_score"], reverse=True)
    candidates_without_baseline.sort(key=lambda item: item["rank_score"], reverse=True)

    if candidates_with_baseline and candidates_with_baseline[0]["rank_score"] > 0:
        best_first_target = candidates_with_baseline[0]["label"]
        overall_read = "best first ML target identified"
        overall_notes = candidates_with_baseline[0]["notes"]
    elif candidates_without_baseline and candidates_without_baseline[0]["rank_score"] > 0:
        best_first_target = candidates_without_baseline[0]["label"]
        overall_read = "coverage suggests the next baseline target, but evidence is still incomplete"
        overall_notes = candidates_without_baseline[0]["notes"] + [
            "Run and compare baseline experiments for this label before treating it as the first serious ML target.",
        ]
    else:
        best_first_target = None
        overall_read = "evidence is weak across labels"
        overall_notes = [
            "No economic label currently clears a strong viability threshold.",
            "Use this as a guide for the next offline data-quality step, not as a live decision signal.",
        ]

    return {
        "generated_at": datetime.now().isoformat(),
        "dataset_path": dataset_path or None,
        "baseline_dir": baseline_dir or None,
        "random_cv_disallowed": True,
        "comparison_policy": {
            "min_positive_count": min_positive_count,
            "min_available_count": min_available_count,
            "min_positive_rate": min_positive_rate,
            "baseline_selection": "best available file per label by pr_auc, f1, precision, evaluated_folds",
        },
        "labels": label_reports,
        "overall_recommendation": {
            "best_first_target": best_first_target,
            "overall_read": overall_read,
            "notes": overall_notes,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ML Label Viability Report",
        "",
        "## Context",
        "",
        f"- Dataset: `{report.get('dataset_path')}`",
        f"- Baseline dir: `{report.get('baseline_dir')}`",
        f"- Random CV disallowed: `{report.get('random_cv_disallowed')}`",
        "",
        "## Labels",
        "",
    ]
    for item in report["labels"]:
        coverage = item["coverage"]
        baseline = item.get("baseline") or {}
        lines.append(f"### `{item['label']}`")
        lines.append("")
        lines.append(f"- Kind: `{item['kind']}`")
        lines.append(
            f"- Coverage: positives={coverage.get('positive_count')}, negatives={coverage.get('negative_count')}, "
            f"missing={coverage.get('missing_count')}, positive_rate={coverage.get('positive_rate')}"
        )
        if baseline:
            lines.append(
                f"- Baseline: folds={baseline.get('evaluated_folds')}, "
                f"macro_f1={baseline.get('macro_f1')}, pr_auc={baseline.get('macro_pr_auc')}, "
                f"roc_auc={baseline.get('macro_roc_auc')}, research_read={baseline.get('research_read')}"
            )
        else:
            lines.append("- Baseline: not supplied")
        lines.append(f"- Verdict: `{item['verdict']}`")
        for note in item["notes"]:
            lines.append(f"- Note: {note}")
        lines.append("")
    lines.extend(
        [
            "## Recommendation",
            "",
            f"- Best first target: `{report['overall_recommendation']['best_first_target']}`",
            f"- Overall read: `{report['overall_recommendation']['overall_read']}`",
        ]
    )
    for note in report["overall_recommendation"]["notes"]:
        lines.append(f"- Note: {note}")
    lines.append("")
    return "\n".join(lines)


def _print_report(report: dict[str, Any]) -> None:
    print()
    print("compare_ml_label_viability")
    if report.get("dataset_path"):
        print("  Coverage")
        print("  label                          positives  negatives  missing   pos_rate")
        for item in report["labels"]:
            coverage = item["coverage"]
            print(
                f"  {item['label']:<30} "
                f"{str(coverage.get('positive_count')):>9} "
                f"{str(coverage.get('negative_count')):>9} "
                f"{str(coverage.get('missing_count')):>8} "
                f"{_fmt_pct(coverage.get('positive_rate')):>10}"
            )
        print()
    if report.get("baseline_dir"):
        print("  Baselines")
        print("  label                          folds   f1     pr_auc roc_auc read")
        for item in report["labels"]:
            baseline = item.get("baseline") or {}
            if not baseline:
                continue
            print(
                f"  {item['label']:<30} "
                f"{str(baseline.get('evaluated_folds')):>5} "
                f"{str(baseline.get('macro_f1')):>6} "
                f"{str(baseline.get('macro_pr_auc')):>6} "
                f"{str(baseline.get('macro_roc_auc')):>7} "
                f"{str(baseline.get('research_read') or 'n/a')}"
            )
        print()
    print("  Recommendation")
    print(f"  best_first_target : {report['overall_recommendation']['best_first_target'] or 'none'}")
    print(f"  overall_read      : {report['overall_recommendation']['overall_read']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare label viability using coverage and existing baseline outputs."
    )
    parser.add_argument("--dataset", default="", help="Labeled ML dataset file")
    parser.add_argument("--baseline-dir", default="", help="Directory of baseline experiment JSON files")
    parser.add_argument("--output", default="", help="JSON output path")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown output path")
    parser.add_argument("--min-positive-count", type=int, default=30, help="Minimum positives for viable label coverage")
    parser.add_argument("--min-available-count", type=int, default=100, help="Minimum labeled rows for viable coverage")
    parser.add_argument("--min-positive-rate", type=float, default=0.01, help="Minimum positive rate for viable coverage")
    args = parser.parse_args(argv)

    try:
        report = build_report(
            dataset_path=args.dataset,
            baseline_dir=args.baseline_dir,
            min_positive_count=args.min_positive_count,
            min_available_count=args.min_available_count,
            min_positive_rate=args.min_positive_rate,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else Path("logs/ml_label_viability_report.json")
    markdown_path = Path(args.markdown_output) if args.markdown_output else output_path.with_suffix(".md")
    write_json(output_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
