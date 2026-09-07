from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

from app.core.time_utils import get_korean_now
from app.notifications.ops_warnings import send_detached_handler_warning
from app.pipeline.buy_lane import (
    BUY_SCAN_LANE_CONTROLLER,
    BuyLane,
    BuyLaneResult,
    BuyScanLaneController,
    BuyScanPrefetchStartResult,
    LiveQuoteLane,
)
from app.pipeline.cycle_budget import LaneBudget
from app.pipeline.intents import OrderGateResult, OrderIntent
from app.pipeline.order_gate import OrderGate, summarize_order_gate_result
from app.pipeline.runtime_adapters import build_default_lane_runtime_adapters
from app.pipeline.sell_lane import SellLane, SellLaneResult


def _prefetch_buy_scan_prices(**kwargs):
    from app.scanner.quote_account import prefetch_buy_scan_prices

    return prefetch_buy_scan_prices(**kwargs)


def _buy_min_remaining_budget_seconds(settings, context: dict[str, Any]) -> float:
    return max(
        0.0,
        float(
            context.get(
                "buy_min_remaining_budget_seconds",
                getattr(settings, "buy_scan_min_remaining_budget_seconds", 5.0),
            )
            or 0.0
        ),
    )


@dataclass(frozen=True)
class LaneSchedulerCycleResult:
    cycle_id: str
    sell_result: SellLaneResult
    buy_result: BuyLaneResult
    order_gate_result: OrderGateResult
    telemetry: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "sell_intent_symbol": (
                self.sell_result.intent.symbol if self.sell_result.intent else None
            ),
            "sell_skipped_reason": self.sell_result.skipped_reason,
            "buy_intent_symbol": (
                self.buy_result.intent.symbol if self.buy_result.intent else None
            ),
            "buy_scan_skipped_reason": self.buy_result.skipped_reason,
            "order_gate": summarize_order_gate_result(self.order_gate_result),
            "order_gate_order": tuple(
                f"{decision.intent_type}:{decision.symbol}"
                for decision in self.order_gate_result.decisions
            ),
            "telemetry": dict(self.telemetry),
        }


