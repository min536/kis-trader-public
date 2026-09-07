"""``run_cycle`` regime phase — Stage B-3 slice I (국면 10).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 10행 / "Stage B
실행 사양"): the SELL post-processing / account-state payload / daily-pnl brake /
**regime 확정** / 성과 프린트 block of ``app.main.run_cycle`` — from the account-
balance interpretation print through the sell-cadence status text — is moved here
**byte-verbatim** (logic/order/output unchanged; only indentation adjusted). The
original block has no early ``return``; the phase always falls through, so this
function always returns ``False`` (convention consistency with the sibling gate
phases — ``run_cycle`` continues into the SELL-candidate/order phase 11).

This phase is gate C2's **single writer** of ``ctx.effective_buy_settings`` (the
regime-adjusted BUY budget); downstream phases only read it.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-F/G/H precedent): every ``app.main`` collaborator that a test may patch
(blocklist-checked against ``main_patched_names.txt``) is received as an
**identically named keyword parameter**, bound at the call site from
``run_cycle``'s enclosing/module scope. Notably:

- ``replace`` (``dataclasses.replace``) is injected, **not imported** —
  ``tests/test_lane_vs_legacy_equivalence.py`` patches ``main_module.replace``
  with a ``SimpleNamespace`` shim, so importing it here would bypass that patch
  and break lane-vs-legacy equivalence.
- ``_build_regime_state`` is the A2-보류 wrapper that still lives in
  ``app.main`` (it routes through ``_risk_build_regime_state`` /
  ``apply_high_risk_overrides``, both main-scope-patched by
  ``tests/test_autotuner_high_risk_activation.py``); it is passed by reference so
  that wrapper's patch surface stays intact.

The one non-blocklisted, un-patched presentation helper (``print_sell_decision``)
is imported directly — the same import ``account_snapshot.py`` makes. This module
must NOT import ``app.main``.
"""

from __future__ import annotations

from typing import Any

from app.reporting.console import print_sell_decision as _print_sell_decision


def run_regime_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    sell_check_due: Any,
    build_account_state_payload: Any,
    build_current_drawdown_state: Any,
    _build_today_realized_summary: Any,
    _build_daily_pnl_brake_state: Any,
    _build_daily_pnl_brake_observability: Any,
    _build_regime_state: Any,
    replace: Any,
    _print_account_balance_interpretation: Any,
    _print_regime_state: Any,
    _print_daily_pnl_brake_state: Any,
    _print_portfolio_positions: Any,
    _print_today_performance_summary: Any,
    _print_today_bought_tracking: Any,
) -> bool:
    _print_account_balance_interpretation(
        portfolio_snapshot=ctx.portfolio_snapshot,
        sell_analysis_results=ctx.sell_analysis_results,
    )
    account_state_payload = build_account_state_payload(
        portfolio_snapshot=ctx.portfolio_snapshot,
        sell_analysis_results=ctx.sell_analysis_results,
    )
    realized_summary = _build_today_realized_summary(settings)
    ctx.daily_pnl_brake_state = _build_daily_pnl_brake_state(
        state=state,
        settings=settings,
        current_equity_krw=int(account_state_payload["deployment_invariant_equity_krw"]),
        operating_equity_krw=int(account_state_payload["operating_equity_krw"]),
    )
    ctx.daily_pnl_brake_state = {
        **ctx.daily_pnl_brake_state,
        **_build_daily_pnl_brake_observability(
            brake_state=ctx.daily_pnl_brake_state,
            account_state_payload=account_state_payload,
            realized_summary=realized_summary,
            portfolio_snapshot=ctx.portfolio_snapshot,
        ),
    }
    drawdown_state = build_current_drawdown_state(
        current_equity_krw=int(account_state_payload["deployment_invariant_equity_krw"]),
    )
    ctx.regime_state = _build_regime_state(
        settings=settings,
        daily_pnl_brake_state=ctx.daily_pnl_brake_state,
        drawdown_state=drawdown_state,
    )
    state["current_regime"] = ctx.regime_state["current_regime"]
    state["regime_reason"] = ctx.regime_state["regime_reason"]
    state["regime_multiplier"] = ctx.regime_state["regime_multiplier"]
    _print_regime_state(regime_state=ctx.regime_state)
    ctx.effective_buy_settings = replace(
        settings,
        buy_max_budget_per_trade_krw=int(
            ctx.regime_state["effective_buy_max_budget_per_trade_krw"]
        ),
        buy_max_account_exposure_pct=float(
            ctx.regime_state["effective_buy_max_account_exposure_pct"]
        ),
        buy_max_qty_per_trade=int(ctx.regime_state["effective_buy_max_qty_per_trade"]),
        rebuy_cooldown_minutes=int(
            ctx.regime_state["effective_rebuy_cooldown_minutes"]
        ),
        same_symbol_max_buys_per_day=int(
            ctx.regime_state["effective_same_symbol_max_buys_per_day"]
        ),
        buy_daily_max_order_submissions=int(
            ctx.regime_state["effective_buy_daily_max_order_submissions"]
        ),
    )
    _print_daily_pnl_brake_state(
        brake_state=ctx.daily_pnl_brake_state,
        current_equity_krw=int(account_state_payload["deployment_invariant_equity_krw"]),
        settings=settings,
    )
    _print_portfolio_positions(ctx.portfolio_snapshot, ctx.sell_analysis_results)
    _print_today_performance_summary(
        portfolio_snapshot=ctx.portfolio_snapshot,
        sell_analysis_results=ctx.sell_analysis_results,
        settings=settings,
        realized_summary=realized_summary,
    )
    _print_today_bought_tracking(
        state=state,
        portfolio_snapshot=ctx.portfolio_snapshot,
        sell_analysis_results=ctx.sell_analysis_results,
    )

    if not sell_check_due:
        ctx.sell_status_text = "skipped(cadence)"
        ctx.sell_watch_skipped_reason = "cadence_not_reached"
        print("=== 매도 전략 판단 ===")
        print("[info] sell cadence: skipped | cadence not reached")
        print("이번 사이클은 SELL 체크 주기가 아니어서 보유 종목 매도 검토를 생략합니다.")
        print()
    elif ctx.portfolio_snapshot.held_positions and not ctx.sell_analysis_results and not ctx.market_open:
        ctx.sell_status_text = "skipped(order_window_closed)"
        ctx.sell_watch_skipped_reason = "order_window_closed"
        print("=== 매도 전략 판단 ===")
        print("[info] sell cadence: due | session not regular, quote-based SELL evaluation skipped")
        print("정규장 외 세션이라 보유 종목 현재가 기반 매도 전략 평가는 생략했습니다.")
        print()
    else:
        ctx.sell_status_text = (
            f"evaluated({ctx.sell_evaluated_count}/{ctx.portfolio_snapshot.position_count}{',partial' if ctx.sell_watch_partial else ''})"
            if ctx.portfolio_snapshot is not None
            else "evaluated"
        )
        ctx.sell_lane_running = False
        print("[info] sell cadence: due | SELL evaluation running")
        _print_sell_decision(
            ctx.sell_analysis_results,
            settings=settings,
            holdings_count=ctx.portfolio_snapshot.position_count if ctx.portfolio_snapshot is not None else 0,
            partial=ctx.sell_watch_partial,
            partial_reason=ctx.sell_watch_partial_reason,
            cursor_before=ctx.sell_watch_cursor_before,
            cursor_after=ctx.sell_watch_cursor_after,
            evaluated_symbols=ctx.sell_watch_evaluated_symbols,
            skipped_symbols=ctx.sell_watch_skipped_symbols,
        )

    return False
