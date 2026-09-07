from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import datetime
from typing import Any

from app.core.time_utils import get_korean_now
from app.pipeline.intents import (
    BUY_INTENT,
    SELL_INTENT,
    OrderGateDecision,
    OrderGateResult,
    OrderIntent,
)

OrderHandler = Callable[[OrderIntent], Any]


class OrderGateHandlerTimeout(TimeoutError):
    """Raised when an order handler exceeds the remaining cycle budget.

    Subclasses ``TimeoutError`` for backward compatibility, but lets the gate
    distinguish a genuine budget/future timeout from a network read timeout
    raised *inside* the handler (which is also a builtin ``TimeoutError``).
    """


# Process-wide registry of order handlers that timed out but whose worker
# thread is still running (Python cannot kill it). ``OrderGate`` instances are
# constructed fresh every cycle, so the registry is module-level to survive
# across cycles and prevent a fresh gate from submitting a duplicate order while
# a detached handler may still be writing.
_DETACHED_HANDLERS: list[dict[str, Any]] = []
_DETACHED_HANDLERS_LOCK = threading.Lock()


def reset_detached_handlers_for_tests() -> None:
    """Clear the detached-handler registry (test isolation helper)."""
    with _DETACHED_HANDLERS_LOCK:
        _DETACHED_HANDLERS.clear()


def _register_detached_handler(*, intent: OrderIntent, future: Future) -> None:
    entry = {
        "intent_id": intent.intent_id,
        "intent_type": intent.intent_type,
        "symbol": intent.symbol,
        "future": future,
    }
    with _DETACHED_HANDLERS_LOCK:
        _DETACHED_HANDLERS.append(entry)

    def _drop_when_done(_completed: Future) -> None:
        # Self-purge as soon as the detached worker actually finishes, so a
        # completed handler stops blocking subsequent cycles even before the
        # next process_ready_intents() purge sweep runs.
        with _DETACHED_HANDLERS_LOCK:
            try:
                _DETACHED_HANDLERS.remove(entry)
            except ValueError:
                pass

    future.add_done_callback(_drop_when_done)


def _purge_and_list_live_detached_handlers() -> list[dict[str, Any]]:
    with _DETACHED_HANDLERS_LOCK:
        live = [
            entry
            for entry in _DETACHED_HANDLERS
            if not entry["future"].done()
        ]
        _DETACHED_HANDLERS[:] = live
        return list(live)


