"""CLI: postrun_diagnostics

Run the most useful post-run diagnostics together for one account/date pair.

Usage:
    python3 -m app.tools.postrun_diagnostics --date 20260407 --account mock_12345678_01
    python3 -m app.tools.postrun_diagnostics --date 20260407 --account mock_12345678_01 --session REGULAR --last-n-cycles 5
    python3 -m app.tools.postrun_diagnostics --date 20260407 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.tools.analyze_candidate_logs import (
    KNOWN_BUCKETS,
    SEMANTIC_REJECTION_REASONS,
    STAGE_ORDER,
)
from app.tools.postrun_report import (
    _build_filtered_core_bucket_summary,
    _compact_counter,
    _derive_diagnostic_conclusion,
    _resolve_next_steps,
    build_md_report,
    build_report,
)

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}"


def _ok(text: str) -> str:
    return _c(_GREEN, text)


def _bad(text: str) -> str:
    return _c(_RED, text)


def _warn(text: str) -> str:
    return _c(_YELLOW, text)


def _dim(text: str) -> str:
    return _c(_DIM, text)


def _header(text: str) -> str:
    return _c(_BOLD + _CYAN, text)


def _print_funnel_section(report: dict[str, Any]) -> None:
    funnel = report.get("funnel", {})
    total = int(funnel.get("total", 0) or 0)
    stages = funnel.get("stages", {}) or {}
    print(_header("  1. Funnel Summary"))
    print(f"  rows={total}")
    print(
        "  "
        + " | ".join(
            (
                f"{stage}={int(funnel.get('final_candidate_count', stages.get(stage, 0)) or 0)}"
                if stage == "final_candidate"
                else (
                    f"{stage}={int(funnel.get('executed_count', stages.get(stage, 0)) or 0)}"
                    if stage == "executed"
                    else f"{stage}={int(stages.get(stage, 0) or 0)}"
                )
            )
            for stage in STAGE_ORDER
        )
    )
    print()


_LEGACY_BAD_PATTERN_IDS = frozenset({
    "residual_reason_mismatch",
    "deep_eval_other_reason",
    "deep_eval_missing_selection_outcome",
})


def _print_bad_patterns_section(report: dict[str, Any]) -> None:
    payload = report.get("bad_patterns", {})
    counts = payload.get("counts", {}) or {}
    print(_header("  2. Bad Pattern Counts"))
    if not counts:
        print(_dim("  no checks available"))
        print()
        return
    # Separate legacy-data patterns from true code bugs
    non_legacy_total = sum(
        v for k, v in counts.items() if k not in _LEGACY_BAD_PATTERN_IDS
    )
    legacy_total = sum(
        v for k, v in counts.items() if k in _LEGACY_BAD_PATTERN_IDS
    )
    real_issues = bool(non_legacy_total > 0)
    status = _bad("issues") if real_issues else _ok("clean")
    print(f"  verdict={status} total_bad_rows={int(payload.get('total', 0) or 0)}")
    for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if name in _LEGACY_BAD_PATTERN_IDS and count > 0:
            color = _warn
            tag = " [구 로그 잔재 — 현 코드 무관]"
        elif count > 0:
            color = _bad
            tag = ""
        else:
            color = _dim
            tag = ""
        print(f"  {name:<36} {color(str(count))}{tag}")
    if legacy_total > 0:
        print(_dim(f"  * legacy 패턴 {legacy_total}건: 수정 전 코드가 작성한 구 로그 데이터에서 발생. 현재 코드는 올바르게 처리합니다."))
    print()


def _print_rejection_distribution_section(report: dict[str, Any]) -> None:
    distribution = report.get("rejection_distribution", {}) or {}
    print(_header("  3. Recent Rejection Reasons"))
    if not distribution:
        print(_dim("  none"))
        print()
        return
    for reason, count in distribution.items():
        print(f"  {reason:<36} {count}")
    print()


def _print_bucket_final_exec_section(report: dict[str, Any]) -> None:
    bfe = report.get("bucket_final_exec", {}) or {}
    print(_header("  4. Bucket → Deep-Eval / Final-Candidate / Executed"))
    print(
        f"  {'bucket':<12} {'total':>6} {'deep_eval':>10} {'final_cand':>11} {'executed':>9} {'avg_score':>10}"
    )
    print(_dim("  " + "─" * 64))
    for b in KNOWN_BUCKETS:
        d = bfe.get(b) or {}
        total = int(d.get("total", 0) or 0)
        deep = int(d.get("deep_eval", 0) or 0)
        final = int(d.get("final_candidate", 0) or 0)
        executed = int(d.get("executed", 0) or 0)
        avg_sc = float(d.get("avg_score", 0.0) or 0.0)
        final_col = _ok if final > 0 else _dim
        exec_col = _ok if executed > 0 else _dim
        print(
            f"  {b:<12} {total:>6} {deep:>10} "
            + final_col(f"{final:>11}") + " "
            + exec_col(f"{executed:>9}") + f" {avg_sc:>10.2f}"
        )
    print()


def _print_core_bucket_section(report: dict[str, Any]) -> None:
    payload = report.get("core_bucket", {}) or {}
    print(_header("  5. Core Bucket Deep-Eval Analysis"))
    core_loser_count = int(payload.get("core_loser_count", 0) or 0)
    non_core_final_count = int(payload.get("non_core_final_count", 0) or 0)
    print(
        f"  core_deep_eval_losers={core_loser_count} | "
        f"non_core_final_candidates={non_core_final_count}"
    )
    core_symbols = payload.get("top_core_symbols", []) or []
    non_core_symbols = payload.get("top_non_core_final_symbols", []) or []
    if core_symbols:
        core_text = ", ".join(
            f"{item.get('symbol')}({item.get('count')})" for item in core_symbols
        )
        print(f"  top_core_losers      : {core_text}")
    if non_core_symbols:
        non_core_text = ", ".join(
            f"{item.get('symbol')}({item.get('count')})" for item in non_core_symbols
        )
        print(f"  top_non_core_finals  : {non_core_text}")

    # Score gap
    avg_core = float(payload.get("avg_core_loser_score_deep", 0.0) or 0.0)
    avg_non_core = float(payload.get("avg_non_core_final_score_deep", 0.0) or 0.0)
    if avg_core > 0.0 or avg_non_core > 0.0:
        print(
            f"  avg_score_deep       : core_loser={avg_core:.2f} | non_core_final={avg_non_core:.2f}"
        )

    # Rejection reason breakdown for core losers
    core_reasons = payload.get("core_rejection_reasons", {}) or {}
    if core_loser_count > 0:
        print(_dim("  core rejection breakdown:"))
        for reason in SEMANTIC_REJECTION_REASONS:
            count = int(core_reasons.get(reason, 0) or 0)
            col = _bad if count > 0 else _dim
            print(f"    {reason:<34} " + col(str(count)))
        # any other non-semantic reasons
        other_reasons = {
            k: v
            for k, v in core_reasons.items()
            if k not in set(SEMANTIC_REJECTION_REASONS)
        }
        for reason, count in sorted(other_reasons.items(), key=lambda x: -x[1]):
            print(f"    {str(reason):<34} {count}")
        if not core_reasons:
            print(_dim("    (no rejection reasons logged — old log format)"))

    summary = str(payload.get("human_summary") or "").strip()
    if summary:
        print(f"  summary              : {summary}")
    print()


def _print_technical_section(report: dict[str, Any]) -> None:
    payload = report.get("technical_activation", {}) or {}
    meta = payload.get("meta", {}) or {}
    summary = payload.get("summary", {}) or {}
    bucket_breakdown = payload.get("bucket_breakdown", {}) or {}
    print(_header("  6. Technical Feature Activation"))
    if int(meta.get("analyzed_rows", 0) or 0) <= 0:
        print(_dim("  unavailable or no analyzable deep-eval rows"))
        print()
        return
    print(
        f"  analyzed_rows={int(meta.get('analyzed_rows', 0) or 0)} | "
        f"trend_nonzero={int(summary.get('trend_nonzero_count', 0) or 0)} | "
        f"macd_nonzero={int(summary.get('macd_nonzero_count', 0) or 0)} | "
        f"history_26plus={int(summary.get('history_sufficient_count', 0) or 0)}"
    )
    for bucket_name in ("core", "rotating", "exploration"):
        bucket = bucket_breakdown.get(bucket_name, {}) or {}
        print(
            f"  {bucket_name:<12} rows={int(bucket.get('rows', 0) or 0):<4} "
            f"hist26+={int(bucket.get('history_sufficient_rows', 0) or 0):<4} "
            f"trend>0={int(bucket.get('trend_nonzero_rows', 0) or 0):<4} "
            f"macd>0={int(bucket.get('macd_nonzero_rows', 0) or 0):<4} "
            f"any>0={int(bucket.get('either_nonzero_rows', 0) or 0):<4}"
        )
    samples = payload.get("samples", []) or []
    if samples:
        sample = samples[0]
        print(
            "  sample_active        : "
            f"{sample.get('symbol')}({sample.get('symbol_name') or ''}) "
            f"bucket={sample.get('bucket')} "
            f"obs={sample.get('observation_count')} "
            f"trend={sample.get('trend_alignment_score')} "
            f"macd={sample.get('macd_momentum_score')}"
        )
    print()


def _print_budget_section(report: dict[str, Any]) -> None:
    budget = report.get("budget", {}) or {}
    flags = budget.get("flags", {}) or {}
    sell_watch = budget.get("sell_watch", {}) or {}
    buy_scan = budget.get("buy_scan", {}) or {}
    drain = budget.get("drain", {}) or {}
    recs = budget.get("recommendations", []) or []
    total_cycles = int((budget.get("meta", {}) or {}).get("cycles_analyzed", 0) or 0)
    print(_header("  7. Budget / Rate-Limit Pressure"))
    if total_cycles == 0:
        print(_dim("  no snapshot data available"))
        print()
        return
    buy_skip = int(flags.get("skipped_buy_scan_budget_limited", 0) or 0)
    sell_partial = int(flags.get("sell_watch_partial", 0) or 0)
    rl_triggered = int(flags.get("rate_limit_triggered", 0) or 0)
    rl_src = sell_watch.get("rate_limit_sources", {}) or {}
    print(
        f"  cycles={total_cycles} | "
        f"rl_triggered={rl_triggered} | "
        f"buy_skip={buy_skip}({_pct_str(buy_skip, total_cycles)}) | "
        f"sell_partial={sell_partial}({_pct_str(sell_partial, total_cycles)})"
    )
    if rl_src:
        src_str = " ".join(f"{k}={v}" for k, v in sorted(rl_src.items(), key=lambda x: -x[1]))
        print(f"  rl_sources: {src_str}")
    sw_dc = int(drain.get("sell_watch_drain_count", 0) or 0)
    ex_dc = int(drain.get("exec_tail_drain_count", 0) or 0)
    if sw_dc > 0 or ex_dc > 0:
        sw_avg = float(drain.get("sell_watch_drain_avg_ms", 0) or 0)
        ex_avg = float(drain.get("exec_tail_drain_avg_ms", 0) or 0)
        print(
            f"  drain: sw={sw_dc}cyc/{sw_avg:.0f}ms avg | "
            f"exec={ex_dc}cyc/{ex_avg:.0f}ms avg"
        )
    if recs:
        for i, rec in enumerate(recs, 1):
            print(f"  ※ {rec}")
    print()


def _pct_str(part: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100.0 * part / total:.0f}%"


# ---------------------------------------------------------------------------
# Diagnostic conclusion derivation
# ---------------------------------------------------------------------------

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


def _wrap(text: str, width: int = 70, indent: str = "    ") -> list[str]:
    """Word-wrap text to width, returning lines prefixed by indent."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current.rstrip())
            current = indent + word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def _print_diagnostic_conclusion(report: dict[str, Any]) -> None:
    c = report.get("conclusion", {}) or {}
    if not c:
        return

    bottleneck = str(c.get("bottleneck", _BN_INCONCLUSIVE))
    core_stuck = bool(c.get("core_stuck", False))
    cold_start = bool(c.get("cold_start_signal", False))
    low_act = bool(c.get("low_activation", False))

    # bottleneck colour
    if bottleneck == _BN_NONE:
        bn_col = _ok
    elif bottleneck == _BN_INCONCLUSIVE:
        bn_col = _warn
    else:
        bn_col = _bad

    print(_header("═" * 76))
    print(_header("  ▶  DIAGNOSTIC CONCLUSION"))
    print(_header("─" * 76))

    # core status line
    core_tag = (
        _bad("STUCK") if core_stuck else _ok("OK   ")
    )
    print(
        f"  core status      : {core_tag}  "
        f"deep={c['core_deep']}  final={c['core_final']}  exec={c['core_exec']}"
    )

    # dominant rejection reason
    print(f"  top core reason  : {c.get('dominant_core_reason', '—')}")

    # semantic breakdown (only if any non-zero)
    sem = c.get("semantic_counts", {}) or {}
    if any(v > 0 for v in sem.values()):
        parts = "  ".join(
            f"{k.replace('_', '-')}={v}" for k, v in sem.items() if v > 0
        )
        print(f"  semantic reasons : {parts}")

    # technical cold-start line (only if analysis data present)
    if c.get("analyzed_rows", 0) > 0:
        cold_tag = _bad("YES") if cold_start else _ok("no ")
        act_tag = _bad("YES") if low_act else _ok("no ")
        print(
            f"  cold-start       : hist_ok={c['hist_rate']:.0%}  "
            f"tech_active={c['act_rate']:.0%}  "
            f"hist<20%={cold_tag}  act<10%={act_tag}"
        )

    print()
    print(f"  ▶ BOTTLENECK : {bn_col(bottleneck)}")

    hint = str(c.get("hint") or "").strip()
    for line in _wrap(hint, width=74, indent="    "):
        print(line)

    # next steps block
    next_steps: list[dict[str, str]] = c.get("next_steps") or []
    if next_steps:
        print()
        print(_header("  ─ NEXT STEP ─────────────────────────────────────────────────"))
        for i, step in enumerate(next_steps, 1):
            label = str(step.get("label") or "").strip()
            cmd = str(step.get("cmd") or "").strip()
            if label:
                print(f"  {i}. {label}")
            if cmd:
                print(_dim(f"     {cmd}"))

    print(_header("═" * 76))
    print()


