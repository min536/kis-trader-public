"""Pure post-run diagnostic report builders."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from app.tools.analyze_candidate_logs import (
    KNOWN_BUCKETS,
    SEMANTIC_REJECTION_REASONS,
    STAGE_ORDER,
    _filter_cycle_ids,
    _filter_session,
    _load_rows,
    _log_path,
    run_analysis as run_candidate_analysis,
)
from app.tools.analyze_budget_bottlenecks import run_analysis as run_budget_analysis
from app.tools.analyze_technical_feature_activation import (
    run_analysis as run_technical_activation_analysis,
)
from app.tools.validate_candidate_logs import run_validation

_LEGACY_BAD_PATTERN_IDS = frozenset({
    "residual_reason_mismatch",
    "deep_eval_other_reason",
    "deep_eval_missing_selection_outcome",
})



_BN_STRATEGY = "strategy_rule_insufficiency"
_BN_SCORE = "score_threshold_bottleneck"
_BN_COST = "cost_profit_bottleneck"
_BN_COLDSTART = "data_history_cold_start"
_BN_NONE = "none_core_is_converting"
_BN_INCONCLUSIVE = "inconclusive"

_BN_HINTS: dict[str, str] = {
    _BN_STRATEGY: (
        "core 종목이 전략 규칙을 충분히 통과하지 못합니다 (passed_count 미달). "
        "analyze_core_bucket으로 전략별 hit율을 확인하거나 "
        "내일 로그에서 passed_count 분포를 점검하세요."
    ),
    _BN_SCORE: (
        "전략은 통과했지만 최종 score가 선택 임계값 미만입니다. "
        "analyze_core_bucket으로 score 성분 분포(보너스/패널티)를 확인하세요."
    ),
    _BN_COST: (
        "cost 또는 profit_buffer 필터가 core 매수를 차단하고 있습니다. "
        "analyze_core_bucket으로 cost_bps 분포와 cost_block_reason을 확인하세요."
    ),
    _BN_COLDSTART: (
        "기술적 피처(EMA/MACD)의 히스토리 데이터가 부족합니다 (26개 관측값 미만). "
        "다음 거래일 이후 재실행하면 hist_ok율이 올라갑니다."
    ),
    _BN_NONE: "core 버킷이 정상적으로 final_candidate를 생성하고 있습니다.",
    _BN_INCONCLUSIVE: (
        "rejection 이유가 구 코드 'other'로 기록되어 있어 원인 판별 불가입니다. "
        "새 코드로 생성된 로그에서 재실행하거나 "
        "analyze_core_bucket으로 score 성분을 직접 비교하세요."
    ),
}

# Each entry is a list of {"label": str, "cmd": str} dicts.
# {date}, {account}, {session_arg}, {last_n_arg} are substituted at render time.
# session_arg = " --session SESSION" when session is set; last_n_arg = " --last-n-cycles N" when N>0.
_BN_NEXT_STEPS: dict[str, list[dict[str, str]]] = {
    _BN_STRATEGY: [
        {
            "label": "core 버킷 passed_count / score 성분 분해",
            "cmd": (
                "python3 -m app.tools.analyze_core_bucket"
                " --date {date} --account {account}{session_arg}"
            ),
        },
    ],
    _BN_SCORE: [
        {
            "label": "deep_eval → final_candidate 병목 상세",
            "cmd": (
                "python3 -m app.tools.analyze_selection_bottleneck"
                " --date {date} --account {account}{session_arg}"
            ),
        },
        {
            "label": "core vs non-core score 성분 비교",
            "cmd": (
                "python3 -m app.tools.analyze_core_bucket"
                " --date {date} --account {account}{session_arg}"
            ),
        },
    ],
    _BN_COST: [
        {
            "label": "core 버킷 cost / profit_buffer 거절 분포",
            "cmd": (
                "python3 -m app.tools.analyze_candidate_logs"
                " --date {date} --account {account}{session_arg}"
                " --bucket core{last_n_arg}"
            ),
        },
        {
            "label": "core cost_bps 분포 및 cost_block_reason 비교",
            "cmd": (
                "python3 -m app.tools.analyze_core_bucket"
                " --date {date} --account {account}{session_arg}"
            ),
        },
    ],
    _BN_COLDSTART: [
        {
            "label": "기술 피처 히스토리 / 활성화율 상세 확인",
            "cmd": (
                "python3 -m app.tools.analyze_technical_feature_activation"
                " --date {date} --account {account}{session_arg}{last_n_arg}"
            ),
        },
        {
            "label": "내일 장 종료 후 재실행 (히스토리 누적 후)",
            "cmd": (
                "python3 -m app.tools.postrun_diagnostics"
                " --date NEXT_DATE --account {account}{session_arg}{last_n_arg}"
            ),
        },
    ],
    _BN_INCONCLUSIVE: [
        {
            "label": "로그 무결성 / bad-pattern 검증",
            "cmd": (
                "python3 -m app.tools.validate_candidate_logs"
                " --date {date} --account {account}{session_arg}{last_n_arg}"
            ),
        },
        {
            "label": "core 버킷 원시 거절 이유 분포",
            "cmd": (
                "python3 -m app.tools.analyze_candidate_logs"
                " --date {date} --account {account}{session_arg}"
                " --bucket core{last_n_arg}"
            ),
        },
    ],
    _BN_NONE: [
        {
            "label": "전체 세션 활동 현황 확인",
            "cmd": (
                "python3 -m app.tools.analyze_candidate_logs"
                " --date {date} --account {account}{session_arg}{last_n_arg}"
            ),
        },
    ],
}



def _compact_counter(counter: dict[str, Any], *, limit: int = 6) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for key, value in counter.items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        pairs.append((str(key), count))
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return pairs[:limit]


def _build_filtered_core_bucket_summary(
    *,
    account: str,
    date: str,
    session: str,
    selected_cycle_ids: list[str],
) -> dict[str, Any]:
    path = _log_path(account, date)
    rows, errors = _load_rows(path)
    rows = _filter_session(rows, session)
    if selected_cycle_ids:
        rows = _filter_cycle_ids(rows, set(selected_cycle_ids))

    core_losers = [
        row
        for row in rows
        if row.get("selection_bucket") == "core"
        and row.get("deep_evaluated") is True
        and row.get("final_candidate") is not True
    ]
    non_core_finals = [
        row
        for row in rows
        if row.get("selection_bucket") != "core"
        and row.get("final_candidate") is True
    ]
    core_reason_counts = Counter(
        str(row.get("rejection_reason"))
        for row in core_losers
        if row.get("rejection_reason") is not None
    )

    def _top_symbols(rows_to_count: list[dict[str, Any]]) -> list[dict[str, Any]]:
        symbol_counts = Counter(
            str(row.get("symbol") or "")
            for row in rows_to_count
            if str(row.get("symbol") or "").strip()
        )
        named_rows: dict[str, dict[str, Any]] = {}
        for row in rows_to_count:
            symbol = str(row.get("symbol") or "").strip()
            if symbol and symbol not in named_rows:
                named_rows[symbol] = row
        return [
            {
                "symbol": symbol,
                "symbol_name": named_rows.get(symbol, {}).get("symbol_name"),
                "count": count,
            }
            for symbol, count in symbol_counts.most_common(5)
        ]

    avg_core_score = 0.0
    if core_losers:
        core_scores = [
            float(row.get("score_deep") or 0.0)
            for row in core_losers
            if isinstance(row.get("score_deep"), (int, float))
        ]
        if core_scores:
            avg_core_score = round(sum(core_scores) / len(core_scores), 3)

    avg_non_core_final_score = 0.0
    if non_core_finals:
        non_core_scores = [
            float(row.get("score_deep") or 0.0)
            for row in non_core_finals
            if isinstance(row.get("score_deep"), (int, float))
        ]
        if non_core_scores:
            avg_non_core_final_score = round(sum(non_core_scores) / len(non_core_scores), 3)

    summary_bits: list[str] = []
    if core_reason_counts:
        reason, count = core_reason_counts.most_common(1)[0]
        summary_bits.append(f"top core rejection={reason}({count})")
    if avg_non_core_final_score > 0.0 or avg_core_score > 0.0:
        summary_bits.append(
            f"avg score_deep {avg_core_score:.2f} vs {avg_non_core_final_score:.2f}"
        )

    return {
        "core_loser_count": len(core_losers),
        "non_core_final_count": len(non_core_finals),
        "top_core_symbols": _top_symbols(core_losers),
        "top_non_core_final_symbols": _top_symbols(non_core_finals),
        "core_rejection_reasons": dict(core_reason_counts),
        "avg_core_loser_score_deep": avg_core_score,
        "avg_non_core_final_score_deep": avg_non_core_final_score,
        "human_summary": ", ".join(summary_bits) if summary_bits else "",
        "load_warnings": errors,
    }


def build_report(account: str, date: str, session: str, last_n_cycles: int) -> dict[str, Any]:
    candidate = run_candidate_analysis(
        account=account,
        date=date,
        session=session,
        bucket="",
        last_n=last_n_cycles,
    )
    validation = run_validation(
        account=account,
        date=date,
        session=session,
        last_n=last_n_cycles,
    )
    technical = run_technical_activation_analysis(
        account=account,
        date=date,
        session=session,
        bucket="",
        last_n=last_n_cycles,
    )
    budget = run_budget_analysis(
        account=account,
        date=date,
        session=session,
    )
    selected_cycle_ids = candidate.get("meta", {}).get("selected_cycle_ids", [])
    core_bucket = _build_filtered_core_bucket_summary(
        account=account,
        date=date,
        session=session,
        selected_cycle_ids=selected_cycle_ids,
    )

    bad_pattern_counts = {
        check_id: int(payload.get("count", 0) or 0)
        for check_id, payload in validation.get("checks", {}).items()
    }
    bad_pattern_total = sum(bad_pattern_counts.values())

    rejection_distribution = dict(
        _compact_counter(candidate.get("distributions", {}).get("rejection_reasons", {}), limit=8)
    )

    # Per-bucket final candidate / executed counts from candidate analysis
    bucket_summary_raw = candidate.get("bucket_summary", {}) or {}
    bucket_final_exec = {
        b: {
            "total": int((bucket_summary_raw.get(b) or {}).get("total", 0) or 0),
            "deep_eval": int(
                ((bucket_summary_raw.get(b) or {}).get("stages") or {}).get("deep_eval", 0) or 0
            ),
            "final_candidate": int(
                (bucket_summary_raw.get(b) or {}).get(
                    "final_candidate_count",
                    ((bucket_summary_raw.get(b) or {}).get("stages") or {}).get("final_candidate", 0),
                )
                or 0
            ),
            "executed": int(
                (bucket_summary_raw.get(b) or {}).get(
                    "executed_count",
                    ((bucket_summary_raw.get(b) or {}).get("stages") or {}).get("executed", 0),
                )
                or 0
            ),
            "avg_score": round(
                float((bucket_summary_raw.get(b) or {}).get("avg_deep_score", 0.0) or 0.0), 2
            ),
        }
        for b in KNOWN_BUCKETS
    }

    report: dict[str, Any] = {
        "meta": {
            "account": account,
            "date": date,
            "session": session or "ALL",
            "last_n_cycles": last_n_cycles,
            "selected_cycle_ids": selected_cycle_ids,
        },
        "funnel": candidate.get("funnel", {}),
        "bad_patterns": {
            "counts": bad_pattern_counts,
            "total": bad_pattern_total,
            "any_issues": bool(validation.get("any_issues")),
        },
        "rejection_distribution": rejection_distribution,
        "bucket_final_exec": bucket_final_exec,
        "core_bucket": core_bucket,
        "technical_activation": {
            "meta": technical.get("meta", {}),
            "summary": technical.get("activation_summary", {}),
            "bucket_breakdown": technical.get("bucket_breakdown", {}),
            "samples": technical.get("samples", {}).get("technical_any_active", [])[:3],
        },
        "budget": budget,
        "sources": {
            "candidate_analysis": candidate,
            "validation": validation,
            "technical_activation_analysis": technical,
            "budget_analysis": budget,
        },
    }
    report["conclusion"] = _derive_diagnostic_conclusion(report)
    # Resolve next_steps with real date/account/session/last_n from meta
    _meta = report["meta"]
    report["conclusion"]["next_steps"] = _resolve_next_steps(
        report["conclusion"]["bottleneck"],
        date=str(_meta.get("date", "")),
        account=str(_meta.get("account", "")),
        session=str(_meta.get("session", "")),
        last_n=int(_meta.get("last_n_cycles", 0) or 0),
    )
    return report


def _resolve_next_steps(
    bottleneck: str,
    *,
    date: str,
    account: str,
    session: str,
    last_n: int,
) -> list[dict[str, str]]:
    """Return next-step dicts with {date}/{account}/{session_arg}/{last_n_arg} resolved."""
    session_upper = (session or "").upper()
    session_arg = (
        f" --session {session}" if session_upper and session_upper != "ALL" else ""
    )
    last_n_arg = f" --last-n-cycles {last_n}" if last_n > 0 else ""
    resolved: list[dict[str, str]] = []
    for step in _BN_NEXT_STEPS.get(bottleneck, []):
        resolved.append(
            {
                "label": step["label"],
                "cmd": step["cmd"].format(
                    date=date,
                    account=account,
                    session_arg=session_arg,
                    last_n_arg=last_n_arg,
                ),
            }
        )
    return resolved


def _derive_diagnostic_conclusion(report: dict[str, Any]) -> dict[str, Any]:
    """Pure function: read the assembled report dict, return a conclusion dict."""

    # ── 1. Is core stuck (deep_eval > 0 but final_candidate == 0)? ──────────
    bfe = report.get("bucket_final_exec", {}) or {}
    core_d = bfe.get("core", {}) or {}
    core_deep = int(core_d.get("deep_eval", 0) or 0)
    core_final = int(core_d.get("final_candidate", 0) or 0)
    core_exec = int(core_d.get("executed", 0) or 0)
    core_stuck = core_deep > 0 and core_final == 0

    # ── 2. Rejection reason breakdown for core ───────────────────────────────
    core_reasons = (
        (report.get("core_bucket", {}) or {}).get("core_rejection_reasons", {}) or {}
    )
    dominant_reason_str = "—"
    if core_reasons:
        top_key, top_cnt = max(core_reasons.items(), key=lambda kv: kv[1])
        dominant_reason_str = f"{top_key}({top_cnt})"

    semantic_counts: dict[str, int] = {
        r: int(core_reasons.get(r, 0) or 0) for r in SEMANTIC_REJECTION_REASONS
    }
    total_semantic = sum(semantic_counts.values())
    other_count = int(core_reasons.get("other", 0) or 0)

    # ── 3. Technical cold-start signals ──────────────────────────────────────
    tech = report.get("technical_activation", {}) or {}
    tech_summary = tech.get("summary", {}) or {}
    tech_meta = tech.get("meta", {}) or {}
    analyzed_rows = int(tech_meta.get("analyzed_rows", 0) or 0)
    history_ok = int(tech_summary.get("history_sufficient_count", 0) or 0)
    tech_active = int(tech_summary.get("either_nonzero_count", 0) or 0)
    hist_rate = round(history_ok / analyzed_rows, 2) if analyzed_rows > 0 else 0.0
    act_rate = round(tech_active / analyzed_rows, 2) if analyzed_rows > 0 else 0.0
    # cold-start: <20% of rows have sufficient history; low-activation: <10% active
    cold_start = analyzed_rows > 0 and hist_rate < 0.20
    low_activation = analyzed_rows > 0 and act_rate < 0.10

    # ── 4. Bottleneck determination ───────────────────────────────────────────
    if not core_stuck:
        bottleneck = _BN_NONE
    elif total_semantic == 0 and other_count > 0:
        # old logs before semantic logging fix — cannot distinguish
        bottleneck = _BN_INCONCLUSIVE
    elif total_semantic == 0:
        # core stuck but zero rejection reasons logged at all
        bottleneck = _BN_COLDSTART if (cold_start or low_activation) else _BN_INCONCLUSIVE
    else:
        # pick dominant semantic reason (highest count wins; ties broken by priority order)
        ordered = [
            ("passed_count_insufficient", _BN_STRATEGY),
            ("score_below_threshold", _BN_SCORE),
            ("cost_filter_blocked", _BN_COST),
            ("profit_buffer_insufficient", _BN_COST),
        ]
        bottleneck = _BN_INCONCLUSIVE
        best = 0
        for reason_key, bn in ordered:
            cnt = semantic_counts.get(reason_key, 0)
            if cnt > best:
                best = cnt
                bottleneck = bn
        # if semantic reasons exist but all tiny, and cold-start is clearer, prefer cold-start
        if best <= 1 and (cold_start or low_activation):
            bottleneck = _BN_COLDSTART

    return {
        "core_stuck": core_stuck,
        "core_deep": core_deep,
        "core_final": core_final,
        "core_exec": core_exec,
        "dominant_core_reason": dominant_reason_str,
        "semantic_counts": semantic_counts,
        "analyzed_rows": analyzed_rows,
        "hist_rate": hist_rate,
        "act_rate": act_rate,
        "cold_start_signal": cold_start,
        "low_activation": low_activation,
        "bottleneck": bottleneck,
        "hint": _BN_HINTS.get(bottleneck, ""),
        # next_steps resolved with real args by build_report after meta is available
        "next_steps": [],
    }


def build_md_report(report: dict[str, Any]) -> str:
    """Return a markdown string summarising the report."""
    lines: list[str] = []
    meta = report.get("meta", {}) or {}

    # ── header ──────────────────────────────────────────────────────────────
    lines.append(f"# postrun_diagnostics — {meta.get('date', '?')} / {meta.get('account', '?')}")
    lines.append("")
    lines.append(f"- **session**: {meta.get('session', 'ALL')}")
    lines.append(f"- **last_n_cycles**: {meta.get('last_n_cycles') or 'ALL'}")
    lines.append(f"- **generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # ── 1. Funnel ────────────────────────────────────────────────────────────
    funnel = report.get("funnel", {}) or {}
    stages = funnel.get("stages", {}) or {}
    lines.append("## 1. Funnel Summary")
    lines.append("")
    lines.append(f"Total rows: **{int(funnel.get('total', 0) or 0)}**")
    lines.append("")
    if stages:
        lines.append("| Stage | Count |")
        lines.append("|---|---|")
        for stage in STAGE_ORDER:
            lines.append(f"| {stage} | {int(stages.get(stage, 0) or 0)} |")
    lines.append("")

    # ── 2. Bad Patterns ──────────────────────────────────────────────────────
    bp = report.get("bad_patterns", {}) or {}
    bp_counts = bp.get("counts", {}) or {}
    non_legacy = sum(v for k, v in bp_counts.items() if k not in _LEGACY_BAD_PATTERN_IDS)
    lines.append("## 2. Bad Pattern Counts")
    lines.append("")
    verdict = "🔴 issues" if non_legacy > 0 else "✅ clean"
    lines.append(f"Verdict: **{verdict}** — total bad rows: {int(bp.get('total', 0) or 0)}")
    lines.append("")
    if bp_counts:
        lines.append("| Pattern | Count | Note |")
        lines.append("|---|---|---|")
        for name, count in sorted(bp_counts.items(), key=lambda x: (-x[1], x[0])):
            note = "구 로그 잔재" if name in _LEGACY_BAD_PATTERN_IDS else ("⚠" if count > 0 else "")
            lines.append(f"| `{name}` | {count} | {note} |")
    lines.append("")

    # ── 3. Rejection Distribution ────────────────────────────────────────────
    distribution = report.get("rejection_distribution", {}) or {}
    lines.append("## 3. Recent Rejection Reasons")
    lines.append("")
    if distribution:
        lines.append("| Reason | Count |")
        lines.append("|---|---|")
        for reason, count in distribution.items():
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("_None_")
    lines.append("")

    # ── 4. Bucket / Final / Exec ─────────────────────────────────────────────
    bfe = report.get("bucket_final_exec", {}) or {}
    lines.append("## 4. Bucket → Deep-Eval / Final-Candidate / Executed")
    lines.append("")
    lines.append("| Bucket | Total | Deep-Eval | Final-Cand | Executed | Avg Score |")
    lines.append("|---|---|---|---|---|---|")
    for b in KNOWN_BUCKETS:
        d = bfe.get(b) or {}
        lines.append(
            f"| {b} "
            f"| {int(d.get('total', 0) or 0)} "
            f"| {int(d.get('deep_eval', 0) or 0)} "
            f"| {int(d.get('final_candidate', 0) or 0)} "
            f"| {int(d.get('executed', 0) or 0)} "
            f"| {float(d.get('avg_score', 0.0) or 0.0):.2f} |"
        )
    lines.append("")

    # ── 5. Core Bucket ───────────────────────────────────────────────────────
    cb = report.get("core_bucket", {}) or {}
    lines.append("## 5. Core Bucket Deep-Eval Analysis")
    lines.append("")
    lines.append(f"- core_deep_eval_losers: **{int(cb.get('core_loser_count', 0) or 0)}**")
    lines.append(f"- non_core_final_candidates: **{int(cb.get('non_core_final_count', 0) or 0)}**")
    avg_core = float(cb.get('avg_core_loser_score_deep', 0.0) or 0.0)
    avg_nc = float(cb.get('avg_non_core_final_score_deep', 0.0) or 0.0)
    if avg_core > 0 or avg_nc > 0:
        lines.append(f"- avg score_deep — core losers: **{avg_core:.2f}** / non-core finals: **{avg_nc:.2f}**")
    cr = cb.get("core_rejection_reasons", {}) or {}
    if cr:
        lines.append("")
        lines.append("**Core rejection breakdown:**")
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("|---|---|")
        for reason in SEMANTIC_REJECTION_REASONS:
            lines.append(f"| {reason} | {int(cr.get(reason, 0) or 0)} |")
        for reason, count in sorted(
            {k: v for k, v in cr.items() if k not in set(SEMANTIC_REJECTION_REASONS)}.items(),
            key=lambda x: -x[1],
        ):
            lines.append(f"| {reason} | {count} |")
    summary = str(cb.get("human_summary") or "").strip()
    if summary:
        lines.append("")
        lines.append(f"> {summary}")
    lines.append("")

    # ── 6. Technical Activation ──────────────────────────────────────────────
    ta = report.get("technical_activation", {}) or {}
    ta_meta = ta.get("meta", {}) or {}
    ta_summary = ta.get("summary", {}) or {}
    ta_buckets = ta.get("bucket_breakdown", {}) or {}
    lines.append("## 6. Technical Feature Activation")
    lines.append("")
    if int(ta_meta.get("analyzed_rows", 0) or 0) > 0:
        lines.append(
            f"analyzed_rows={int(ta_meta.get('analyzed_rows', 0) or 0)} | "
            f"trend_nonzero={int(ta_summary.get('trend_nonzero_count', 0) or 0)} | "
            f"macd_nonzero={int(ta_summary.get('macd_nonzero_count', 0) or 0)} | "
            f"history_26plus={int(ta_summary.get('history_sufficient_count', 0) or 0)}"
        )
        lines.append("")
        lines.append("| Bucket | Rows | hist26+ | trend>0 | macd>0 | any>0 |")
        lines.append("|---|---|---|---|---|---|")
        for bname in ("core", "rotating", "exploration"):
            bk = ta_buckets.get(bname, {}) or {}
            lines.append(
                f"| {bname} "
                f"| {int(bk.get('rows', 0) or 0)} "
                f"| {int(bk.get('history_sufficient_rows', 0) or 0)} "
                f"| {int(bk.get('trend_nonzero_rows', 0) or 0)} "
                f"| {int(bk.get('macd_nonzero_rows', 0) or 0)} "
                f"| {int(bk.get('either_nonzero_rows', 0) or 0)} |"
            )
    else:
        lines.append("_unavailable or no analyzable deep-eval rows_")
    lines.append("")

    # ── 7. Budget / Rate-Limit ───────────────────────────────────────────────
    budget = report.get("budget", {}) or {}
    bflags = budget.get("flags", {}) or {}
    bsell = budget.get("sell_watch", {}) or {}
    bdrain = budget.get("drain", {}) or {}
    brecs = budget.get("recommendations", []) or []
    btotal = int((budget.get("meta", {}) or {}).get("cycles_analyzed", 0) or 0)
    lines.append("## 7. Budget / Rate-Limit Pressure")
    lines.append("")
    if btotal > 0:
        def _pct_md(v: int, t: int) -> str:
            return f"{100.0 * v / t:.0f}%" if t > 0 else "—"
        buy_skip = int(bflags.get("skipped_buy_scan_budget_limited", 0) or 0)
        sell_partial = int(bflags.get("sell_watch_partial", 0) or 0)
        rl_trig = int(bflags.get("rate_limit_triggered", 0) or 0)
        lines.append(f"- cycles: **{btotal}**")
        lines.append(f"- rl_triggered: **{rl_trig}** ({_pct_md(rl_trig, btotal)})")
        lines.append(f"- buy_skip: **{buy_skip}** ({_pct_md(buy_skip, btotal)})")
        lines.append(f"- sell_partial: **{sell_partial}** ({_pct_md(sell_partial, btotal)})")
        rl_src = (bsell.get("rate_limit_sources") or {})
        if rl_src:
            src_str = " | ".join(f"{k}={v}" for k, v in sorted(rl_src.items(), key=lambda x: -x[1]))
            lines.append(f"- rl_sources: {src_str}")
        sw_dc = int(bdrain.get("sell_watch_drain_count", 0) or 0)
        ex_dc = int(bdrain.get("exec_tail_drain_count", 0) or 0)
        if sw_dc > 0 or ex_dc > 0:
            lines.append(
                f"- drain: sw={sw_dc}cyc/{float(bdrain.get('sell_watch_drain_avg_ms', 0) or 0):.0f}ms avg | "
                f"exec={ex_dc}cyc/{float(bdrain.get('exec_tail_drain_avg_ms', 0) or 0):.0f}ms avg"
            )
        if brecs:
            lines.append("")
            for rec in brecs:
                lines.append(f"> ※ {rec}")
    else:
        lines.append("_no snapshot data available_")
    lines.append("")

    # ── 8. Diagnostic Conclusion ─────────────────────────────────────────────
    c = report.get("conclusion", {}) or {}
    lines.append("## 8. Diagnostic Conclusion")
    lines.append("")
    if c:
        bottleneck = str(c.get("bottleneck", _BN_INCONCLUSIVE))
        lines.append(f"**BOTTLENECK: `{bottleneck}`**")
        lines.append("")
        hint = str(c.get("hint") or "").strip()
        if hint:
            lines.append(f"> {hint}")
            lines.append("")
        core_tag = "🔴 STUCK" if c.get("core_stuck") else "✅ OK"
        lines.append(
            f"- core: {core_tag} — "
            f"deep={c.get('core_deep', 0)} "
            f"final={c.get('core_final', 0)} "
            f"exec={c.get('core_exec', 0)}"
        )
        lines.append(f"- top core reason: `{c.get('dominant_core_reason', '—')}`")
        if c.get("analyzed_rows", 0) > 0:
            lines.append(
                f"- hist_ok={float(c.get('hist_rate', 0)):.0%} "
                f"tech_active={float(c.get('act_rate', 0)):.0%}"
            )
        next_steps = c.get("next_steps") or []
        if next_steps:
            lines.append("")
            lines.append("**Next steps:**")
            lines.append("")
            for i, step in enumerate(next_steps, 1):
                label = str(step.get("label") or "").strip()
                cmd = str(step.get("cmd") or "").strip()
                if label:
                    lines.append(f"{i}. {label}")
                if cmd:
                    lines.append(f"   ```")
                    lines.append(f"   {cmd}")
                    lines.append(f"   ```")
    else:
        lines.append("_no conclusion data_")
    lines.append("")

    return "\n".join(lines)


