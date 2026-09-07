"""Recommend score/pass-count guards from candidate outcome logs.

Usage:
    python3 -m app.tools.recommend_score_guards \
        --account mock_12345678_01 \
        --dates 20260416,20260417,20260421
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KNOWN_BUCKETS = ("core", "rotating", "exploration")


def _log_path(account: str, date_text: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date_text}.jsonl"


def _parse_dates(raw: str) -> list[str]:
    dates: list[str] = []
    for chunk in (raw or "").split(","):
        token = chunk.strip()
        if not token:
            continue
        dates.append(token.replace("-", ""))
    return dates


def _load_rows(account: str, dates: list[str], session: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    target_session = session.upper().strip()
    for date_text in dates:
        path = _log_path(account, date_text)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if target_session and str(payload.get("session") or "").upper() != target_session:
                    continue
                payload = dict(payload)
                payload["date"] = date_text
                rows.append(payload)
    return rows


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _ceil_to_tenth(value: float) -> float:
    return round(math.ceil(value * 10.0) / 10.0, 1)


def _score_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [
        float(score)
        for score in (_safe_float(row.get("score_deep")) for row in rows)
        if score is not None
    ]
    passed_counts = [
        int(count)
        for count in (_safe_int(row.get("passed_count_deep")) for row in rows)
        if count is not None
    ]
    return {
        "count": len(rows),
        "score_count": len(scores),
        "score_min": (None if not scores else round(min(scores), 2)),
        "score_median": (
            None
            if not scores
            else round(sorted(scores)[len(scores) // 2], 2)
        ),
        "score_max": (None if not scores else round(max(scores), 2)),
        "passed_count_distribution": dict(sorted(Counter(passed_counts).items())),
    }


def _recommend_guard(
    *,
    positive_rows: list[dict[str, Any]],
    score_blocked_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    positive_scores = [
        float(score)
        for score in (_safe_float(row.get("score_deep")) for row in positive_rows)
        if score is not None
    ]
    score_blocked_scores = [
        float(score)
        for score in (_safe_float(row.get("score_deep")) for row in score_blocked_rows)
        if score is not None
    ]
    positive_pass_counts = [
        int(count)
        for count in (_safe_int(row.get("passed_count_deep")) for row in positive_rows)
        if count is not None
    ]

    if not positive_scores or not score_blocked_scores or not positive_pass_counts:
        return {
            "buy_min_score": None,
            "buy_rule_required_pass_count": None,
            "score_gap_exists": False,
            "score_gap": None,
        }

    max_negative = max(score_blocked_scores)
    min_positive = min(positive_scores)
    score_gap = round(min_positive - max_negative, 2)

    # Conservative threshold: just above the top blocked sample.
    recommended_score = _ceil_to_tenth(max_negative + 0.05)
    if recommended_score >= min_positive:
        recommended_score = round(max_negative, 2)

    return {
        "buy_min_score": recommended_score,
        "buy_rule_required_pass_count": min(positive_pass_counts),
        "score_gap_exists": min_positive > max_negative,
        "score_gap": score_gap,
        "max_blocked_score": round(max_negative, 2),
        "min_positive_score": round(min_positive, 2),
    }


def build_recommendation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deep_rows = [row for row in rows if bool(row.get("deep_evaluated"))]

    def _bucket_rows(bucket: str) -> list[dict[str, Any]]:
        if bucket == "overall":
            return list(deep_rows)
        return [row for row in deep_rows if str(row.get("selection_bucket") or "") == bucket]

    buckets = ("overall",) + _KNOWN_BUCKETS
    bucket_payloads: dict[str, Any] = {}

    for bucket in buckets:
        bucket_rows = _bucket_rows(bucket)
        positive_rows = [
            row for row in bucket_rows
            if bool(row.get("executed")) or bool(row.get("final_candidate"))
        ]
        score_blocked_rows = [
            row for row in bucket_rows
            if str(row.get("rejection_reason") or "") == "score_below_threshold"
        ]
        profit_buffer_rows = [
            row for row in bucket_rows
            if str(row.get("rejection_reason") or "") == "profit_buffer_insufficient"
        ]
        passed_count_rows = [
            row for row in bucket_rows
            if str(row.get("rejection_reason") or "") == "passed_count_insufficient"
        ]
        bucket_payloads[bucket] = {
            "positive": _score_stats(positive_rows),
            "score_blocked": _score_stats(score_blocked_rows),
            "profit_buffer_insufficient": _score_stats(profit_buffer_rows),
            "passed_count_insufficient": _score_stats(passed_count_rows),
            "recommendation": _recommend_guard(
                positive_rows=positive_rows,
                score_blocked_rows=score_blocked_rows,
            ),
        }

    recommended_settings: dict[str, Any] = {}
    overall = bucket_payloads["overall"]["recommendation"]
    if overall.get("buy_rule_required_pass_count") is not None:
        recommended_settings["buy_rule_required_pass_count"] = overall["buy_rule_required_pass_count"]
    if overall.get("buy_min_score") is not None:
        recommended_settings["buy_min_score"] = overall["buy_min_score"]

    core = bucket_payloads["core"]["recommendation"]
    if core.get("buy_rule_required_pass_count") is not None:
        recommended_settings["buy_rule_required_pass_count_core"] = core["buy_rule_required_pass_count"]
    if core.get("buy_min_score") is not None:
        recommended_settings["buy_min_score_core"] = core["buy_min_score"]

    return {
        "row_count": len(rows),
        "deep_row_count": len(deep_rows),
        "buckets": bucket_payloads,
        "recommended_settings_overrides": recommended_settings,
    }


def _render_text(
    *,
    account: str,
    dates: list[str],
    summary: dict[str, Any],
) -> str:
    lines = [
        "recommend_score_guards",
        f"  account     : {account}",
        f"  dates       : {', '.join(dates)}",
        f"  rows        : {summary['row_count']}",
        f"  deep rows   : {summary['deep_row_count']}",
        "",
    ]

    for bucket_name in ("overall", "core", "rotating"):
        bucket = dict((summary.get("buckets") or {}).get(bucket_name) or {})
        rec = dict(bucket.get("recommendation") or {})
        lines.append(f"[{bucket_name}]")
        lines.append(
            "  positive        : "
            f"{bucket.get('positive', {}).get('count')} rows | "
            f"scores {bucket.get('positive', {}).get('score_min')} -> {bucket.get('positive', {}).get('score_max')}"
        )
        lines.append(
            "  score_blocked   : "
            f"{bucket.get('score_blocked', {}).get('count')} rows | "
            f"scores {bucket.get('score_blocked', {}).get('score_min')} -> {bucket.get('score_blocked', {}).get('score_max')}"
        )
        lines.append(
            "  pass_insuff     : "
            f"{bucket.get('passed_count_insufficient', {}).get('count')} rows"
        )
        lines.append(
            "  profit_buffer   : "
            f"{bucket.get('profit_buffer_insufficient', {}).get('count')} rows"
        )
        lines.append(
            "  recommendation  : "
            f"buy_min_score={rec.get('buy_min_score')} "
            f"buy_rule_required_pass_count={rec.get('buy_rule_required_pass_count')} "
            f"score_gap={rec.get('score_gap')}"
        )
        lines.append("")

    lines.append("settings_overrides:")
    for key, value in (summary.get("recommended_settings_overrides") or {}).items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recommend score/pass-count guards from candidate outcome logs.")
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument("--dates", required=True, help="Comma-separated dates (YYYYMMDD or YYYY-MM-DD)")
    parser.add_argument("--session", default="REGULAR", help="Session filter (default: REGULAR)")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = parser.parse_args(argv)

    dates = _parse_dates(args.dates)
    rows = _load_rows(account=args.account, dates=dates, session=args.session)
    summary = build_recommendation_summary(rows)
    payload = {
        "account": args.account,
        "dates": dates,
        "session": args.session.upper().strip(),
        **summary,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(account=args.account, dates=dates, summary=summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
