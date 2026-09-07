"""``run_cycle`` BUY-order phase — Stage B-3 slice M (国면 15), Stage B final extract.

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (国면 지도 15행 / "Stage B
실행 사양"): the BUY-order execution block of ``app.main.run_cycle`` — the
cycle-hard-budget skip gate, the ``buy_flow_context`` seed, the block-local
``_apply_buy_flow_context`` / ``run_buy_order_flow_handler`` defs, and the
``OrderGate(buy_handler=...).process_ready_intents`` submission with its success /
order-gate-skip / ``except: _apply_buy_flow_context(); raise`` paths — is moved
here **byte-verbatim** (logic / order / side effects unchanged; only the leading
indentation is dedented by 4 spaces from the ``run_cycle`` ``try`` body). This is
the tail of ``run_cycle``'s single top-level ``try`` body — after it the E block
(top-level ``except``) and F block (``finally``) stay in ``run_cycle``.

Early-``return``/``raise`` mapping (国면 outcome -> ``run_cycle`` signal):
  * budget skip (orig :1148) -> ``return True`` (``run_cycle`` returns; finally
    still finalizes)
  * order-gate skip (``buy_flow_result is None``, orig :1277) -> ``return True``
  * success end (orig :1296) -> ``return True``
  * ``except Exception`` re-``raise`` (orig :1299) -> re-``raise`` (unchanged;
    ``_apply_buy_flow_context()`` runs first)
Every path returns or raises, so the phase never falls through the block. The
trailing ``return False`` is unreachable-by-construction — it exists only to make
a future edit that drops a ``return`` a fall-through into "continue" rather than
"implicitly return ``None``", matching the sibling phase modules' contract that a
falsy return means "``run_cycle`` continues". The block currently has no such
fall-through.

M-1 (closure reference injection): ``run_buy_order_flow_handler`` and
``_apply_buy_flow_context`` are block-local defs that move with the block. The
collaborators the handler forwards to ``_buy_flow.run_buy_order_flow`` —
``sell_order_flow`` (the ``run_cycle`` OrderGate SELL closure),
``execution_budget_wait``, ``_send_order_slack_notification`` — plus
``record_order_gate_summary`` stay in ``run_cycle`` and are injected **by
reference** as identically named keyword parameters; this module never
reconstructs or rewires them. ``OrderGate`` creation moves here freely (the
detached-handler registry is ``order_gate.py`` module-global, so the construction
site is irrelevant to it).

M-2 (dual-path ctx reflection): the ``try / except Exception:
_apply_buy_flow_context(); raise`` skeleton is preserved verbatim — the except
path reflects the partial ``buy_flow_context`` into ``ctx`` before re-raising so
the ``run_cycle`` E/finalize sees the partial snapshot.

L-4 / deps discipline (§3 "deps 번들 주입" — individual keyword params, mirroring
the sibling ``run_buy_scan_phase`` / ``run_sell_order_phase`` splits): every
``app.main`` collaborator a test may patch on ``main_module`` (blocklist-checked)
is received as an identically named keyword parameter, bound at the ``run_cycle``
call site — ``get_korean_now`` / ``_record_cycle_action`` / ``_print_cycle_conclusion``
/ ``_send_order_slack_notification``. ``get_korean_now`` is passed **by reference**
(never pre-called) so ``side_effect`` mock sequences keep their call order. The
``record_order_gate_summary`` / ``sell_order_flow`` /
``execution_budget_wait`` run_cycle closures and the run_cycle locals (``settings``
/ ``state`` / ``api_budget_state`` / ``cycle_id`` / ``cycle_budget`` /
``order_type`` / ``sell_check_due``) are likewise injected.

``_buy_flow`` (the ``app.execution.buy_flow`` module alias), ``OrderGate`` /
``BuyIntent`` (from ``app.pipeline``), and ``timedelta`` are imported directly —
none is patched on ``app.main`` scope in the suite. This module must NOT import
``app.main``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import app.execution.buy_flow as _buy_flow
from app.pipeline import BuyIntent, OrderGate


def run_buy_order_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    cycle_id: Any,
    cycle_budget: Any,
    order_type: Any,
    sell_check_due: Any,
    record_order_gate_summary: Any,
    sell_order_flow: Any,
    execution_budget_wait: Any,
    get_korean_now: Any,
    _record_cycle_action: Any,
    _print_cycle_conclusion: Any,
    _send_order_slack_notification: Any,
) -> bool:
    if cycle_budget.should_skip_stage(min_remaining_seconds=2.0):
        ctx.buy_scan_skipped_reason = ctx.buy_scan_skipped_reason or "cycle_budget_low"
        ctx.buy_status_text = "skipped(cycle_budget_low)"
        ctx.cycle_budget_exceeded = cycle_budget.exceeded
        ctx.budget_exceeded_stage = "before_buy_order"
        _print_cycle_conclusion(
            side="HOLD",
            display_name=ctx.selected_candidate.display_name,
            reason="남은 cycle hard budget이 부족해 BUY 주문 검토를 다음 tick으로 넘깁니다.",
            planned_qty=0,
        )
        _record_cycle_action(
            state,
            action="HOLD_CYCLE_BUDGET_BUY_ORDER",
            reason="남은 cycle hard budget이 부족해 BUY 주문 검토를 넘겼습니다.",
            selected_symbol=ctx.selected_candidate.symbol,
        )
        return True

    buy_flow_context: dict[str, object] = {
        "execution_snapshot": ctx.execution_snapshot,
        "position_sizing": ctx.position_sizing,
        "sell_position_sizing": ctx.sell_position_sizing,
        "buy_risk_guard_payload": ctx.buy_risk_guard_payload,
        "sell_risk_guard_payload": ctx.sell_risk_guard_payload,
        "rebalance_buy_preview": ctx.rebalance_buy_preview,
        "quality_rebalance_preview": ctx.quality_rebalance_preview,
        "active_sell_analysis": ctx.active_sell_analysis,
        "rebalance_selection_ms": ctx.rebalance_selection_ms,
        "execution_tail_backoff_drain_ms": ctx.execution_tail_backoff_drain_ms,
        "rate_limit_source": ctx.rate_limit_source,
    }

    def _apply_buy_flow_context() -> None:
        ctx.execution_snapshot = buy_flow_context.get("execution_snapshot")
        ctx.position_sizing = buy_flow_context.get("position_sizing")
        ctx.sell_position_sizing = buy_flow_context.get("sell_position_sizing")
        ctx.buy_risk_guard_payload = buy_flow_context.get("buy_risk_guard_payload")
        ctx.sell_risk_guard_payload = buy_flow_context.get("sell_risk_guard_payload")
        ctx.rebalance_buy_preview = buy_flow_context.get("rebalance_buy_preview")
        ctx.quality_rebalance_preview = buy_flow_context.get("quality_rebalance_preview")
        ctx.active_sell_analysis = buy_flow_context.get("active_sell_analysis")
        ctx.rebalance_selection_ms = buy_flow_context.get("rebalance_selection_ms")
        ctx.execution_tail_backoff_drain_ms = float(
            buy_flow_context.get("execution_tail_backoff_drain_ms", 0.0) or 0.0
        )
        ctx.rate_limit_source = (
            str(buy_flow_context.get("rate_limit_source") or "").strip() or None
        )

    def run_buy_order_flow_handler(_intent=None):
        return _buy_flow.run_buy_order_flow(
            state=state,
            settings=settings,
            effective_buy_settings=ctx.effective_buy_settings,
            token=ctx.token,
            portfolio_snapshot=ctx.portfolio_snapshot,
            selected_candidate=ctx.selected_candidate,
            scan_results=ctx.scan_results,
            sell_analysis_results=ctx.sell_analysis_results,
            selection_details=ctx.selection_details,
            regime_state=ctx.regime_state,
            daily_pnl_brake_state=ctx.daily_pnl_brake_state,
            market_open=ctx.market_open,
            session_status=ctx.session_status,
            order_type=order_type,
            cycle_id=cycle_id,
            sell_check_due=sell_check_due,
            sell_watch_partial=ctx.sell_watch_partial,
            sell_watch_partial_reason=ctx.sell_watch_partial_reason,
            api_budget_state=api_budget_state,
            timing_summary=ctx.timing_summary,
            rate_limit_source=ctx.rate_limit_source,
            run_sell_order_flow=sell_order_flow,
            send_order_slack_notification=_send_order_slack_notification,
            wait_for_execution_request_budget=execution_budget_wait,
            flow_context=buy_flow_context,
        )

    try:
        if bool(getattr(settings, "order_gate_enabled", True)):
            intent_created_at = get_korean_now()
            intent_ttl_seconds = max(
                1.0,
                min(
                    cycle_budget.remaining_seconds,
                    float(getattr(settings, "buy_scan_total_budget_seconds", 25.0) or 25.0),
                ),
            )
            buy_intent = BuyIntent(
                intent_id=f"{cycle_id}:buy:{ctx.selected_candidate.symbol}",
                source_cycle_id=cycle_id,
                source_lane="buy_scan",
                symbol=ctx.selected_candidate.symbol,
                reason=str(ctx.selection_details.get("selection_reason") or "buy candidate"),
                created_at=intent_created_at,
                expires_at=intent_created_at + timedelta(seconds=intent_ttl_seconds),
                quote_age_ms=ctx.quote_age_max_ms,
                candidate_score=float(getattr(ctx.selected_candidate, "score", 0.0) or 0.0),
            )
            gate_result = OrderGate(
                buy_handler=run_buy_order_flow_handler,
            ).process_ready_intents((buy_intent,), now=get_korean_now())
            record_order_gate_summary(gate_result)
            decision = gate_result.last_decision
            if (
                decision is not None
                and decision.status == "failed"
                and "exception" in decision.payload
            ):
                raise decision.payload["exception"]
            buy_flow_result = (
                decision.payload.get("handler_result")
                if decision is not None and decision.status == "processed"
                else None
            )
            if buy_flow_result is None:
                ctx.buy_scan_skipped_reason = (
                    ctx.buy_scan_skipped_reason
                    or ctx.order_gate_last_skip_reason
                    or "order_gate_skipped"
                )
                ctx.buy_status_text = f"skipped({ctx.buy_scan_skipped_reason})"
                _record_cycle_action(
                    state,
                    action="HOLD_ORDER_GATE_BUY",
                    reason=ctx.buy_scan_skipped_reason,
                    selected_symbol=ctx.selected_candidate.symbol,
                )
                return True
        else:
            buy_flow_result = run_buy_order_flow_handler()
        buy_flow_context.update(
            {
                "execution_snapshot": buy_flow_result.execution_snapshot,
                "position_sizing": buy_flow_result.position_sizing,
                "sell_position_sizing": buy_flow_result.sell_position_sizing,
                "buy_risk_guard_payload": buy_flow_result.buy_risk_guard_payload,
                "sell_risk_guard_payload": buy_flow_result.sell_risk_guard_payload,
                "rebalance_buy_preview": buy_flow_result.rebalance_buy_preview,
                "quality_rebalance_preview": buy_flow_result.quality_rebalance_preview,
                "active_sell_analysis": buy_flow_result.active_sell_analysis,
                "rebalance_selection_ms": buy_flow_result.rebalance_selection_ms,
                "execution_tail_backoff_drain_ms": buy_flow_result.execution_tail_backoff_drain_ms,
                "rate_limit_source": buy_flow_result.rate_limit_source,
            }
        )
        _apply_buy_flow_context()
        return True
    except Exception:
        _apply_buy_flow_context()
        raise
    return False
