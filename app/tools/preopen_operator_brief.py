"""CLI: preopen_operator_brief

One-screen operator brief for the next market open.

This command is report-only. It combines readiness, latest market-health,
core bottleneck analysis, budget pressure, and parameter nudges into a single
pre-open brief without mutating settings.

Usage:
    python3 -m app.tools.preopen_operator_brief
    python3 -m app.tools.preopen_operator_brief --date 20260410
    python3 -m app.tools.preopen_operator_brief --json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from app.auth.account_scope import get_account_scope_context
from app.auth.settings import get_settings
from app.tools.analyze_budget_bottlenecks import run_analysis as run_budget_analysis
from app.tools.analyze_core_bucket import run_analysis as run_core_analysis
from app.tools.eod_health_check import detect_latest_market_date
from app.tools.live_health_check import build_health_summary
from app.tools.preopen_readiness_check import build_report as build_readiness_report

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _bad(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _fmt_ratio(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{100.0 * part / total:.0f}%"


def build_parameter_nudges(
    *,
    settings,
    health: dict[str, Any],
    budget: dict[str, Any],
    core: dict[str, Any],
) -> list[dict[str, Any]]:
    nudges: list[dict[str, Any]] = []
    cycles = int((budget.get("meta") or {}).get("cycles_analyzed", 0) or 0)
    sell_partial = int((health.get("sell_watch_partial_count") or 0) or 0)
    rl_triggered = int((health.get("rate_limit_triggered_count") or 0) or 0)
    buy_skip = int((health.get("buy_scan_rate_limit_skip_count") or 0) or 0)
    rate_limit_sources = dict(health.get("rate_limit_sources") or {})
    sell_watch_rl = int(rate_limit_sources.get("sell_watch", 0) or 0)

    if cycles > 0 and (sell_partial / cycles >= 0.60 or sell_watch_rl / cycles >= 0.40):
        current = int(settings.sell_check_interval_seconds)
        suggested = max(current + 2, int(round(current * 1.5)))
        nudges.append(
            {
                "priority": 1,
                "parameter": "SELL_CHECK_INTERVAL_SECONDS",
                "current": current,
                "suggested": suggested,
                "reason": (
                    f"sell_watch partial={sell_partial}/{cycles} ({_fmt_ratio(sell_partial, cycles)}) "
                    f"and sell_watch RL source={sell_watch_rl}/{cycles}입니다."
                ),
                "expected_effect": "sell_watch 압박을 낮춰 buy_scan 예산과 request window를 보호합니다.",
            }
        )

    if cycles > 0 and buy_skip / cycles >= 0.10:
        current = int(settings.api_buy_scan_min_request_reserve)
        nudges.append(
            {
                "priority": 2,
                "parameter": "API_BUY_SCAN_MIN_REQUEST_RESERVE",
                "current": current,
                "suggested": current + 1,
                "reason": (
                    f"buy_scan skip={buy_skip}/{cycles} ({_fmt_ratio(buy_skip, cycles)})로 "
                    "buy 예산 선보호가 더 필요합니다."
                ),
                "expected_effect": "sell_watch가 request budget을 다 쓰기 전에 buy_scan 최소 몫을 남깁니다.",
            }
        )

    if cycles > 0 and rl_triggered / cycles >= 0.60:
        current = int(settings.api_buy_scan_min_quote_reserve)
        nudges.append(
            {
                "priority": 3,
                "parameter": "API_BUY_SCAN_MIN_QUOTE_RESERVE",
                "current": current,
                "suggested": current + 1,
                "reason": (
                    f"rate_limit_triggered={rl_triggered}/{cycles} ({_fmt_ratio(rl_triggered, cycles)})로 "
                    "quote reserve를 더 보수적으로 둘 만합니다."
                ),
                "expected_effect": "buy_scan 진입 시 최소 quote 여유를 더 확보합니다.",
            }
        )

    rc = core.get("rule_contrast_interpretation") or {}
    lift = core.get("rule_lift_opportunities") or {}
    near_threshold_rows = int(lift.get("near_threshold_row_count", 0) or 0)
    if (
        str(rc.get("verdict") or "") == "threshold_only"
        and int(rc.get("gap_to_threshold", 0) or 0) == 1
        and near_threshold_rows > 0
    ):
        current = int(settings.buy_rule_required_pass_count_core)
        nudges.append(
            {
                "priority": 4,
                "parameter": "BUY_RULE_REQUIRED_PASS_COUNT_CORE",
                "current": current,
                "suggested": max(1, current - 1),
                "reason": (
                    f"core verdict가 threshold_only이고 near-threshold row={near_threshold_rows}건입니다."
                ),
                "expected_effect": "core deep_eval -> final 전환을 시험적으로 열어볼 수 있습니다.",
            }
        )

    symbol_diagnosis = (core.get("symbol_failure_diagnosis") or {}).get("symbols") or []
    if symbol_diagnosis:
        first = symbol_diagnosis[0]
        top_rules = [item.get("rule") for item in first.get("top_missing_rules") or [] if item.get("rule")]
        if top_rules:
            nudges.append(
                {
                    "priority": 5,
                    "parameter": "RULE_THRESHOLD_REVIEW",
                    "current": "-",
                    "suggested": ", ".join(top_rules[:2]),
                    "reason": (
                        f"대표 near-miss symbol={first.get('symbol')} "
                        f"verdict={first.get('verdict')}입니다."
                    ),
                    "expected_effect": "우선 재검토할 전략 rule family를 좁혀서 장후 튜닝 시간을 줄입니다.",
                }
            )

    return sorted(nudges, key=lambda item: (int(item.get("priority", 99) or 99), str(item.get("parameter") or "")))


def _load_runtime_state_readonly() -> dict[str, Any] | None:
    """Read-only account-scoped runtime_state load; None if unavailable.

    Reuses ``app.runtime_state.load_runtime_state`` (account-scoped path
    resolution) — no new save path invented. Any failure → None so the brief can
    render an explicit "정보 없음" that is distinct from "의심 없음" (gate C8).
    """
    try:
        from app.runtime_state import load_runtime_state

        state = load_runtime_state()
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def build_manual_trades_section(state: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the manual-trades brief section from a runtime_state dict.

    ``state is None`` → state unavailable (distinct from "no suspects", C8).
    Suspects whose ``last_detected_at`` KR-calendar date is within the last 2
    calendar days are surfaced (resolved entries included but flagged).
    """
    if not isinstance(state, dict):
        return {"state_available": False, "suspects": [], "intent_adjustments": []}

    from app.core.time_utils import KOREA_TZ, get_korean_now

    today = get_korean_now().astimezone(KOREA_TZ).date()
    suspects_raw = state.get("manual_trade_suspects_by_symbol") or {}
    suspects: list[dict[str, Any]] = []
    if isinstance(suspects_raw, dict):
        for symbol, entry in suspects_raw.items():
            if not isinstance(entry, dict):
                continue
            last_detected_at = str(entry.get("last_detected_at") or "").strip()
            try:
                detected_date = (
                    datetime.fromisoformat(last_detected_at)
                    .astimezone(KOREA_TZ)
                    .date()
                )
            except (TypeError, ValueError):
                continue
            if (today - detected_date).days > 2:
                continue
            suspects.append(
                {
                    "symbol": str(symbol),
                    "classification": str(entry.get("classification") or "-"),
                    "expected_qty": int(entry.get("expected_qty", 0) or 0),
                    "actual_qty": int(entry.get("actual_qty", 0) or 0),
                    "last_detected_at": last_detected_at,
                    "occurrences": int(entry.get("occurrences", 0) or 0),
                    "resolved": entry.get("resolved_at") is not None,
                }
            )
    suspects.sort(key=lambda item: str(item.get("last_detected_at") or ""), reverse=True)

    adjustments_raw = state.get("last_intent_adjustments") or []
    intent_adjustments = [a for a in adjustments_raw if isinstance(a, dict)]
    return {
        "state_available": True,
        "suspects": suspects,
        "intent_adjustments": intent_adjustments,
    }