def print_terminal_report(report: dict[str, Any]) -> None:
    meta = report.get("meta", {}) or {}
    print()
    print(_header("═" * 76))
    print(_header("  postrun_diagnostics"))
    print(_header("═" * 76))
    print(f"  account : {meta.get('account')}")
    print(f"  date    : {meta.get('date')}")
    print(f"  session : {meta.get('session')}")
    print(f"  last_n  : {meta.get('last_n_cycles') or 'ALL'}")
    cycle_ids = meta.get("selected_cycle_ids") or []
    if cycle_ids:
        print(f"  cycle_ids: {', '.join(cycle_ids)}")
    print()
    _print_funnel_section(report)
    _print_bad_patterns_section(report)
    _print_rejection_distribution_section(report)
    _print_bucket_final_exec_section(report)
    _print_core_bucket_section(report)
    _print_technical_section(report)
    _print_budget_section(report)
    _print_diagnostic_conclusion(report)


def print_json_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------

def export_md_report(
    report: dict[str, Any],
    *,
    account: str,
    date: str,
    session: str,
) -> Path:
    """Write markdown report to logs/ directory. Returns the path written."""
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    out_dir = _PROJECT_ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    session_tag = session.upper() if session else "ALL"
    out_path = out_dir / f"postrun_{account}_{date}_{session_tag}.md"
    out_path.write_text(build_md_report(report), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run compact post-run diagnostics for one date/account."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--last-n-cycles", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--export-md",
        action="store_true",
        help="Save a markdown summary to logs/postrun_<account>_<date>_<session>.md",
    )
    args = parser.parse_args()

    report = build_report(
        account=args.account,
        date=args.date,
        session=args.session,
        last_n_cycles=args.last_n_cycles,
    )

    if args.json:
        print_json_report(report)
    else:
        print_terminal_report(report)

    if args.export_md:
        out_path = export_md_report(
            report,
            account=args.account,
            date=args.date,
            session=args.session,
        )
        print(f"Markdown report saved → {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
