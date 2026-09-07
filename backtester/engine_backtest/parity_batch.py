"""Batch summarization utilities for parity reports."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from backtester.engine_backtest.parity import build_parity_report

_STAGE_TO_GROUP = {
    "capacity_blocked": "capacity_issue_dates",
    "pre_rule_blocked": "pre_rule_issue_dates",
    "rule_blocked": "rule_issue_dates",
    "score_blocked": "score_issue_dates",
    "sizing_blocked": "sizing_issue_dates",
    "executed_buy": "executed_buy_dates",
    "unknown_zero_buy": "unknown_dates",
}

_GROUP_LABELS = {
    "capacity_issue_dates": "capacity issue dates",
    "pre_rule_issue_dates": "pre-rule issue dates",
    "rule_issue_dates": "rule issue dates",
    "score_issue_dates": "score issue dates",
    "sizing_issue_dates": "sizing issue dates",
    "executed_buy_dates": "executed buy dates",
    "unknown_dates": "unknown dates",
}

_ROW_FIELDS = [
    "account",
    "date",
    "parity_level",
    "engine_source_mode",
    "stage",
    "available_slots_before_buy_pass",
    "positions_before_buy_pass",
    "max_positions",
    "seeded_position_count",
    "buy_signal_count",
    "buy_scored_candidate_count",
    "executed_buy_count",
    "dominant_rule_failure",
    "dominant_rule_fail_count",
    "dominant_rule_pass_rate",
    "dominant_rule_failure_share",
    "rule_failure_profile",
    "min_score_threshold",
    "signal_score_max",
    "rejected_score_max",
    "candidate_score_max",
    "selected_buy_score",
    "sizing_block_reason",
    "primary_rejection_reason",
    "score_tuning_label",
    "score_tuning_reason",
]

_RULE_DOMINANCE_SHARE_THRESHOLD = 0.6


def _normalize_date_text(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _parse_dates_arg(raw: str) -> list[str]:
    return [
        _normalize_date_text(part)
        for part in str(raw or "").split(",")
        if str(part).strip()
    ]


def _expand_date_range(start: str, end: str) -> list[str]:
    start_iso = _normalize_date_text(start)
    end_iso = _normalize_date_text(end)
    start_date = date.fromisoformat(start_iso)
    end_date = date.fromisoformat(end_iso)
    if end_date < start_date:
        raise ValueError("date_to must be on or after date_from")
    dates: list[str] = []
    cursor = start_date
    while cursor <= end_date:
        dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return dates


def _parse_paths_arg(raw: str) -> list[Path]:
    return [
        Path(part.strip())
        for part in str(raw or "").split(",")
        if str(part).strip()
    ]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Parity report is not a JSON object: {path}")
    return payload


def _discover_report_paths(report_dir: str | Path) -> list[Path]:
    root = Path(report_dir)
    if not root.exists():
        raise FileNotFoundError(f"Report directory not found: {root}")
    return sorted(
        path for path in root.glob("parity_*.json")
        if path.is_file()
    )


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _score_bucket_max(bucket: dict[str, Any] | None) -> float | None:
    if not isinstance(bucket, dict):
        return None
    return _safe_float(bucket.get("max"))


def _dominant_rule_text(dominant_rule_failure: dict[str, Any] | None) -> str:
    if not isinstance(dominant_rule_failure, dict):
        return "-"
    rule = str(dominant_rule_failure.get("rule") or "").strip() or "-"
    fail_count = _safe_int(dominant_rule_failure.get("fail_count")) or 0
    pass_rate = dominant_rule_failure.get("pass_rate")
    if pass_rate is None:
        return f"{rule}(fail={fail_count})"
    return f"{rule}(fail={fail_count},pass_rate={pass_rate})"


def _recommend_score_tuning(row: dict[str, Any]) -> tuple[str, str]:
    stage = str(row.get("stage") or "").strip()
    if stage == "score_blocked":
        return "include_primary", "score threshold/normalization 분석 우선 대상"
    if stage == "executed_buy":
        return "include_primary", "positive reference set"
    if stage == "rule_blocked":
        share = _safe_float(row.get("dominant_rule_failure_share")) or 0.0
        if row.get("dominant_rule_failure") and share >= _RULE_DOMINANCE_SHARE_THRESHOLD:
            return "include_conditional", "dominant rule failure가 강해 rule 병목 분석용 조건부 포함"
        return "exclude", "rule block이지만 지배 규칙 정보가 부족해 제외"
    if stage == "capacity_blocked":
        return "exclude", "capacity block 날짜는 score tuning에서 제외"
    if stage == "pre_rule_blocked":
        return "exclude", "pre-rule block 날짜는 score tuning에서 제외"
    if stage == "sizing_blocked":
        return "exclude", "sizing block 날짜는 score tuning에서 제외"
    return "exclude", "원인 불명 또는 비점수 병목이라 제외"


def build_parity_batch_row(report: dict[str, Any]) -> dict[str, Any]:
    engine = dict(((report.get("summary_counts") or {}).get("engine_backtest")) or {})
    diagnostics = dict(engine.get("buy_diagnostics") or {})
    capacity = dict(engine.get("buy_capacity") or {})
    score_stats = dict(engine.get("buy_score_stats") or {})
    sizing = dict(engine.get("buy_sizing") or {})
    dominant_rule_failure = engine.get("dominant_rule_failure")
    rule_fail_counts = {
        str(name): _safe_int(count) or 0
        for name, count in dict(engine.get("buy_rule_fail_counts") or {}).items()
    }
    total_rule_failures = sum(rule_fail_counts.values())
    dominant_rule_fail_count = (
        _safe_int((dominant_rule_failure or {}).get("fail_count"))
        if isinstance(dominant_rule_failure, dict)
        else None
    )
    if total_rule_failures <= 0 and dominant_rule_fail_count is not None and dominant_rule_fail_count > 0:
        total_rule_failures = dominant_rule_fail_count
    dominant_rule_failure_share = (
        round((dominant_rule_fail_count or 0) / total_rule_failures, 4)
        if total_rule_failures > 0 and dominant_rule_fail_count is not None
        else None
    )
    rule_failure_profile = None
    if str(diagnostics.get("stage") or "") == "rule_blocked":
        if dominant_rule_failure_share is not None and dominant_rule_failure_share >= _RULE_DOMINANCE_SHARE_THRESHOLD:
            rule_failure_profile = "strong_dominant_failure"
        else:
            rule_failure_profile = "mixed_failures"

    row = {
        "account": str(report.get("account") or ""),
        "date": str(((report.get("date_range") or {}).get("start")) or ""),
        "parity_level": str(report.get("parity_level") or ""),
        "engine_source_mode": str(((report.get("engine_backtest_source") or {}).get("mode")) or ""),
        "stage": str(diagnostics.get("stage") or "unknown_zero_buy"),
        "available_slots_before_buy_pass": _safe_int(capacity.get("available_slots_before_buy_pass")),
        "positions_before_buy_pass": _safe_int(capacity.get("positions_before_buy_pass")),
        "max_positions": _safe_int(capacity.get("max_positions")),
        "seeded_position_count": _safe_int(engine.get("seeded_position_count")),
        "buy_signal_count": _safe_int(engine.get("buy_signal_count")) or 0,
        "buy_scored_candidate_count": _safe_int(engine.get("buy_scored_candidate_count")) or 0,
        "executed_buy_count": _safe_int(engine.get("executed_buy_count")) or 0,
        "dominant_rule_failure": _dominant_rule_text(
            dominant_rule_failure if isinstance(dominant_rule_failure, dict) else None
        ),
        "dominant_rule_fail_count": dominant_rule_fail_count,
        "dominant_rule_pass_rate": (
            _safe_float((dominant_rule_failure or {}).get("pass_rate"))
            if isinstance(dominant_rule_failure, dict)
            else None
        ),
        "dominant_rule_failure_share": dominant_rule_failure_share,
        "rule_failure_profile": rule_failure_profile,
        "min_score_threshold": _safe_float(score_stats.get("min_score_threshold")),
        "signal_score_max": _score_bucket_max(score_stats.get("signal_scores")),
        "rejected_score_max": _score_bucket_max(score_stats.get("score_rejected")),
        "candidate_score_max": _score_bucket_max(score_stats.get("scored_candidates")),
        "selected_buy_score": _safe_float(engine.get("selected_buy_score")),
        "sizing_block_reason": str(sizing.get("block_reason_code") or "").strip() or None,
        "primary_rejection_reason": str(diagnostics.get("primary_rejection_reason") or "").strip() or None,
    }
    tuning_label, tuning_reason = _recommend_score_tuning(row)
    row["score_tuning_label"] = tuning_label
    row["score_tuning_reason"] = tuning_reason
    return row


def build_parity_batch_summary(
    reports: list[dict[str, Any]],
    *,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    rows = sorted(
        (build_parity_batch_row(report) for report in reports),
        key=lambda row: str(row.get("date") or ""),
    )
    stage_groups = {key: [] for key in _GROUP_LABELS}
    stage_counts = {key: 0 for key in _GROUP_LABELS}
    score_tuning = {
        "include_primary_dates": [],
        "include_conditional_dates": [],
        "excluded_dates": [],
    }

    for row in rows:
        stage = str(row.get("stage") or "unknown_zero_buy")
        group_key = _STAGE_TO_GROUP.get(stage, "unknown_dates")
        stage_groups[group_key].append(str(row.get("date") or ""))
        stage_counts[group_key] += 1

        tuning_label = str(row.get("score_tuning_label") or "")
        if tuning_label == "include_primary":
            score_tuning["include_primary_dates"].append(str(row.get("date") or ""))
        elif tuning_label == "include_conditional":
            score_tuning["include_conditional_dates"].append(str(row.get("date") or ""))
        else:
            score_tuning["excluded_dates"].append(str(row.get("date") or ""))

    summary = {
        "generated_at": datetime.now().isoformat(),
        "row_count": len(rows),
        "rows": rows,
        "stage_groups": stage_groups,
        "stage_counts": stage_counts,
        "score_tuning_candidates": {
            **score_tuning,
            "recommended_dates": (
                list(score_tuning["include_primary_dates"])
                + list(score_tuning["include_conditional_dates"])
            ),
        },
        "errors": list(errors or []),
    }
    return summary


def collect_parity_reports(
    *,
    account: str | None = None,
    dates: list[str] | None = None,
    session: str = "REGULAR",
    initial_cash: int | None = None,
    report_paths: list[Path] | None = None,
    report_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    discovered_paths: list[Path] = []
    if report_dir:
        discovered_paths.extend(_discover_report_paths(report_dir))
    if report_paths:
        discovered_paths.extend(report_paths)

    for path in discovered_paths:
        try:
            reports.append(_load_json(path))
        except Exception as exc:
            errors.append({"source": str(path), "error": str(exc)})

    for raw_date in dates or []:
        iso_date = _normalize_date_text(raw_date)
        if not account:
            errors.append({
                "source": iso_date,
                "error": "account is required when building parity reports from dates",
            })
            continue
        try:
            reports.append(
                build_parity_report(
                    account=account,
                    date=iso_date,
                    session=session,
                    initial_cash=initial_cash,
                )
            )
        except Exception as exc:
            errors.append({"source": iso_date, "error": str(exc)})

    deduped_by_date: dict[str, dict[str, Any]] = {}
    for report in reports:
        date_text = str(((report.get("date_range") or {}).get("start")) or "").strip()
        if not date_text:
            continue
        deduped_by_date[date_text] = report

    deduped_reports = [deduped_by_date[key] for key in sorted(deduped_by_date)]
    return deduped_reports, errors


def build_parity_batch_from_sources(
    *,
    account: str | None = None,
    dates: list[str] | None = None,
    session: str = "REGULAR",
    initial_cash: int | None = None,
    report_paths: list[Path] | None = None,
    report_dir: str | Path | None = None,
) -> dict[str, Any]:
    reports, errors = collect_parity_reports(
        account=account,
        dates=dates,
        session=session,
        initial_cash=initial_cash,
        report_paths=report_paths,
        report_dir=report_dir,
    )
    if not reports and errors:
        first = errors[0]
        raise FileNotFoundError(f"No usable parity reports found: {first['error']}")
    return build_parity_batch_summary(reports, errors=errors)


def render_parity_batch_console(summary: dict[str, Any]) -> str:
    rows = list(summary.get("rows") or [])
    stage_groups = dict(summary.get("stage_groups") or {})
    score_tuning = dict(summary.get("score_tuning_candidates") or {})
    errors = list(summary.get("errors") or [])

    def _fmt(value: Any, width: int) -> str:
        text = "-" if value in (None, "") else str(value)
        if len(text) > width:
            return text[: width - 1] + "~"
        return text.ljust(width)

    lines = [
        "Parity Batch Summary",
        f"rows: {summary.get('row_count', 0)}",
        "",
    ]

    if rows:
        header = (
            f"{_fmt('date', 10)} "
            f"{_fmt('stage', 17)} "
            f"{_fmt('slots', 5)} "
            f"{_fmt('sig', 4)} "
            f"{_fmt('cand', 4)} "
            f"{_fmt('buy', 3)} "
            f"{_fmt('dom_rule', 28)} "
            f"{_fmt('dom_share', 9)} "
            f"{_fmt('score', 19)} "
            f"{_fmt('size_reason', 18)} "
            f"{_fmt('tuning', 17)}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for row in rows:
            score_text = (
                f"thr={row.get('min_score_threshold')},"
                f"rej={row.get('rejected_score_max')},"
                f"sel={row.get('selected_buy_score')}"
            )
            lines.append(
                f"{_fmt(row.get('date'), 10)} "
                f"{_fmt(row.get('stage'), 17)} "
                f"{_fmt(row.get('available_slots_before_buy_pass'), 5)} "
                f"{_fmt(row.get('buy_signal_count'), 4)} "
                f"{_fmt(row.get('buy_scored_candidate_count'), 4)} "
                f"{_fmt(row.get('executed_buy_count'), 3)} "
                f"{_fmt(row.get('dominant_rule_failure'), 28)} "
                f"{_fmt(row.get('dominant_rule_failure_share'), 9)} "
                f"{_fmt(score_text, 19)} "
                f"{_fmt(row.get('sizing_block_reason'), 18)} "
                f"{_fmt(row.get('score_tuning_label'), 17)}"
            )
        lines.append("")

    lines.append("Stage Groups")
    for key, label in _GROUP_LABELS.items():
        lines.append(f"- {label}: {stage_groups.get(key, [])}")

    lines.extend([
        "",
        "Score Tuning Candidates",
        f"- include_primary: {score_tuning.get('include_primary_dates', [])}",
        f"- include_conditional: {score_tuning.get('include_conditional_dates', [])}",
        f"- excluded: {score_tuning.get('excluded_dates', [])}",
        f"- recommended_dates: {score_tuning.get('recommended_dates', [])}",
    ])

    if errors:
        lines.extend(["", "Errors"])
        for item in errors:
            lines.append(f"- {item.get('source')}: {item.get('error')}")

    return "\n".join(lines)


def write_parity_batch_outputs(summary: dict[str, Any], *, output_base: str | Path) -> tuple[Path, Path]:
    base = Path(output_base)
    if base.suffix:
        json_path = base.with_suffix(".json")
        csv_path = base.with_suffix(".csv")
    else:
        json_path = Path(str(base) + ".json")
        csv_path = Path(str(base) + ".csv")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_ROW_FIELDS)
        writer.writeheader()
        for row in summary.get("rows") or []:
            writer.writerow({field: row.get(field) for field in _ROW_FIELDS})

    return json_path, csv_path


__all__ = [
    "build_parity_batch_from_sources",
    "build_parity_batch_row",
    "build_parity_batch_summary",
    "collect_parity_reports",
    "render_parity_batch_console",
    "write_parity_batch_outputs",
    "_parse_dates_arg",
    "_expand_date_range",
    "_parse_paths_arg",
]