def print_manual_trades_section(section: dict[str, Any]) -> None:
    print(_h("  5. Manual Trades Reconciliation"))
    if not section.get("state_available"):
        # C8: distinct from the "no suspects" line — a reset/absent state must
        # never be read as "no manual trades".
        print(_dim("  runtime_state 정보 없음 (상태 로드 불가로 판단 보류)"))
        print()
        return
    suspects = list(section.get("suspects") or [])
    if not suspects:
        print(_dim("  수동 매매 의심 없음"))
    else:
        for item in suspects:
            resolved = " [resolved]" if item.get("resolved") else ""
            print(
                _warn(
                    f"  {item.get('symbol')} | {item.get('classification')} | "
                    f"{int(item.get('expected_qty', 0) or 0)}→"
                    f"{int(item.get('actual_qty', 0) or 0)}{resolved}"
                )
            )
    adjustments = list(section.get("intent_adjustments") or [])
    for adjustment in adjustments[:5]:
        print(
            f"  intent {adjustment.get('symbol')} {adjustment.get('cause')}: "
            f"{int(adjustment.get('before_qty', 0) or 0)}→"
            f"{int(adjustment.get('after_qty', 0) or 0)}"
        )
    print()


def build_brief(*, account: str, date: str | None, session: str) -> dict[str, Any]:
    settings = get_settings()
    resolved_date = date or detect_latest_market_date(account)
    readiness = build_readiness_report(stale_minutes=30, online=False)
    health = build_health_summary(account=account, date=resolved_date, session=session)
    budget = run_budget_analysis(account=account, date=resolved_date, session=session)
    core = run_core_analysis(account=account, date=resolved_date, session=session)
    nudges = build_parameter_nudges(
        settings=settings,
        health=health,
        budget=budget,
        core=core,
    )
    manual_trades = build_manual_trades_section(_load_runtime_state_readonly())
    return {
        "account": account,
        "date": resolved_date,
        "session": session or "ALL",
        "readiness": readiness,
        "health": health,
        "budget": budget,
        "core": core,
        "parameter_nudges": nudges,
        "manual_trades": manual_trades,
    }