class LaneScheduler:
    def __init__(
        self,
        *,
        sell_lane: SellLane,
        buy_lane: BuyLane,
        order_gate: OrderGate,
        clock: Callable[[], float] = time.perf_counter,
        now_func: Callable[[], datetime] = get_korean_now,
    ) -> None:
        self.sell_lane = sell_lane
        self.buy_lane = buy_lane
        self.order_gate = order_gate
        self.clock = clock
        self.now_func = now_func

    def run_cycle(
        self,
        *,
        cycle_id: str,
        settings,
        sell_due: bool,
        buy_due: bool,
        buy_symbols: tuple[str, ...],
        execution_token: str,
        context: dict[str, Any] | None = None,
    ) -> LaneSchedulerCycleResult:
        started_at = self.clock()
        budget = LaneBudget(
            started_at=started_at,
            hard_budget_seconds=float(
                getattr(settings, "session_cycle_hard_budget_seconds", 60.0) or 60.0
            ),
            clock=self.clock,
        )
        context = context or {}
        budget.checkpoint("lane_scheduler_start")
        buy_min_remaining_seconds = _buy_min_remaining_budget_seconds(settings, context)
        overlap_enabled = bool(
            getattr(settings, "buy_scan_prefetch_overlap_enabled", False)
        )

        # S4 overlap: when enabled and the buy lane is eligible (due, budget not
        # expired, above the low-budget floor), START the read-only quote prefetch
        # on the controller's background worker BEFORE running the SELL lane so the
        # two overlap. Orders never flow through here — this is quote-only.
        overlap_start: BuyScanPrefetchStartResult | None = None
        overlap_join_wait_ms = 0.0
        overlap_active = (
            overlap_enabled
            and buy_due
            and not budget.expired()
            and budget.remaining_seconds() > buy_min_remaining_seconds
        )
        if overlap_active:
            overlap_start = self.buy_lane.controller.start_quote_prefetch(
                scan_id=f"{cycle_id}:buy_scan",
                symbols=buy_symbols,
                settings=settings,
                execution_token=execution_token,
                now=self.now_func(),
                prefetch_func=self.buy_lane.quote_lane._prefetch_func,
            )

        sell_result = self.sell_lane.run(
            due=sell_due,
            cycle_id=cycle_id,
            budget=budget,
            context=context,
        )
        # A background prefetch was actually started this cycle only when the
        # overlap start was allowed. If the budget then expires / drops to the
        # low floor during the SELL lane, the budget skip branches below must
        # still DRAIN that in-flight prefetch (join with a 0s timeout) so the
        # scan guard is released promptly (or cleanly marked detached), instead
        # of leaking a held guard + blind telemetry into the next cycle.
        prefetch_started = bool(
            overlap_start is not None and overlap_start.decision.allowed
        )

        def _drain_started_prefetch(skipped_reason: str) -> BuyLaneResult:
            nonlocal overlap_join_wait_ms
            join_result = self.buy_lane.controller.join_active_prefetch(
                timeout_seconds=0.0
            )
            overlap_join_wait_ms = float(join_result.join_wait_ms)
            return BuyLaneResult(
                due=True,
                skipped_reason=skipped_reason,
                started=True,
                guard_released=bool(join_result.guard_released),
                guard_release_reason=join_result.guard_release_reason,
                worker_detached=bool(join_result.worker_detached),
                cleanup_nonblocking=bool(join_result.cleanup_nonblocking),
                future_done=bool(join_result.future_done),
            )

        budget_skip_stage: str | None = None
        if buy_due and budget.expired():
            if prefetch_started:
                buy_result = _drain_started_prefetch("cycle_budget_exceeded")
            else:
                buy_result = BuyLaneResult(
                    due=True, skipped_reason="cycle_budget_exceeded"
                )
            budget.checkpoint("before_buy_scan_budget_exceeded")
        elif (
            buy_due
            and budget.remaining_seconds() <= buy_min_remaining_seconds
        ):
            budget_skip_stage = "before_buy_scan"
            if prefetch_started:
                buy_result = _drain_started_prefetch("cycle_budget_low")
            else:
                buy_result = BuyLaneResult(due=True, skipped_reason="cycle_budget_low")
            budget.checkpoint("before_buy_scan_budget_low")
        elif overlap_active and overlap_start is not None:
            context["buy_min_remaining_budget_seconds"] = buy_min_remaining_seconds
            if not overlap_start.decision.allowed:
                buy_result = BuyLaneResult(
                    due=True,
                    skipped_reason=overlap_start.decision.reason,
                    previous_scan_id=overlap_start.decision.previous_scan_id,
                )
            else:
                join_timeout_seconds = max(
                    0.0, budget.remaining_seconds() - buy_min_remaining_seconds
                )
                join_result = self.buy_lane.controller.join_active_prefetch(
                    timeout_seconds=join_timeout_seconds
                )
                overlap_join_wait_ms = float(join_result.join_wait_ms)
                buy_result = self.buy_lane.run_with_prefetched(
                    cycle_id=cycle_id,
                    settings=settings,
                    budget=budget,
                    join_result=join_result,
                    context=context,
                )
        else:
            context["buy_min_remaining_budget_seconds"] = buy_min_remaining_seconds
            buy_result = self.buy_lane.run(
                due=buy_due and not budget.expired(),
                cycle_id=cycle_id,
                symbols=buy_symbols,
                settings=settings,
                execution_token=execution_token,
                budget=budget,
                now=self.now_func(),
                context=context,
            )

        ready_intents: list[OrderIntent] = []
        if sell_result.intent is not None:
            ready_intents.append(sell_result.intent)
        if buy_result.intent is not None:
            ready_intents.append(buy_result.intent)

        gate_result = self.order_gate.process_ready_intents(
            tuple(ready_intents),
            now=self.now_func(),
            budget=budget,
            min_buy_remaining_seconds=buy_min_remaining_seconds,
        )
        budget.checkpoint("lane_scheduler_end")
        elapsed_ms = max(0.0, (self.clock() - started_at) * 1000.0)
        prefetch = buy_result.prefetch_result
        prefetch_request_count = (
            len(prefetch.completed_symbols) + len(prefetch.failed_symbols)
            if prefetch is not None
            else 0
        )
        prefetch_elapsed_ms = float(prefetch.elapsed_ms) if prefetch is not None else 0.0
        quote_age_max_ms: float | None = None
        quote_age_avg_ms: float | None = None
        if prefetch is not None and prefetch.price_data_by_symbol:
            # Real per-symbol ages when the prefetch recorded collection clocks
            # (same perf_counter family as self.clock in production); handmade
            # results without them fall back to the elapsed_ms approximation.
            collected = dict(
                getattr(prefetch, "collected_monotonic_by_symbol", {}) or {}
            )
            ages_ms = [
                max(0.0, (self.clock() - float(ts)) * 1000.0)
                for symbol, ts in collected.items()
                if symbol in prefetch.price_data_by_symbol
            ]
            if ages_ms:
                quote_age_max_ms = round(max(ages_ms), 1)
                quote_age_avg_ms = round(sum(ages_ms) / len(ages_ms), 1)
            else:
                quote_age_max_ms = round(prefetch_elapsed_ms, 1)
                quote_age_avg_ms = round(prefetch_elapsed_ms, 1)
        context_rate_limit_triggered = bool(context.get("rate_limit_triggered"))
        context_rate_limit_source = (
            str(context.get("rate_limit_source") or "").strip() or None
        )
        prefetch_rate_limit_triggered = (
            bool(prefetch.rate_limit_triggered) if prefetch is not None else False
        )
        rate_limit_triggered = (
            context_rate_limit_triggered or prefetch_rate_limit_triggered
        )
        rate_limit_source = context_rate_limit_source or (
            "buy_scan" if prefetch_rate_limit_triggered else None
        )
        sell_evaluated_count = int(
            context.get(
                "sell_evaluated_count",
                1 if sell_result.processed else 0,
            )
            or 0
        )
        sell_watch_partial = bool(context.get("sell_watch_partial", False))
        gate_summary = summarize_order_gate_result(gate_result)
        blocked_detached_count = int(
            gate_summary.get("order_gate_blocked_detached_count", 0) or 0
        )
        detached_handler_warning_sent = False
        if blocked_detached_count > 0:
            blocked_reason = next(
                (
                    decision.reason
                    for decision in gate_result.decisions
                    if decision.status == "blocked_detached_handler"
                ),
                "detached order handler still running",
            )
            detached_handler_warning_sent = send_detached_handler_warning(
                reason=blocked_reason,
                blocked_count=blocked_detached_count,
                notifier=context.get("ops_slack_notifier"),
            )
        order_gate_order = tuple(
            f"{decision.intent_type}:{decision.symbol}"
            for decision in gate_result.decisions
        )
        budget_exceeded_stage = (
            gate_result.budget_exceeded_stage
            or ("lane_scheduler_end" if budget.expired() else None)
        )
        budget_skip_stage = budget_skip_stage or gate_result.budget_skip_stage
        guard_state = self.buy_lane.controller.active_metadata()
        worker_ttl_expired = (
            buy_result.skipped_reason == "previous_scan_worker_stale"
            or str(guard_state.get("terminal_reason") or "") == "worker_ttl_expired"
            or bool(guard_state.get("stale"))
        )
        telemetry: dict[str, object] = {
            "lane_scheduler_enabled": True,
            "sell_lane_running": False,
            "buy_lane_running": self.buy_lane.controller.running,
            "cycle_budget_seconds": float(budget.hard_budget_seconds),
            "cycle_budget_remaining_ms": round(budget.remaining_seconds() * 1000.0, 1),
            "cycle_budget_exceeded": bool(budget.expired()),
            "budget_exceeded_stage": budget_exceeded_stage,
            "budget_skip_stage": budget_skip_stage,
            "cycle_elapsed_ms": round(elapsed_ms, 1),
            "sell_watch_skipped_reason": sell_result.skipped_reason,
            "sell_watch_overrun": False,
            "sell_evaluated_count": sell_evaluated_count,
            "sell_watch_total_holdings": int(
                context.get("sell_watch_total_holdings", 0) or 0
            ),
            "sell_watch_partial": sell_watch_partial,
            "sell_watch_partial_reason": context.get("sell_watch_partial_reason"),
            "sell_watch_budget_plan_limit": int(
                context.get("sell_watch_budget_plan_limit", 0) or 0
            ),
            "sell_watch_budget_plan_pressure_level": context.get(
                "sell_watch_budget_plan_pressure_level"
            ),
            "sell_watch_budget_plan_reason": context.get(
                "sell_watch_budget_plan_reason"
            ),
            "sell_watch_evaluated_symbols": tuple(
                context.get("sell_watch_evaluated_symbols") or ()
            ),
            "sell_watch_skipped_symbols": tuple(
                context.get("sell_watch_skipped_symbols") or ()
            ),
            "buy_scan_skipped_reason": buy_result.skipped_reason,
            "buy_scan_exception": (
                str(buy_result.exception)
                if getattr(buy_result, "exception", None) is not None
                else None
            ),
            "buy_scan_requested_count": (
                len(prefetch.requested_symbols) if prefetch is not None else 0
            ),
            "buy_scan_evaluated_count": (
                len(prefetch.price_data_by_symbol) if prefetch is not None else 0
            ),
            "buy_lane_previous_scan_id": buy_result.previous_scan_id,
            "buy_scan_guard_state": dict(guard_state),
            "worker_ttl_expired": bool(worker_ttl_expired),
            "stale_worker_recovered": bool(worker_ttl_expired),
            "buy_scan_guard_released": bool(buy_result.guard_released),
            "buy_scan_guard_release_reason": buy_result.guard_release_reason,
            "buy_quote_prefetch_deadline_seconds": (
                prefetch.deadline_seconds if prefetch is not None else None
            ),
            "buy_quote_prefetch_deadline_hit": (
                bool(prefetch.deadline_hit) if prefetch is not None else False
            ),
            "buy_quote_prefetch_request_count": prefetch_request_count,
            "buy_quote_prefetch_completed": (
                len(prefetch.completed_symbols) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_failed": (
                len(prefetch.failed_symbols) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_skipped_deadline": (
                len(prefetch.skipped_deadline_symbols) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_budget_skipped": (
                len(prefetch.budget_skipped_symbols) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_timeout_count": (
                int(prefetch.timeout_count) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_request_timeout_seconds": (
                float(prefetch.request_timeout_seconds) if prefetch is not None else 0.0
            ),
            "buy_quote_prefetch_max_attempts": (
                int(prefetch.max_attempts) if prefetch is not None else 0
            ),
            "buy_quote_prefetch_elapsed_ms": round(prefetch_elapsed_ms, 1),
            "buy_quote_prefetch_join_wait_ms": round(overlap_join_wait_ms, 1),
            "buy_quote_prefetch_success_ratio": (
                float(prefetch.success_ratio) if prefetch is not None else 0.0
            ),
            "buy_quote_prefetch_worker_detached": bool(buy_result.worker_detached),
            "buy_quote_prefetch_cleanup_nonblocking": bool(
                buy_result.cleanup_nonblocking
            ),
            "buy_quote_prefetch_future_done": bool(buy_result.future_done),
            "quote_age_max_ms": quote_age_max_ms,
            "quote_age_avg_ms": quote_age_avg_ms,
            "rate_limit_triggered": rate_limit_triggered,
            "rate_limit_source": rate_limit_source,
            "suspected_shared_rate_bucket": bool(
                rate_limit_triggered
                and str(rate_limit_source or "").strip()
                in {"", "unknown", "buy_scan", "sell_watch", "balance"}
            ),
            "order_gate_order": order_gate_order,
            "detached_handler_warning_sent": bool(detached_handler_warning_sent),
            **gate_summary,
        }
        return LaneSchedulerCycleResult(
            cycle_id=cycle_id,
            sell_result=sell_result,
            buy_result=buy_result,
            order_gate_result=gate_result,
            telemetry=telemetry,
        )


