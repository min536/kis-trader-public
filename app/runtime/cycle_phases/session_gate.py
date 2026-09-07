"""``run_cycle`` session-gate phase — Stage B-3 slice G (국면 6).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 6행 / "Stage B
실행 사양"): the session 判定 + light-session (장전/주문불가) early-HOLD block of
``app.main.run_cycle`` is moved here **byte-verbatim** (logic/order/output
unchanged; only indentation adjusted). The single original early ``return`` at the
end of the light-session branch maps 1:1 to ``return True``; when the light
session is not taken the gate falls through to ``return False`` and ``run_cycle``
continues into the API-budget gate.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-F precedent): every ``app.main`` collaborator that a test may patch is
received as an **identically named keyword parameter**, bound at the call site
from ``run_cycle``'s enclosing/module scope. This keeps the moved body
source-identical and keeps the late-binding patch surface alive. The one pure,
un-patched utility (``phase_timing_summary``) is imported directly below — the
same call finalize.py makes. This module must NOT import ``app.main``.
"""

from __future__ import annotations

import time
from typing import Any

from app.reporting.console import phase_timing_summary as _phase_timing_summary


def run_session_gate(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    cycle_id: Any,
    session_check_started_perf: Any,
    get_korean_market_session: Any,
    get_korean_now: Any,
    build_market_session_console_lines: Any,
    _summarize_api_budget_state: Any,
    _should_run_light_session_cycle: Any,
    _print_premarket_wait_notice: Any,
    _print_cycle_conclusion: Any,
    _record_cycle_action: Any,
    _log_engine_event: Any,
    _write_slack_runtime_status_snapshot: Any,
) -> bool:
    ctx.session_status = get_korean_market_session()
    ctx.timing_summary["session_check"] = _phase_timing_summary(
        elapsed_ms=(time.perf_counter() - session_check_started_perf) * 1000
    )
    state["last_market_session"] = ctx.session_status.session
    _write_slack_runtime_status_snapshot(
        runtime_state=state,
        session_status=ctx.session_status,
    )
    state["current_regime"] = ctx.regime_state["current_regime"]
    state["regime_reason"] = ctx.regime_state["regime_reason"]
    state["regime_multiplier"] = ctx.regime_state["regime_multiplier"]
    ctx.market_open = ctx.session_status.order_allowed
    for line in build_market_session_console_lines(ctx.session_status):
        print(line)
    print()
    state["last_budget_status"] = _summarize_api_budget_state(
        api_budget_state,
        now=get_korean_now(),
    )
    if _should_run_light_session_cycle(settings=settings, session_status=ctx.session_status):
        ctx.regime_state = {
            **(ctx.regime_state or {}),
            "current_regime": "DATA_INSUFFICIENT",
            "regime_reason": (
                "장전 경량 대기 중이라 계좌 기반 regime 계산을 생략합니다."
                if ctx.session_status.session == "PREMARKET"
                else "주문 불가 세션이라 계좌 기반 regime 계산을 생략합니다."
            ),
            "regime_multiplier": None,
        }
        state["current_regime"] = ctx.regime_state["current_regime"]
        state["regime_reason"] = ctx.regime_state["regime_reason"]
        state["regime_multiplier"] = ctx.regime_state["regime_multiplier"]
        ctx.buy_scan_skipped_reason = f"session:{ctx.session_status.session.lower()}"
        ctx.buy_status_text = f"skipped({ctx.session_status.session.lower()})"
        ctx.sell_status_text = f"skipped({ctx.session_status.session.lower()})"
        _print_premarket_wait_notice(ctx.session_status)
        wait_action = (
            "WAITING_PREMARKET_OPEN"
            if ctx.session_status.session == "PREMARKET"
            else f"WAITING_{ctx.session_status.session}"
        )
        wait_reason = (
            "정규장 시작 전 대기 중입니다. 09:00 이후 자동으로 운영을 시작합니다."
            if ctx.session_status.session == "PREMARKET"
            else ctx.session_status.reason
        )
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=wait_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action=wait_action,
            reason=wait_reason,
            selected_symbol=state.get("last_selected_symbol"),
        )
        _log_engine_event(
            action=(
                "waiting_premarket_open"
                if ctx.session_status.session == "PREMARKET"
                else f"waiting_{ctx.session_status.session.lower()}"
            ),
            reason=wait_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True
    return False
