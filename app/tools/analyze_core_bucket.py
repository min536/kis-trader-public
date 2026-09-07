"""CLI: analyze_core_bucket

Diagnostic tooling to explain why the `core` bucket reaches deep_eval
but does not convert into final candidates, by comparing core deep-eval
losers against non-core final candidates using score-composition data.

Usage:
    python3 -m app.tools.analyze_core_bucket --date 20260403 --account mock_12345678_01
    python3 -m app.tools.analyze_core_bucket --date 20260403 --account mock_12345678_01 --session REGULAR
    python3 -m app.tools.analyze_core_bucket --date 20260403 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.tools.core_bucket_analysis import (
    _build_candidate_detail_index,
    _build_human_summary,
    _coalesce_metric,
    _component_profile,
    _enrich_rows,
    _infer_required_pass_count,
    _metric_stats,
    _passed_count_distribution,
    _pattern_passing_rules,
    _pct,
    _rule_contrast_interpretation,
    _rule_lift_opportunities,
    _safe_float,
    _safe_mean,
    _safe_median,
    _sample_rows,
    _shadow_analysis,
    _strategy_hit_rate,
    _strategy_rate_map,
    _symbol_failure_diagnosis,
    _threshold_simulation,
    _top_fail_patterns,
    _top_symbol_rows,
)

from app.tools.core_bucket_console import (
    _dim,
    _format_metric_row,
    _header,
    _ok,
    _print_rule_lift_opportunities,
    _print_shadow_section,
    _print_symbol_failure_diagnosis,
    _print_threshold_simulation,
    _warn,
    print_terminal,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWN_BUCKETS = ("core", "rotating", "exploration")


def _candidate_log_path(account: str, date: str) -> Path:
    return PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    return PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"File not found: {path}")
        return rows, errors
    with path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"Line {lineno}: JSON parse error – {exc}")
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                errors.append(f"Line {lineno}: not a JSON object")
    return rows, errors


def _filter_session(rows: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    if not session:
        return rows
    target = session.upper()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        direct_session = str(row.get("session", "")).upper()
        market_session = row.get("market_session")
        if isinstance(market_session, dict):
            nested_session = str(market_session.get("session", "")).upper()
        else:
            nested_session = str(market_session or "").upper()
        if direct_session == target or nested_session == target:
            filtered.append(row)
    return filtered


_SHADOW_RULE_ORDER: tuple[str, ...] = (
    "non_overextension",
    "macd_momentum_confirmed",
    "intraday_stability",
)


def run_analysis(account: str, date: str, session: str) -> dict[str, Any]:
    candidate_path = _candidate_log_path(account, date)
    snapshot_path = _cycle_snapshots_path(account)
    candidate_rows, candidate_errors = _load_rows(candidate_path)
    snapshot_rows, snapshot_errors = _load_rows(snapshot_path)

    if not candidate_path.exists():
        return {"error": f"File not found: {candidate_path}"}

    rows = _filter_session(candidate_rows, session)
    snapshot_rows = _filter_session(snapshot_rows, session)
    detail_index = _build_candidate_detail_index(snapshot_rows)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = str(row.get("selection_bucket") or "UNKNOWN")
        buckets[bucket].append(row)

    bucket_stats: dict[str, Any] = {}
    for bucket_name, bucket_rows in buckets.items():
        pre_gate_rej = sum(
            1
            for row in bucket_rows
            if row.get("stage_reached") == "pre_gate_rejected"
            or row.get("pre_gate_passed") is False
        )
        deep_eval = sum(1 for row in bucket_rows if row.get("deep_evaluated") is True)
        fin_cand = sum(1 for row in bucket_rows if row.get("final_candidate") is True)
        exec_cnt = sum(1 for row in bucket_rows if row.get("executed") is True)
        deep_scores = [
            float(row["score_deep"])
            for row in bucket_rows
            if row.get("deep_evaluated") and isinstance(row.get("score_deep"), (int, float))
        ]
        bucket_stats[bucket_name] = {
            "total_rows": len(bucket_rows),
            "pre_gate_rejected": pre_gate_rej,
            "deep_eval": deep_eval,
            "final_candidate": fin_cand,
            "executed": exec_cnt,
            "score_deep": {
                "avg": _safe_mean(deep_scores),
                "median": _safe_median(deep_scores),
                "min": min(deep_scores) if deep_scores else 0.0,
                "max": max(deep_scores) if deep_scores else 0.0,
                "count": len(deep_scores),
            },
        }

    core_deep_losers = [
        row
        for row in rows
        if row.get("selection_bucket") == "core"
        and row.get("deep_evaluated") is True
        and row.get("final_candidate") is not True
    ]
    non_core_final_candidates = [
        row
        for row in rows
        if row.get("selection_bucket") != "core"
        and row.get("final_candidate") is True
    ]

    enriched_core_losers = _enrich_rows(core_deep_losers, detail_index)
    enriched_non_core_finals = _enrich_rows(non_core_final_candidates, detail_index)

    core_loser_reasons = Counter(
        str(row.get("rejection_reason"))
        for row in core_deep_losers
        if row.get("rejection_reason") is not None
    )
    core_loser_outcomes = Counter(
        str(row.get("selection_outcome"))
        for row in core_deep_losers
        if row.get("selection_outcome") is not None
    )
    non_core_final_buckets = Counter(
        str(row.get("selection_bucket"))
        for row in non_core_final_candidates
        if row.get("selection_bucket") is not None
    )

    core_profile = _component_profile(enriched_core_losers)
    non_core_profile = _component_profile(enriched_non_core_finals)

    core_strategy_hit_rate = _strategy_hit_rate(core_deep_losers)
    non_core_strategy_hit_rate = _strategy_hit_rate(non_core_final_candidates)

    passed_count_dist = _passed_count_distribution(core_deep_losers)
    top_fail_patterns = _top_fail_patterns(core_deep_losers)
    inferred_rpc = _infer_required_pass_count(core_deep_losers)
    rule_contrast = _rule_contrast_interpretation(
        core_strategy_hit_rate,
        non_core_strategy_hit_rate,
        core_deep_losers,
        inferred_rpc,
    )
    threshold_simulation = _threshold_simulation(core_deep_losers, inferred_rpc)
    rule_lift_opportunities = _rule_lift_opportunities(
        core_deep_losers,
        inferred_rpc,
        non_core_shr=non_core_strategy_hit_rate,
    )
    symbol_failure_diagnosis = _symbol_failure_diagnosis(
        core_deep_losers,
        inferred_rpc,
        structural_gap_rules=list(rule_contrast.get("structural_gap_rules") or []),
        non_core_shr=non_core_strategy_hit_rate,
    )

    return {
        "account": account,
        "date": date,
        "session": session or "ALL",
        "candidate_file": str(candidate_path),
        "cycle_snapshots_file": str(snapshot_path),
        "total_rows": len(rows),
        "errors": candidate_errors + snapshot_errors,
        "bucket_stats": bucket_stats,
        "core_losers_stats": {
            "count": len(core_deep_losers),
            "top_symbols": _top_symbol_rows(core_deep_losers),
            "reasons": dict(core_loser_reasons),
            "outcomes": dict(core_loser_outcomes),
            "samples": _sample_rows(enriched_core_losers),
        },
        "non_core_final_stats": {
            "count": len(non_core_final_candidates),
            "top_symbols": _top_symbol_rows(non_core_final_candidates),
            "buckets": dict(non_core_final_buckets),
            "samples": _sample_rows(enriched_non_core_finals),
        },
        "composition_comparison": {
            "core_deep_eval_losers": core_profile,
            "non_core_final_candidates": non_core_profile,
        },
        "human_summary": _build_human_summary(core_profile, non_core_profile),
        "strategy_hit_rate": {
            "core_deep_eval_losers": core_strategy_hit_rate,
            "non_core_final_candidates": non_core_strategy_hit_rate,
        },
        "passed_count_distribution": passed_count_dist,
        "top_fail_patterns": top_fail_patterns,
        "rule_contrast_interpretation": rule_contrast,
        "threshold_simulation": threshold_simulation,
        "rule_lift_opportunities": rule_lift_opportunities,
        "symbol_failure_diagnosis": symbol_failure_diagnosis,
        "shadow_analysis": _shadow_analysis(core_deep_losers),
    }





def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze why the core bucket loses after deep_eval."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_analysis(args.account, args.date, args.session)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_terminal(result)


if __name__ == "__main__":
    main()