def run_lane_scheduler_cycle(
    settings,
    *,
    sell_check_due: bool,
    buy_scan_due: bool,
    scheduler_state: dict[str, object] | None = None,
    api_budget_state: dict[str, object] | None = None,
) -> LaneSchedulerCycleResult:
    scheduler_state = scheduler_state if scheduler_state is not None else {}
    hooks = dict(scheduler_state.get("lane_scheduler_hooks") or {})
    runtime_adapters = build_default_lane_runtime_adapters(
        settings,
        scheduler_state=scheduler_state,
        api_budget_state=api_budget_state,
    )
    clock = hooks.get("clock") or runtime_adapters.clock or time.perf_counter
    now_func = hooks.get("now_func") or runtime_adapters.now_func or get_korean_now
    controller = (
        hooks.get("buy_controller")
        or runtime_adapters.buy_controller
        or BUY_SCAN_LANE_CONTROLLER
    )
    if not isinstance(controller, BuyScanLaneController):
        raise TypeError("buy_controller must be a BuyScanLaneController")

    quote_lane = LiveQuoteLane(
        prefetch_func=hooks.get("prefetch_func")
        or hooks.get("live_quote_prefetch_func")
        or runtime_adapters.quote_prefetch_func
        or _prefetch_buy_scan_prices
    )
    sell_lane = SellLane(
        intent_factory=hooks.get("sell_intent_factory")
        or runtime_adapters.sell_intent_factory
    )
    buy_lane = BuyLane(
        controller=controller,
        quote_lane=quote_lane,
        intent_factory=hooks.get("buy_intent_factory")
        or runtime_adapters.buy_intent_factory,
    )
    handler_timeout_enabled = hooks.get(
        "order_gate_handler_timeout_enabled",
        runtime_adapters.order_gate_handler_timeout_enabled,
    )
    gate = OrderGate(
        buy_handler=hooks.get("buy_order_handler")
        or runtime_adapters.buy_order_handler,
        sell_handler=hooks.get("sell_order_handler")
        or runtime_adapters.sell_order_handler,
        clock=now_func,
        handler_timeout_enabled=bool(handler_timeout_enabled),
    )
    adapter_source = "hook" if any(
        key in hooks
        for key in (
            "prefetch_func",
            "live_quote_prefetch_func",
            "sell_intent_factory",
            "buy_intent_factory",
            "buy_symbols",
            "execution_token",
        )
    ) else "default"
    handler_source = "hook" if any(
        key in hooks for key in ("sell_order_handler", "buy_order_handler")
    ) else "default"
    scheduler = LaneScheduler(
        sell_lane=sell_lane,
        buy_lane=buy_lane,
        order_gate=gate,
        clock=clock,
        now_func=now_func,
    )
    cycle_id = str(scheduler_state.get("cycle_id") or f"lane-{int(clock() * 1000)}")
    execution_token = str(
        hooks.get("execution_token") or runtime_adapters.execution_token
    )
    result = scheduler.run_cycle(
        cycle_id=cycle_id,
        settings=settings,
        sell_due=sell_check_due,
        buy_due=buy_scan_due,
        buy_symbols=tuple(hooks.get("buy_symbols") or runtime_adapters.buy_symbols),
        execution_token=execution_token,
        context={
            **dict(runtime_adapters.context),
            **dict(hooks.get("context") or {}),
            "scheduler_state": scheduler_state,
            "api_budget_state": api_budget_state,
            "sell_check_due": bool(sell_check_due),
            "buy_scan_due": bool(buy_scan_due),
            "execution_token": execution_token,
            "buy_min_remaining_budget_seconds": float(
                hooks.get(
                    "buy_min_remaining_budget_seconds",
                    getattr(settings, "buy_scan_min_remaining_budget_seconds", 5.0),
                )
                or 0.0
            ),
        },
    )
    result.telemetry["adapter_source"] = adapter_source
    result.telemetry["handler_source"] = handler_source
    scheduler_state["lane_scheduler_result"] = result.to_dict()
    scheduler_state["lane_scheduler_telemetry"] = dict(result.telemetry)
    if api_budget_state is not None:
        api_budget_state["last_lane_scheduler_telemetry"] = dict(result.telemetry)
        api_budget_state["last_timing_summary"] = dict(result.telemetry)
    return result