def print_terminal(brief: dict[str, Any]) -> None:
    health = brief["health"]
    budget = brief["budget"]
    core = brief["core"]
    rc = core.get("rule_contrast_interpretation") or {}
    readiness_checks = list((brief.get("readiness") or {}).get("checks") or [])

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  preopen_operator_brief"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {brief['account']}")
    print(f"  date    : {brief['date']}")
    print(f"  session : {brief['session']}")
    print()

    print(_h("  1. Opening Read"))
    for check in readiness_checks[:4]:
        status = str(check.get("status") or "WARN")
        badge = _ok(status) if status == "OK" else (_warn(status) if status == "WARN" else _bad(status))
        print(f"  {check['name']:<22} {badge:<12} {check['detail']}")
    print()

    print(_h("  2. Last Market Pressure"))
    cycles = int((budget.get("meta") or {}).get("cycles_analyzed", 0) or 0)
    print(
        f"  final/executed         : {health.get('final_candidate_count')} / {health.get('executed_count')}"
    )
    print(
        f"  rate_limit_triggered   : {health.get('rate_limit_triggered_count')} / {cycles}"
    )
    print(
        f"  sell_watch_partial     : {health.get('sell_watch_partial_count')} / {cycles}"
    )
    print(
        f"  buy_scan RL skip       : {health.get('buy_scan_rate_limit_skip_count')} / {cycles}"
    )
    print(
        f"  rate_limit_sources     : {dict(health.get('rate_limit_sources') or {})}"
    )
    print()

    print(_h("  3. Core Conversion Read"))
    core_stats = core.get("core_losers_stats") or {}
    lift = core.get("rule_lift_opportunities") or {}
    print(
        f"  core deep losers       : {core_stats.get('count', 0)}"
    )
    print(
        f"  rule contrast          : {rc.get('verdict') or '-'} | gap_to_threshold={rc.get('gap_to_threshold')}"
    )
    print(
        f"  near-threshold rows    : {lift.get('near_threshold_row_count', 0)}"
    )
    print(f"  human read             : {core.get('human_summary')}")
    print()

    print(_h("  4. Today Knobs"))
    nudges = brief.get("parameter_nudges") or []
    if not nudges:
        print(_dim("  no clear parameter nudge stood out from the latest logs."))
    else:
        for item in nudges[:5]:
            label = item["parameter"]
            current = item["current"]
            suggested = item["suggested"]
            print(f"  {label}: {current} -> {suggested}")
            print(f"    why: {item['reason']}")
            print(f"    effect: {item['expected_effect']}")
    print()

    manual_trades = brief.get("manual_trades")
    if isinstance(manual_trades, dict):
        print_manual_trades_section(manual_trades)

    print(_h("  6. Suggested Routine"))
    print("  1. `preopen_readiness_check`로 파일 freshness와 마지막 상태를 확인합니다.")
    print("  2. `preopen_operator_brief`에서 오늘 만질 파라미터 후보 1~2개만 고릅니다.")
    print("  3. 장후에는 `analyze_core_bucket`, `analyze_budget_bottlenecks`, `replay_cycles --focus`로 바로 복기합니다.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="장전 운영 브리프: readiness, 병목, budget, 파라미터 후보를 한 화면에 보여줍니다."
    )
    parser.add_argument("--account", default=get_account_scope_context()["account_signature"])
    parser.add_argument("--date", default="")
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    brief = build_brief(
        account=args.account,
        date=args.date or None,
        session=args.session,
    )
    if args.json:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    else:
        print_terminal(brief)


if __name__ == "__main__":
    main()
