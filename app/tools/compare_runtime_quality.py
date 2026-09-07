"""CLI: compare_runtime_quality

Compare two trading days using existing operational logs.

Primary sources:
- cycle_stats_*.jsonl
- orders_*.jsonl
- app stdout log (best effort, optional for day-accurate parsing)

Usage:
    python3 -m app.tools.compare_runtime_quality \
        --account mock_12345678_01 \
        --baseline-date 20260417 \
        --target-date 20260418

    python3 -m app.tools.compare_runtime_quality \
        --account mock_12345678_01 \
        --baseline-date 20260417 \
        --target-date 20260418 \
        --stdout-a logs/app_stdout_20260417.log \
        --stdout-b logs/app_stdout_20260418.log
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RATE_LIMIT_ACTIONS = {
    "rate_limit_detected_buy_scan",
    "rate_limit_detected_sell_watch",
    "skipped_buy_scan_budget_limited",
    "sell_watch_partial_budget_protection",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def _cycle_stats_path(root: Path, account: str, date: str) -> Path:
    return root / "logs" / f"cycle_stats_{account}_{date}.jsonl"


def _orders_path(root: Path, account: str) -> Path:
    return root / "logs" / f"orders_{account}.jsonl"


def _same_date(text: object, date: str) -> bool:
    normalized = str(text or "").strip().replace("-", "")
    return normalized.startswith(date)


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_cycle_rows(root: Path, account: str, date: str) -> list[dict[str, Any]]:
    return _load_jsonl(_cycle_stats_path(root, account, date))


def _load_order_rows(root: Path, account: str, date: str) -> list[dict[str, Any]]:
    rows = _load_jsonl(_orders_path(root, account))
    return [row for row in rows if _same_date(row.get("timestamp") or row.get("ts"), date)]


def _analyze_stdout(stdout_path: Path | None, *, date: str) -> dict[str, Any]:
    if stdout_path is None or not stdout_path.exists():
        return {
            "live_snapshot_usage_count": None,
            "stale_or_invalid_fallback_count": None,
            "hard_stop_ready_occurrence_count_stdout": None,
            "stdout_scope": "missing",
        }

    live_snapshot_usage_count = 0
    stale_fallback_count = 0
    hard_stop_ready_count = 0
    date_token = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    exact_date_lines = 0

    with stdout_path.open(encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if date_token in line:
                exact_date_lines += 1
            if "buy universe source=live_snapshot" in line and date_token in line:
                live_snapshot_usage_count += 1
            if "fallback_reason=stale_or_invalid" in line:
                stale_fallback_count += 1
            if "daily pnl brake: HARD_STOP_READY" in line:
                hard_stop_ready_count += 1

    return {
        "live_snapshot_usage_count": live_snapshot_usage_count,
        "stale_or_invalid_fallback_count": stale_fallback_count,
        "hard_stop_ready_occurrence_count_stdout": hard_stop_ready_count,
        "stdout_scope": "date_filtered" if exact_date_lines > 0 else "file_wide_best_effort",
    }


def _default_stdout_path(root: Path, date: str) -> Path | None:
    candidate = root / "logs" / f"app_stdout_{date}.log"
    if candidate.exists():
        return candidate
    return None


def build_day_metrics(
    *,
    root: Path,
    account: str,
    date: str,
    stdout_path: Path | None = None,
) -> dict[str, Any]:
    cycle_rows = _load_cycle_rows(root, account, date)
    order_rows = _load_order_rows(root, account, date)

    action_counts: Counter[str] = Counter()
    affected_cycle_ids: set[str] = set()
    executed_order_count = 0

    for row in order_rows:
        action = str(row.get("action") or "").strip()
        cycle_id = str(row.get("cycle_id") or "").strip()
        if action:
            action_counts[action] += 1
        if action in RATE_LIMIT_ACTIONS and cycle_id:
            affected_cycle_ids.add(cycle_id)
        if (
            str(row.get("result") or "").strip() == "success"
            and action in {"order_succeeded", "sell_order_succeeded"}
        ):
            executed_order_count += 1

    shortlist_count = sum(_safe_int(row.get("shallow_shortlist_size")) for row in cycle_rows)
    deep_eval_count = sum(_safe_int(row.get("deep_eval_count")) for row in cycle_rows)
    successful_cycles = sum(
        1
        for row in cycle_rows
        if (
            _safe_int(row.get("final_candidate_count")) > 0
            or _safe_int(row.get("executed_order_count")) > 0
            or _safe_int(row.get("sell_triggered_count")) > 0
        )
    )
    hard_stop_ready_occurrence_count = sum(
        1
        for row in cycle_rows
        if _safe_int(row.get("pre_gate_rejected_daily_pnl_brake")) > 0
    )
    deep_eval_utilization_ratio = (
        deep_eval_count / shortlist_count if shortlist_count > 0 else 0.0
    )

    stdout_metrics = _analyze_stdout(stdout_path, date=date)

    return {
        "date": date,
        "cycles": len(cycle_rows),
        "affected_cycles_by_rate_limit_or_budget_protection": len(affected_cycle_ids),
        "rate_limit_detected_buy_scan": _safe_int(action_counts.get("rate_limit_detected_buy_scan")),
        "rate_limit_detected_sell_watch": _safe_int(action_counts.get("rate_limit_detected_sell_watch")),
        "skipped_buy_scan_budget_limited": _safe_int(action_counts.get("skipped_buy_scan_budget_limited")),
        "sell_watch_partial_budget_protection": _safe_int(action_counts.get("sell_watch_partial_budget_protection")),
        "shortlist_count": shortlist_count,
        "deep_eval_count": deep_eval_count,
        "deep_eval_utilization_ratio": deep_eval_utilization_ratio,
        "successful_cycles": successful_cycles,
        "executed_order_count": executed_order_count,
        "live_snapshot_usage_count": stdout_metrics["live_snapshot_usage_count"],
        "stale_or_invalid_fallback_count": stdout_metrics["stale_or_invalid_fallback_count"],
        "hard_stop_ready_occurrence_count": (
            stdout_metrics["hard_stop_ready_occurrence_count_stdout"]
            if stdout_metrics["hard_stop_ready_occurrence_count_stdout"] is not None
            else hard_stop_ready_occurrence_count
        ),
        "hard_stop_blocked_cycle_count": hard_stop_ready_occurrence_count,
        "stdout_scope": stdout_metrics["stdout_scope"],
    }


def _format_value(metric: str, value: object) -> str:
    if value is None:
        return "n/a"
    if metric == "deep_eval_utilization_ratio":
        return f"{_safe_float(value) * 100:.1f}%"
    return f"{value}"


def _delta(metric: str, baseline: object, target: object) -> str:
    if baseline is None or target is None:
        return "n/a"
    if metric == "deep_eval_utilization_ratio":
        diff = (_safe_float(target) - _safe_float(baseline)) * 100.0
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}pp"
    diff = _safe_int(target) - _safe_int(baseline)
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff}"


def assess_comparison(
    baseline_metrics: dict[str, Any],
    target_metrics: dict[str, Any],
) -> dict[str, Any]:
    target_shortlist = _safe_int(target_metrics.get("shortlist_count"))
    target_deep_eval = _safe_int(target_metrics.get("deep_eval_count"))
    target_successful_cycles = _safe_int(target_metrics.get("successful_cycles"))
    target_executed_orders = _safe_int(target_metrics.get("executed_order_count"))
    target_cycles = _safe_int(target_metrics.get("cycles"))
    target_affected_cycles = _safe_int(
        target_metrics.get("affected_cycles_by_rate_limit_or_budget_protection")
    )
    baseline_hard_stop = _safe_int(
        baseline_metrics.get("hard_stop_ready_occurrence_count")
    )
    target_hard_stop = _safe_int(
        target_metrics.get("hard_stop_ready_occurrence_count")
    )
    baseline_buy_rl = _safe_int(
        baseline_metrics.get("rate_limit_detected_buy_scan")
    ) + _safe_int(baseline_metrics.get("skipped_buy_scan_budget_limited"))
    target_buy_rl = _safe_int(
        target_metrics.get("rate_limit_detected_buy_scan")
    ) + _safe_int(target_metrics.get("skipped_buy_scan_budget_limited"))

    issues: list[str] = []
    regression_highlights: list[str] = []

    comparable = True
    if target_shortlist == 0:
        comparable = False
        issues.append("shortlist_count == 0")
    if target_deep_eval == 0:
        comparable = False
        issues.append("deep_eval_count == 0")
    if target_successful_cycles == 0:
        comparable = False
        issues.append("successful_cycles == 0")
    if target_executed_orders == 0:
        comparable = False
        issues.append("executed_order_count == 0")

    stdout_available = (
        target_metrics.get("live_snapshot_usage_count") is not None
        and target_metrics.get("stale_or_invalid_fallback_count") is not None
    )
    if not stdout_available:
        issues.append("live snapshot stdout metrics unavailable")

    if comparable and target_cycles > 0 and target_affected_cycles == 0:
        confidence = "high"
    elif comparable:
        confidence = "medium"
    else:
        confidence = "low"

    false_hard_stop_verdict = "insufficient evidence"
    if baseline_hard_stop > 0 and target_hard_stop < baseline_hard_stop:
        false_hard_stop_verdict = (
            "appears improved" if confidence != "low" else "looks improved but low confidence"
        )
    elif target_hard_stop > baseline_hard_stop:
        false_hard_stop_verdict = "possible regression"
        regression_highlights.append(
            "hard_stop_ready_count increased versus baseline"
        )

    buy_scan_verdict = "insufficient evidence"
    if comparable and target_buy_rl < baseline_buy_rl:
        buy_scan_verdict = "appears improved"
    elif not comparable and target_buy_rl < baseline_buy_rl:
        buy_scan_verdict = "looks improved but low confidence"
    elif target_buy_rl > baseline_buy_rl:
        buy_scan_verdict = "possible regression"
        regression_highlights.append(
            "buy-scan rate-limit/budget pressure increased versus baseline"
        )

    live_snapshot_verdict = "unavailable"
    baseline_live_usage = baseline_metrics.get("live_snapshot_usage_count")
    target_live_usage = target_metrics.get("live_snapshot_usage_count")
    baseline_stale = baseline_metrics.get("stale_or_invalid_fallback_count")
    target_stale = target_metrics.get("stale_or_invalid_fallback_count")
    if (
        baseline_live_usage is not None
        and target_live_usage is not None
        and baseline_stale is not None
        and target_stale is not None
    ):
        if _safe_int(target_live_usage) > _safe_int(baseline_live_usage) and _safe_int(target_stale) <= _safe_int(baseline_stale):
            live_snapshot_verdict = "appears improved"
        elif _safe_int(target_stale) > _safe_int(baseline_stale):
            live_snapshot_verdict = "possible regression"
            regression_highlights.append(
                "stale_or_invalid fallback count increased versus baseline"
            )
        else:
            live_snapshot_verdict = "mixed / inconclusive"

    return {
        "comparable_regular_session_day": comparable,
        "confidence": confidence,
        "issues": tuple(issues),
        "regression_highlights": tuple(regression_highlights),
        "false_hard_stop_verdict": false_hard_stop_verdict,
        "buy_scan_bottleneck_verdict": buy_scan_verdict,
        "live_snapshot_persistence_verdict": live_snapshot_verdict,
    }


def render_comparison(
    baseline_metrics: dict[str, Any],
    target_metrics: dict[str, Any],
) -> str:
    assessment = assess_comparison(baseline_metrics, target_metrics)
    metric_labels = [
        ("cycles", "cycles"),
        ("affected_cycles_by_rate_limit_or_budget_protection", "affected_cycles"),
        ("rate_limit_detected_buy_scan", "rate_limit_buy_scan"),
        ("rate_limit_detected_sell_watch", "rate_limit_sell_watch"),
        ("skipped_buy_scan_budget_limited", "buy_scan_budget_limited"),
        ("sell_watch_partial_budget_protection", "sell_watch_partial_protect"),
        ("shortlist_count", "shortlist_count"),
        ("deep_eval_count", "deep_eval_count"),
        ("deep_eval_utilization_ratio", "deep_eval_utilization"),
        ("successful_cycles", "successful_cycles"),
        ("executed_order_count", "executed_order_count"),
        ("live_snapshot_usage_count", "live_snapshot_usage"),
        ("stale_or_invalid_fallback_count", "stale_fallback_count"),
        ("hard_stop_ready_occurrence_count", "hard_stop_ready_count"),
    ]

    label_width = max(len(label) for _, label in metric_labels)
    base_header = baseline_metrics["date"]
    target_header = target_metrics["date"]
    value_width = max(len(base_header), len(target_header), 10)

    lines = [
        "=== Runtime Quality Comparison ===",
        f"baseline={baseline_metrics['date']} | target={target_metrics['date']}",
        (
            f"{'metric':<{label_width}}  "
            f"{base_header:>{value_width}}  "
            f"{target_header:>{value_width}}  "
            f"{'delta':>10}"
        ),
        (
            f"{'-' * label_width}  "
            f"{'-' * value_width}  "
            f"{'-' * value_width}  "
            f"{'-' * 10}"
        ),
    ]

    for metric, label in metric_labels:
        baseline_value = _format_value(metric, baseline_metrics.get(metric))
        target_value = _format_value(metric, target_metrics.get(metric))
        delta = _delta(metric, baseline_metrics.get(metric), target_metrics.get(metric))
        lines.append(
            f"{label:<{label_width}}  "
            f"{baseline_value:>{value_width}}  "
            f"{target_value:>{value_width}}  "
            f"{delta:>10}"
        )

    lines.extend(
        [
            "",
            "assessment:",
            (
                "  comparable_regular_session_day="
                f"{'yes' if assessment['comparable_regular_session_day'] else 'no'}"
            ),
            f"  confidence={assessment['confidence']}",
        ]
    )

    issues = tuple(assessment.get("issues", ()))
    if issues:
        lines.append("  evidence_limits:")
        for issue in issues:
            lines.append(f"    - {issue}")

    lines.extend(
        [
            "",
            "verdict:",
            f"  false hard-stop behavior improved? {assessment['false_hard_stop_verdict']}",
            f"  buy-scan bottleneck pressure improved? {assessment['buy_scan_bottleneck_verdict']}",
            f"  live snapshot persistence improved? {assessment['live_snapshot_persistence_verdict']}",
        ]
    )

    regression_highlights = tuple(assessment.get("regression_highlights", ()))
    if regression_highlights:
        lines.append("  regression_highlights:")
        for item in regression_highlights:
            lines.append(f"    - {item}")

    lines.extend(
        [
            "",
            "notes:",
            (
                f"  baseline stdout scope={baseline_metrics.get('stdout_scope') or 'unknown'}"
            ),
            f"  target stdout scope={target_metrics.get('stdout_scope') or 'unknown'}",
            (
                "  hard_stop_ready_count prefers stdout when day-scoped parsing is available; "
                "otherwise it falls back to cycle-level daily_pnl_brake blocked cycles."
            ),
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare runtime quality across two trading days")
    parser.add_argument("--account", required=True, help="account signature, e.g. mock_12345678_01")
    parser.add_argument("--baseline-date", required=True, help="baseline date YYYYMMDD")
    parser.add_argument("--target-date", required=True, help="target date YYYYMMDD")
    parser.add_argument("--stdout-a", default="", help="optional baseline stdout file")
    parser.add_argument("--stdout-b", default="", help="optional target stdout file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stdout_a = (
        Path(args.stdout_a)
        if str(args.stdout_a).strip()
        else _default_stdout_path(PROJECT_ROOT, args.baseline_date)
    )
    stdout_b = (
        Path(args.stdout_b)
        if str(args.stdout_b).strip()
        else _default_stdout_path(PROJECT_ROOT, args.target_date)
    )

    baseline_metrics = build_day_metrics(
        root=PROJECT_ROOT,
        account=args.account,
        date=args.baseline_date,
        stdout_path=stdout_a,
    )
    target_metrics = build_day_metrics(
        root=PROJECT_ROOT,
        account=args.account,
        date=args.target_date,
        stdout_path=stdout_b,
    )
    print(render_comparison(baseline_metrics, target_metrics))


if __name__ == "__main__":
    main()
