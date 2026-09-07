"""CLI: parameter_change_simulation_report

Offline heuristic simulation for the next parameter bundle.

This report does not mutate settings. It uses the latest offline diagnostics to
estimate what may improve if the recommended parameter nudges are applied.

Usage:
    python3 -m app.tools.parameter_change_simulation_report
    python3 -m app.tools.parameter_change_simulation_report --date 20260410
    python3 -m app.tools.parameter_change_simulation_report --json
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Any

from app.auth.account_scope import get_account_scope_context
from app.tools.preopen_operator_brief import build_brief

_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _find_nudge(nudges: list[dict[str, Any]], parameter: str) -> dict[str, Any] | None:
    for item in nudges:
        if str(item.get("parameter") or "") == parameter:
            return item
    return None


def estimate_budget_effects(
    *,
    cycles: int,
    sell_partial: int,
    rate_limit_triggered: int,
    buy_skip: int,
    sell_watch_rl_source: int,
    sell_interval_current: int | None,
    sell_interval_suggested: int | None,
    request_reserve_delta: int,
    quote_reserve_delta: int,
) -> dict[str, Any]:
    ratio = 1.0
    if sell_interval_current and sell_interval_suggested and sell_interval_suggested > 0:
        ratio = min(1.0, float(sell_interval_current) / float(sell_interval_suggested))

    estimated_sell_partial = int(math.ceil(sell_partial * ratio))
    estimated_sell_watch_rl = int(math.ceil(sell_watch_rl_source * ratio))
    reserve_protection = max(0, request_reserve_delta) + max(0, quote_reserve_delta)
    protected_buy_cycles = min(
        buy_skip,
        max(0, int(round((buy_skip * 0.35) + (reserve_protection * max(cycles, 1) * 0.03)))),
    )
    estimated_buy_skip = max(0, buy_skip - protected_buy_cycles)

    indirect_rl_relief = max(0, sell_watch_rl_source - estimated_sell_watch_rl)
    estimated_rate_limit_triggered = max(
        0,
        rate_limit_triggered - indirect_rl_relief - min(protected_buy_cycles, buy_skip),
    )

    return {
        "cycles": cycles,
        "sell_interval_ratio": round(ratio, 3),
        "estimated_sell_partial": estimated_sell_partial,
        "estimated_sell_watch_rl_source": estimated_sell_watch_rl,
        "estimated_buy_skip": estimated_buy_skip,
        "estimated_rate_limit_triggered": estimated_rate_limit_triggered,
        "protected_buy_cycles": protected_buy_cycles,
        "assumptions": [
            "SELL interval 증가 효과는 sell_watch pressure에 비례해 반영했습니다.",
            "request/quote reserve 증가는 buy_scan skip 일부를 보호하는 방향으로만 반영했습니다.",
            "이 수치는 실제 체결 결과가 아니라 최근 로그 패턴 기반 추정치입니다.",
        ],
    }


def estimate_core_effects(
    *,
    core: dict[str, Any],
    current_threshold: int | None,
    suggested_threshold: int | None,
) -> dict[str, Any]:
    simulation = dict(core.get("threshold_simulation") or {})
    if not simulation.get("available") or suggested_threshold is None:
        return {
            "available": False,
            "assumptions": ["threshold simulation data가 없어서 core 전환 추정이 제한됩니다."],
        }

    scenario_by_threshold = {
        int(item.get("threshold")): item
        for item in simulation.get("scenarios") or []
        if item.get("threshold") is not None
    }
    scenario = scenario_by_threshold.get(int(suggested_threshold))
    if scenario is None:
        return {
            "available": False,
            "assumptions": ["추천 threshold에 대응하는 simulation scenario가 없습니다."],
        }

    return {
        "available": True,
        "current_threshold": current_threshold,
        "suggested_threshold": suggested_threshold,
        "current_hits": int(simulation.get("current_hits", 0) or 0),
        "estimated_threshold_hits": int(scenario.get("would_meet_threshold_count", 0) or 0),
        "estimated_additional_hits": int(scenario.get("delta_vs_current_threshold", 0) or 0),
        "flipped_row_count": int(scenario.get("flipped_row_count", 0) or 0),
        "flipped_symbols": dict(scenario.get("flipped_symbols") or {}),
        "assumptions": [
            "passed_count threshold만 바뀐다고 가정하고 기존 deep_eval row를 다시 분류했습니다.",
            "최종 주문/체결 여부는 score ranking, budget, runtime guard에 따라 달라질 수 있습니다.",
        ],
    }


def build_report(*, account: str, date: str | None, session: str) -> dict[str, Any]:
    brief = build_brief(account=account, date=date, session=session)
    nudges = list(brief.get("parameter_nudges") or [])
    health = brief["health"]
    budget = brief["budget"]
    core = brief["core"]

    sell_interval_nudge = _find_nudge(nudges, "SELL_CHECK_INTERVAL_SECONDS")
    request_reserve_nudge = _find_nudge(nudges, "API_BUY_SCAN_MIN_REQUEST_RESERVE")
    quote_reserve_nudge = _find_nudge(nudges, "API_BUY_SCAN_MIN_QUOTE_RESERVE")
    core_threshold_nudge = _find_nudge(nudges, "BUY_RULE_REQUIRED_PASS_COUNT_CORE")

    budget_effects = estimate_budget_effects(
        cycles=int((budget.get("meta") or {}).get("cycles_analyzed", 0) or 0),
        sell_partial=int(health.get("sell_watch_partial_count", 0) or 0),
        rate_limit_triggered=int(health.get("rate_limit_triggered_count", 0) or 0),
        buy_skip=int(health.get("buy_scan_rate_limit_skip_count", 0) or 0),
        sell_watch_rl_source=int((health.get("rate_limit_sources") or {}).get("sell_watch", 0) or 0),
        sell_interval_current=(
            int(sell_interval_nudge["current"]) if sell_interval_nudge else None
        ),
        sell_interval_suggested=(
            int(sell_interval_nudge["suggested"]) if sell_interval_nudge else None
        ),
        request_reserve_delta=(
            int(request_reserve_nudge["suggested"]) - int(request_reserve_nudge["current"])
            if request_reserve_nudge
            else 0
        ),
        quote_reserve_delta=(
            int(quote_reserve_nudge["suggested"]) - int(quote_reserve_nudge["current"])
            if quote_reserve_nudge
            else 0
        ),
    )
    core_effects = estimate_core_effects(
        core=core,
        current_threshold=(
            int(core_threshold_nudge["current"]) if core_threshold_nudge else None
        ),
        suggested_threshold=(
            int(core_threshold_nudge["suggested"]) if core_threshold_nudge else None
        ),
    )
    return {
        "account": account,
        "date": brief["date"],
        "session": brief["session"],
        "brief": brief,
        "budget_effects": budget_effects,
        "core_effects": core_effects,
        "bundle": [item for item in nudges if item.get("parameter") != "RULE_THRESHOLD_REVIEW"],
    }


def print_terminal(report: dict[str, Any]) -> None:
    budget_effects = report["budget_effects"]
    core_effects = report["core_effects"]

    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  parameter_change_simulation_report"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  account : {report['account']}")
    print(f"  date    : {report['date']}")
    print(f"  session : {report['session']}")
    print()

    print(_h("  1. Proposed Bundle"))
    for item in report.get("bundle") or []:
        print(f"  {item['parameter']}: {item['current']} -> {item['suggested']}")
        print(f"    {item['reason']}")
    print()

    print(_h("  2. Estimated Budget Relief"))
    cycles = int(budget_effects.get("cycles", 0) or 0)
    print(
        f"  rate_limit_triggered   : {report['brief']['health'].get('rate_limit_triggered_count')} -> "
        f"{budget_effects.get('estimated_rate_limit_triggered')} / {cycles}"
    )
    print(
        f"  sell_watch_partial     : {report['brief']['health'].get('sell_watch_partial_count')} -> "
        f"{budget_effects.get('estimated_sell_partial')} / {cycles}"
    )
    print(
        f"  buy_scan RL skip       : {report['brief']['health'].get('buy_scan_rate_limit_skip_count')} -> "
        f"{budget_effects.get('estimated_buy_skip')} / {cycles}"
    )
    print(
        f"  protected buy cycles   : {_ok(str(budget_effects.get('protected_buy_cycles', 0)))}"
    )
    print(_dim("  heuristic assumptions:"))
    for line in budget_effects.get("assumptions") or []:
        print(_dim(f"    - {line}"))
    print()

    print(_h("  3. Estimated Core Relief"))
    if not core_effects.get("available"):
        print(_dim("  core threshold simulation unavailable"))
    else:
        print(
            f"  threshold hits         : {core_effects.get('current_hits')} -> "
            f"{core_effects.get('estimated_threshold_hits')}"
        )
        print(
            f"  additional near-miss   : {_ok(str(core_effects.get('estimated_additional_hits')))}"
        )
        print(
            f"  flipped rows           : {core_effects.get('flipped_row_count')} | "
            f"symbols={core_effects.get('flipped_symbols') or {}}"
        )
        print(_dim("  heuristic assumptions:"))
        for line in core_effects.get("assumptions") or []:
            print(_dim(f"    - {line}"))
    print()

    print(_h("  4. Rollout Order"))
    print("  1. `SELL_CHECK_INTERVAL_SECONDS`, `API_BUY_SCAN_MIN_*_RESERVE`부터 먼저 적용합니다.")
    print("  2. 다음 장 후 `analyze_budget_bottlenecks`로 budget relief가 먼저 생겼는지 확인합니다.")
    print("  3. 그 다음 `BUY_RULE_REQUIRED_PASS_COUNT_CORE`를 적용하고 `analyze_core_bucket`으로 core conversion 변화를 확인합니다.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="추천 파라미터 bundle의 오프라인 추정 효과를 보여줍니다."
    )
    parser.add_argument("--account", default=get_account_scope_context()["account_signature"])
    parser.add_argument("--date", default="")
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(
        account=args.account,
        date=args.date or None,
        session=args.session,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_terminal(report)


if __name__ == "__main__":
    main()
