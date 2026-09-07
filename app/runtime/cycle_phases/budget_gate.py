"""``run_cycle`` API-budget-gate phase — Stage B-3 slice G (국면 7).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 7행 / "Stage B
실행 사양"): the API-budget gate block of ``app.main.run_cycle`` — BUY reserve
activation plus the four early-HOLD paths (transient backoff / rate-limit backoff
with SELL-due drain / re-check / request-budget) — is moved here **byte-verbatim**
(logic/order/output unchanged; only indentation adjusted). Each of the four
original early ``return`` points maps 1:1 to ``return True``; the SELL-due backoff
drain ``time.sleep`` is moved intact (timing behavior unchanged). When no gate
fires the block falls through to ``return False`` and ``run_cycle`` continues into
the token-issuance phase.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-F precedent): every ``app.main`` collaborator that a test may patch is
received as an **identically named keyword parameter**, bound at the call site
from ``run_cycle``'s enclosing/module scope (including the run_cycle-local closure
``note``). This keeps the moved body source-identical and keeps the late-binding
patch surface alive. ``time`` (blocklist-checked: not patched on ``app.main``) is
imported directly — the same import finalize.py makes. This module must NOT
import ``app.main``.
"""

from __future__ import annotations

import time
from typing import Any


def run_budget_gate(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    scheduler_state: Any,
    sell_check_due: Any,
    cycle_id: Any,
    note: Any,
    get_korean_now: Any,
    _buy_scan_reserve_active: Any,
    _api_budget_transient_backoff_active: Any,
    _api_budget_transient_backoff_remaining_seconds: Any,
    _api_budget_backoff_active: Any,
    _api_budget_backoff_remaining_seconds: Any,
    _api_budget_can_request: Any,
    _print_cycle_conclusion: Any,
    _record_cycle_action: Any,
    _log_engine_event: Any,
) -> bool:
    ctx.buy_scan_budget_reserved = _buy_scan_reserve_active(
        settings=settings,
        session_status=ctx.session_status,
        buy_scan_due=ctx.buy_scan_due,
    )
    if ctx.buy_scan_budget_reserved:
        reserve_reason = (
            "REGULAR 세션에서 BUY scan due 이므로 최소 BUY reserve를 남기고 SELL watch를 진행합니다."
        )
        print(
            "[info] BUY reserve active | "
            f"request>={settings.api_buy_scan_min_request_reserve}, "
            f"quote>={settings.api_buy_scan_min_quote_reserve}"
        )
        _log_engine_event(
            action="buy_scan_budget_reserved",
            reason=reserve_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        print()
    budget_now = get_korean_now()
    if _api_budget_transient_backoff_active(api_budget_state, now=budget_now):
        ctx.buy_scan_skipped_reason = "api_transient_backoff"
        ctx.buy_status_text = "skipped(api_transient_backoff)"
        ctx.sell_status_text = "skipped(api_transient_backoff)"
        transient_remaining = _api_budget_transient_backoff_remaining_seconds(
            api_budget_state,
            now=budget_now,
        )
        transient_source = str(
            api_budget_state.get("last_transient_error_source") or "unknown"
        )
        transient_reason = (
            "직전 KIS API/network 일시 오류로 잠시 backoff 중이라 "
            "이번 tick의 조회를 건너뜁니다. "
            f"(source={transient_source}, remaining={round(transient_remaining, 1)}s)"
        )
        note("DEGRADED", transient_reason)
        print()
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=transient_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_API_TRANSIENT_BACKOFF",
            reason=transient_reason,
            selected_symbol=state.get("last_selected_symbol"),
        )
        _log_engine_event(
            action="skipped_api_transient_backoff",
            reason=transient_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True
    if _api_budget_backoff_active(api_budget_state, now=budget_now):
        scheduler_decision = str(scheduler_state.get("decision") or "")
        _cycle_start_backoff_drain_seconds = _api_budget_backoff_remaining_seconds(
            api_budget_state,
            now=budget_now,
        )
        if (
            sell_check_due
            and scheduler_decision == "API_BACKOFF_WAIT"
            and _cycle_start_backoff_drain_seconds > 0
        ):
            print(
                "[info] SELL watch waiting for API backoff drain"
                f" | wait={round(_cycle_start_backoff_drain_seconds * 1000)}ms"
            )
            note(
                "INFO",
                "SELL watch due 상태라 남은 rate limit backoff를 소진한 뒤 보유 종목 점검을 진행합니다.",
            )
            time.sleep(_cycle_start_backoff_drain_seconds)
            ctx.sell_watch_backoff_drain_ms = round(
                _cycle_start_backoff_drain_seconds * 1000,
                1,
            )
            ctx.timing_summary["sell_watch_backoff_drain_ms"] = (
                ctx.sell_watch_backoff_drain_ms
            )
            budget_now = get_korean_now()
    if _api_budget_backoff_active(api_budget_state, now=budget_now):
        ctx.buy_scan_skipped_reason = "api_backoff"
        ctx.buy_status_text = "skipped(api_backoff)"
        ctx.sell_status_text = "skipped(api_backoff)"
        backoff_reason = (
            "직전 KIS rate limit 경고로 잠시 backoff 중이라 이번 tick의 무거운 조회를 건너뜁니다."
        )
        note("DEGRADED", backoff_reason)
        print()
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=backoff_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_API_BACKOFF",
            reason=backoff_reason,
            selected_symbol=state.get("last_selected_symbol"),
        )
        _log_engine_event(
            action="skipped_buy_scan_budget_limited",
            reason=backoff_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True
    if not _api_budget_can_request(api_budget_state, now=budget_now):
        ctx.buy_scan_skipped_reason = "api_request_budget_wait"
        ctx.buy_status_text = "skipped(api_budget_wait)"
        ctx.sell_status_text = "skipped(api_budget_wait)"
        budget_reason = "현재 tick의 API request 예산이 부족해 이번 사이클은 대기합니다."
        note("DEGRADED", budget_reason)
        print()
        _print_cycle_conclusion(
            side="HOLD",
            display_name="-",
            reason=budget_reason,
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_API_BUDGET_WAIT",
            reason=budget_reason,
            selected_symbol=state.get("last_selected_symbol"),
        )
        _log_engine_event(
            action="skipped_buy_scan_budget_limited",
            reason=budget_reason,
            cycle_id=cycle_id,
            market_open=ctx.market_open,
            market_session=ctx.session_status.session,
        )
        return True
    return False