class OrderGate:
    """Single logical writer for same-process BUY/SELL order intents."""

    def __init__(
        self,
        *,
        buy_handler: OrderHandler | None = None,
        sell_handler: OrderHandler | None = None,
        clock: Callable[[], datetime] = get_korean_now,
        handler_timeout_enabled: bool = False,
    ) -> None:
        self._buy_handler = buy_handler
        self._sell_handler = sell_handler
        self._clock = clock
        self._handler_timeout_enabled = bool(handler_timeout_enabled)

    @staticmethod
    def sort_intents(intents: Iterable[OrderIntent]) -> tuple[OrderIntent, ...]:
        return tuple(
            sorted(
                intents,
                key=lambda intent: (
                    -int(intent.priority),
                    intent.created_at,
                    intent.intent_id,
                ),
            )
        )

    def process_ready_intents(
        self,
        intents: Iterable[OrderIntent],
        *,
        now: datetime | None = None,
        budget: Any | None = None,
        min_buy_remaining_seconds: float = 0.0,
        budget_stage_prefix: str = "order_gate",
    ) -> OrderGateResult:
        ready_intents = self.sort_intents(intents)
        processed_at = now or self._clock()
        decisions: list[OrderGateDecision] = []
        stopped_due_to_budget = False
        budget_exceeded_stage: str | None = None
        budget_skip_stage: str | None = None

        live_detached = _purge_and_list_live_detached_handlers()
        if live_detached:
            inflight = live_detached[0]
            reason = (
                "detached order handler still running: "
                f"{inflight['intent_type']}:{inflight['symbol']}"
            )
            blocked_decisions = tuple(
                self._decision(
                    intent,
                    status="blocked_detached_handler",
                    reason=reason,
                    processed_at=processed_at,
                )
                for intent in ready_intents
            )
            return OrderGateResult(
                decisions=blocked_decisions,
                queue_depth_before=len(ready_intents),
                stopped_due_to_budget=False,
                budget_exceeded=bool(self._budget_expired(budget)),
            )

        for index, intent in enumerate(ready_intents):
            if self._budget_expired(budget):
                stopped_due_to_budget = True
                budget_exceeded_stage = f"{budget_stage_prefix}_before_{intent.intent_type.lower()}"
                decisions.append(
                    self._decision(
                        intent,
                        status="budget_exceeded",
                        reason="cycle budget exhausted before order gate processing",
                        processed_at=processed_at,
                    )
                )
                break
            if (
                intent.intent_type == BUY_INTENT
                and self._budget_remaining_seconds(budget)
                <= max(0.0, float(min_buy_remaining_seconds))
            ):
                stopped_due_to_budget = True
                budget_skip_stage = f"{budget_stage_prefix}_before_buy"
                decisions.append(
                    self._decision(
                        intent,
                        status="budget_skipped",
                        reason="cycle_budget_low_before_buy_order",
                        processed_at=processed_at,
                    )
                )
                break
            if intent.is_expired(now=processed_at):
                decisions.append(
                    self._decision(
                        intent,
                        status="expired",
                        reason="intent expired before order gate processing",
                        processed_at=processed_at,
                    )
                )
                continue

            handler = self._handler_for(intent)
            if handler is None:
                decisions.append(
                    self._decision(
                        intent,
                        status="no_op",
                        reason=f"no handler registered for {intent.intent_type}",
                        processed_at=processed_at,
                    )
                )
                continue

            try:
                handler_result = self._run_handler(handler, intent, budget=budget)
            except OrderGateHandlerTimeout as exc:
                stopped_due_to_budget = True
                budget_exceeded_stage = f"{budget_stage_prefix}_handler_timeout"
                decisions.append(
                    self._decision(
                        intent,
                        status="handler_timeout",
                        reason="order handler exceeded remaining cycle budget",
                        processed_at=processed_at,
                        payload={"exception": exc},
                    )
                )
                for remaining_intent in ready_intents[index + 1 :]:
                    decisions.append(
                        self._decision(
                            remaining_intent,
                            status="budget_exceeded",
                            reason="cycle budget exhausted after handler timeout",
                            processed_at=processed_at,
                        )
                    )
                break
            except Exception as exc:
                decisions.append(
                    self._decision(
                        intent,
                        status="failed",
                        reason=str(exc),
                        processed_at=processed_at,
                        payload={"exception": exc},
                    )
                )
                continue

            if isinstance(handler_result, OrderGateDecision):
                decisions.append(handler_result)
                continue

            decisions.append(
                self._decision(
                    intent,
                    status="processed",
                    reason="delegated to existing order flow",
                    processed_at=processed_at,
                    payload={"handler_result": handler_result},
                )
            )
            if self._budget_expired(budget):
                stopped_due_to_budget = index < len(ready_intents) - 1
                budget_exceeded_stage = f"{budget_stage_prefix}_after_{intent.intent_type.lower()}"
                if stopped_due_to_budget:
                    continue_intent = ready_intents[index + 1]
                    decisions.append(
                        self._decision(
                            continue_intent,
                            status="budget_exceeded",
                            reason="cycle budget exhausted after prior order handler",
                            processed_at=processed_at,
                        )
                    )
                break

        return OrderGateResult(
            decisions=tuple(decisions),
            queue_depth_before=len(ready_intents),
            stopped_due_to_budget=stopped_due_to_budget,
            budget_exceeded=bool(self._budget_expired(budget)),
            budget_exceeded_stage=budget_exceeded_stage,
            budget_skip_stage=budget_skip_stage,
        )

    def _handler_for(self, intent: OrderIntent) -> OrderHandler | None:
        if intent.intent_type == SELL_INTENT:
            return self._sell_handler
        if intent.intent_type == BUY_INTENT:
            return self._buy_handler
        return None

    def _run_handler(
        self,
        handler: OrderHandler,
        intent: OrderIntent,
        *,
        budget: Any | None,
    ) -> Any:
        if not self._handler_timeout_enabled:
            return handler(intent)
        timeout_seconds = self._budget_remaining_seconds(budget)
        if timeout_seconds <= 0.0:
            raise OrderGateHandlerTimeout()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="order-gate")
        future = executor.submit(handler, intent)
        try:
            # This bounds scheduler wait time and cycle budget. Python cannot
            # forcibly stop a handler that is already running, so order flows
            # must remain safe to finish after the scheduler has timed out.
            return future.result(timeout=timeout_seconds)
        except TimeoutError:
            # future.result() re-raises the handler's own exception too, and a
            # handler network read timeout IS a builtin TimeoutError. Only when
            # the future has NOT completed is this a genuine future/budget
            # timeout; if the future is done, the handler raised it -> re-raise
            # as-is so it falls into the generic "failed" path.
            if future.done():
                raise
            # The worker keeps running detached. Register it so the next cycle's
            # fresh gate refuses to submit a duplicate order while it may still
            # be writing.
            _register_detached_handler(intent=intent, future=future)
            executor.shutdown(wait=False, cancel_futures=True)
            raise OrderGateHandlerTimeout()
        finally:
            if future.done():
                executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _budget_remaining_seconds(budget: Any | None) -> float:
        if budget is None:
            return float("inf")
        remaining = getattr(budget, "remaining_seconds", None)
        if callable(remaining):
            return max(0.0, float(remaining()))
        if remaining is not None:
            return max(0.0, float(remaining))
        return float("inf")

    @staticmethod
    def _budget_expired(budget: Any | None) -> bool:
        if budget is None:
            return False
        expired = getattr(budget, "expired", None)
        if callable(expired):
            return bool(expired())
        exceeded = getattr(budget, "exceeded", None)
        if exceeded is not None:
            return bool(exceeded)
        return OrderGate._budget_remaining_seconds(budget) <= 0.0

    @staticmethod
    def _decision(
        intent: OrderIntent,
        *,
        status: str,
        reason: str,
        processed_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> OrderGateDecision:
        return OrderGateDecision(
            intent_id=intent.intent_id,
            intent_type=intent.intent_type,
            symbol=intent.symbol,
            status=status,
            reason=reason,
            source_lane=intent.source_lane,
            processed_at=processed_at,
            payload=payload or {},
        )


def summarize_order_gate_result(result: OrderGateResult) -> dict[str, object]:
    last_decision = result.last_decision
    blocked_detached_count = sum(
        1
        for decision in result.decisions
        if decision.status == "blocked_detached_handler"
    )
    return {
        "order_gate_queue_depth": int(result.queue_depth_before),
        "order_gate_processed_count": int(result.processed_count),
        "order_gate_blocked_detached_count": blocked_detached_count,
        "order_gate_last_decision": last_decision.status if last_decision else None,
        "order_gate_last_intent_type": last_decision.intent_type if last_decision else None,
        "order_gate_last_symbol": last_decision.symbol if last_decision else None,
        "order_gate_last_skip_reason": (
            last_decision.reason
            if last_decision is not None and last_decision.status != "processed"
            else None
        ),
        "order_gate_stopped_due_to_budget": bool(result.stopped_due_to_budget),
        "order_gate_budget_exceeded": bool(result.budget_exceeded),
        "order_gate_budget_exceeded_stage": result.budget_exceeded_stage,
        "order_gate_budget_skip_stage": result.budget_skip_stage,
    }
