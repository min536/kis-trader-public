from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

from app.pipeline.cycle_budget import LaneBudget
from app.pipeline.intents import BuyIntent
from app.pipeline.scan_lock import BuyScanRunGuard, BuyScanStartDecision

if TYPE_CHECKING:
    from app.scanner.quote_account import BuyScanQuotePrefetchResult


def _prefetch_buy_scan_prices(**kwargs) -> "BuyScanQuotePrefetchResult":
    from app.scanner.quote_account import prefetch_buy_scan_prices

    return prefetch_buy_scan_prices(**kwargs)


@dataclass
class BuyScanPrefetchHandle:
    scan_id: str
    symbols: tuple[str, ...]
    started_at: datetime
    started_monotonic: float
    future: Future
    executor: Any
    max_worker_ttl_at: float | None = None
    shutdown_requested: bool = False
    stale: bool = False
    terminal_reason: str | None = None


@dataclass(frozen=True)
class BuyScanPrefetchStartResult:
    decision: BuyScanStartDecision
    handle: BuyScanPrefetchHandle | None
    running: bool


@dataclass(frozen=True)
class BuyScanPrefetchJoinResult:
    status: str
    handle: BuyScanPrefetchHandle | None
    result: BuyScanQuotePrefetchResult | None = None
    error: BaseException | None = None
    join_wait_ms: float = 0.0
    timed_out: bool = False
    worker_detached: bool = False
    cleanup_nonblocking: bool = False
    future_done: bool = False
    guard_released: bool = False
    guard_release_reason: str | None = None
    stale_worker_active: bool = False


class BuyScanLaneController:
    def __init__(
        self,
        *,
        guard: BuyScanRunGuard | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.guard = guard or BuyScanRunGuard()
        self._clock = clock
        self._lock = Lock()
        self.active_prefetch: BuyScanPrefetchHandle | None = None
        # Holds a completed prefetch result that was finalized by the
        # done-callback before the main thread reached join_active_prefetch.
        # Without this, the fully-computed result is discarded in the race
        # (see _finish_if_active / join_active_prefetch).
        self.last_completed: BuyScanPrefetchJoinResult | None = None

    def _now(self) -> float:
        if self._clock is None:
            import time

            return time.perf_counter()
        return float(self._clock())

    @property
    def running(self) -> bool:
        with self._lock:
            return self.guard.running

    def active_metadata(self) -> dict[str, object]:
        with self._lock:
            handle = self.active_prefetch
            return {
                "running": self.guard.running,
                "scan_id": (
                    handle.scan_id if handle is not None else self.guard.running_scan_id
                ),
                "started_at": (
                    handle.started_at if handle is not None else self.guard.started_at
                ),
                "symbols": len(handle.symbols) if handle is not None else 0,
                "stale": bool(handle.stale if handle is not None else False),
                "terminal_reason": handle.terminal_reason if handle is not None else None,
                "shutdown_requested": bool(
                    handle.shutdown_requested if handle is not None else False
                ),
            }

    def reset_for_tests(self) -> None:
        with self._lock:
            handle = self.active_prefetch
            self.active_prefetch = None
            self.last_completed = None
            self.guard.running_scan_id = None
            self.guard.started_at = None
        if handle is not None:
            self._shutdown(handle)

    def reap_completed(self) -> bool:
        with self._lock:
            handle = self.active_prefetch
            if handle is None:
                return False
            if not handle.future.done():
                if self._ttl_expired(handle):
                    self._mark_stale_locked(handle)
                return False
        try:
            handle.future.result()
        except BaseException:
            pass
        with self._lock:
            if self.active_prefetch is handle:
                self._finish_locked(handle)
            self.last_completed = None
        return True

    def try_start_scan(self, *, scan_id: str, now: datetime) -> BuyScanStartDecision:
        self.reap_completed()
        with self._lock:
            active = self.active_prefetch
            if active is not None and not active.future.done():
                reason = (
                    "previous_scan_worker_stale"
                    if active.stale
                    else "previous_scan_running"
                )
                return BuyScanStartDecision(
                    allowed=False,
                    scan_id=scan_id,
                    reason=reason,
                    previous_scan_id=active.scan_id,
                    previous_started_at=active.started_at,
                )
            decision = self.guard.try_start(scan_id=scan_id, now=now)
            if decision.allowed:
                self.last_completed = None
            return decision

    def start_quote_prefetch(
        self,
        *,
        scan_id: str,
        symbols: tuple[str, ...],
        settings,
        execution_token: str,
        now: datetime,
        executor_factory: Callable[..., Any] = ThreadPoolExecutor,
        prefetch_func: Callable[..., "BuyScanQuotePrefetchResult"] | None = None,
        max_worker_ttl_seconds: float | None = None,
    ) -> BuyScanPrefetchStartResult:
        decision = self.try_start_scan(scan_id=scan_id, now=now)
        if not decision.allowed:
            with self._lock:
                active = self.active_prefetch
            return BuyScanPrefetchStartResult(
                decision=decision,
                handle=active,
                running=True,
            )

        executor = executor_factory(
            max_workers=1,
            thread_name_prefix="buy-quote-prefetch",
        )
        prefetch_callable = prefetch_func or _prefetch_buy_scan_prices
        future = executor.submit(
            prefetch_callable,
            symbols=symbols,
            settings=settings,
            execution_token=execution_token,
        )
        started_monotonic = self._now()
        ttl_at = None
        resolved_ttl_seconds = _resolve_worker_ttl_seconds(
            settings=settings,
            explicit_ttl_seconds=max_worker_ttl_seconds,
        )
        if resolved_ttl_seconds is not None:
            ttl_at = started_monotonic + resolved_ttl_seconds
        handle = BuyScanPrefetchHandle(
            scan_id=scan_id,
            symbols=symbols,
            started_at=now,
            started_monotonic=started_monotonic,
            future=future,
            executor=executor,
            max_worker_ttl_at=ttl_at,
        )
        with self._lock:
            self.last_completed = None
            self.active_prefetch = handle
        future.add_done_callback(lambda _future: self._finish_if_active(handle))
        return BuyScanPrefetchStartResult(
            decision=decision,
            handle=handle,
            running=True,
        )

    def join_active_prefetch(self, *, timeout_seconds: float) -> BuyScanPrefetchJoinResult:
        with self._lock:
            handle = self.active_prefetch
            if handle is None:
                # The done-callback may have finalized a completed prefetch before
                # we got here. Recover its result instead of discarding it.
                pending = self.last_completed
                self.last_completed = None
                if pending is not None:
                    return pending
                return BuyScanPrefetchJoinResult(status="none", handle=None)

        started = self._now()
        try:
            result = handle.future.result(timeout=max(0.0, float(timeout_seconds)))
        except TimeoutError as exc:
            join_wait_ms = (self._now() - started) * 1000.0
            with self._lock:
                if self.active_prefetch is handle:
                    handle.shutdown_requested = True
            self._shutdown(handle)
            return BuyScanPrefetchJoinResult(
                status="timeout",
                handle=handle,
                error=exc,
                join_wait_ms=join_wait_ms,
                timed_out=True,
                worker_detached=not handle.future.done(),
                cleanup_nonblocking=True,
                future_done=handle.future.done(),
                guard_released=False,
                guard_release_reason="worker_still_running",
                stale_worker_active=bool(handle.stale),
            )
        except BaseException as exc:
            join_wait_ms = (self._now() - started) * 1000.0
            with self._lock:
                if self.active_prefetch is handle:
                    self._finish_locked(handle)
                self.last_completed = None
            return BuyScanPrefetchJoinResult(
                status="failed",
                handle=handle,
                error=exc,
                join_wait_ms=join_wait_ms,
                cleanup_nonblocking=True,
                future_done=True,
                guard_released=True,
                guard_release_reason="prefetch_exception",
            )

        join_wait_ms = (self._now() - started) * 1000.0
        with self._lock:
            if self.active_prefetch is handle:
                self._finish_locked(handle)
            self.last_completed = None
        return BuyScanPrefetchJoinResult(
            status="joined",
            handle=handle,
            result=result,
            join_wait_ms=join_wait_ms,
            cleanup_nonblocking=True,
            future_done=True,
            guard_released=True,
            guard_release_reason="prefetch_completed",
        )

    def _shutdown(self, handle: BuyScanPrefetchHandle) -> None:
        try:
            handle.executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            handle.executor.shutdown(wait=False)

    def _finish(self, handle: BuyScanPrefetchHandle) -> None:
        with self._lock:
            self._finish_locked(handle)

    def _finish_locked(self, handle: BuyScanPrefetchHandle) -> None:
        self._shutdown(handle)
        self.guard.finish(scan_id=handle.scan_id)
        if self.active_prefetch is handle:
            self.active_prefetch = None
        handle.terminal_reason = handle.terminal_reason or "finished"

    def _finish_if_active(self, handle: BuyScanPrefetchHandle) -> None:
        # The done-callback runs on the worker thread, potentially before the
        # main thread reaches join_active_prefetch. Capture the completed result
        # so the join can recover it instead of returning status="none".
        completed: BuyScanPrefetchJoinResult | None = None
        if handle.future.done():
            try:
                result = handle.future.result()
                completed = BuyScanPrefetchJoinResult(
                    status="joined",
                    handle=handle,
                    result=result,
                    future_done=True,
                    guard_released=True,
                    guard_release_reason="prefetch_completed",
                )
            except BaseException as exc:
                completed = BuyScanPrefetchJoinResult(
                    status="failed",
                    handle=handle,
                    error=exc,
                    future_done=True,
                    guard_released=True,
                    guard_release_reason="prefetch_exception",
                )
        with self._lock:
            if self.active_prefetch is not handle:
                return
            self.last_completed = completed
            self._finish_locked(handle)

    def _ttl_expired(self, handle: BuyScanPrefetchHandle) -> bool:
        return (
            handle.max_worker_ttl_at is not None
            and self._now() >= float(handle.max_worker_ttl_at)
        )

    def _mark_stale(self, handle: BuyScanPrefetchHandle) -> None:
        with self._lock:
            self._mark_stale_locked(handle)

    def _mark_stale_locked(self, handle: BuyScanPrefetchHandle) -> None:
        handle.stale = True
        handle.terminal_reason = "worker_ttl_expired"
        self.guard.finish(scan_id=handle.scan_id)


def prefetch_metrics_from_result(result: BuyScanQuotePrefetchResult) -> dict[str, object]:
    return {
        "price_data_by_symbol": {
            symbol: dict(price_data)
            for symbol, price_data in result.price_data_by_symbol.items()
        },
        "request_count": len(result.completed_symbols) + len(result.failed_symbols),
        "completed": len(result.completed_symbols),
        "failed": len(result.failed_symbols),
        "skipped_deadline": len(result.skipped_deadline_symbols),
        "budget_skipped": len(result.budget_skipped_symbols),
        "deadline_hit": bool(result.deadline_hit),
        "elapsed_ms": float(result.elapsed_ms),
        "success_ratio": float(result.success_ratio),
        "deadline_seconds": result.deadline_seconds,
        "request_timeout_seconds": float(result.request_timeout_seconds),
        "max_attempts": int(result.max_attempts),
        "timeout_count": int(result.timeout_count),
        "response_ms": float(result.quote_response_ms),
        "throttle_sleep_ms": float(result.throttle_sleep_ms),
        "quote_account_mode": result.quote_account_mode,
        "quote_account_env": result.quote_account_env,
        "rate_limit_triggered": bool(result.rate_limit_triggered),
        "rate_limit_symbol": result.rate_limit_symbol,
        "rate_limit_message": result.rate_limit_message,
    }


def restrict_symbols_to_prefetched(
    symbols: tuple[str, ...],
    price_data_by_symbol: dict[str, dict[str, object]],
) -> tuple[str, ...]:
    return tuple(symbol for symbol in symbols if symbol in price_data_by_symbol)


def _resolve_worker_ttl_seconds(
    *,
    settings,
    explicit_ttl_seconds: float | None,
) -> float | None:
    if explicit_ttl_seconds is not None:
        return max(0.0, float(explicit_ttl_seconds))
    configured = getattr(settings, "buy_scan_quote_worker_max_ttl_seconds", None)
    if configured is not None:
        return max(0.0, float(configured))

    candidates: list[float] = []
    for attr in (
        "buy_scan_total_budget_seconds",
        "buy_scan_quote_prefetch_deadline_seconds",
        "buy_scan_quote_request_timeout_seconds",
    ):
        value = getattr(settings, attr, None)
        if value is not None:
            candidates.append(max(0.0, float(value)))
    if candidates:
        return max(candidates) + 5.0
    return 30.0


BUY_SCAN_LANE_CONTROLLER = BuyScanLaneController()


BuyIntentFactory = Callable[..., BuyIntent | None]


@dataclass(frozen=True)
class LiveQuoteLaneResult:
    result: BuyScanQuotePrefetchResult | None
    elapsed_ms: float = 0.0
    deadline_hit: bool = False
    skipped_reason: str | None = None


class LiveQuoteLane:
    def __init__(
        self,
        *,
        prefetch_func: Callable[..., "BuyScanQuotePrefetchResult"] | None = None,
    ) -> None:
        self._prefetch_func = prefetch_func or _prefetch_buy_scan_prices

    def prefetch(
        self,
        *,
        symbols: tuple[str, ...],
        settings,
        execution_token: str,
        budget: LaneBudget,
        context: dict[str, Any] | None = None,
    ) -> LiveQuoteLaneResult:
        if budget.expired():
            return LiveQuoteLaneResult(
                result=None,
                deadline_hit=True,
                skipped_reason="cycle_budget_exceeded",
            )
        budget.checkpoint("live_quote_lane_prefetch_start")
        result = self._prefetch_func(
            symbols=symbols,
            settings=settings,
            execution_token=execution_token,
            lane_budget=budget,
            **dict((context or {}).get("prefetch_kwargs") or {}),
        )
        budget.checkpoint("live_quote_lane_prefetch_end")
        return LiveQuoteLaneResult(
            result=result,
            elapsed_ms=float(result.elapsed_ms),
            deadline_hit=bool(result.deadline_hit),
        )


@dataclass(frozen=True)
class BuyLaneResult:
    due: bool
    intent: BuyIntent | None = None
    skipped_reason: str | None = None
    prefetch_result: BuyScanQuotePrefetchResult | None = None
    started: bool = False
    guard_released: bool = False
    guard_release_reason: str | None = None
    previous_scan_id: str | None = None
    exception: BaseException | None = None
    worker_detached: bool = False
    cleanup_nonblocking: bool = False
    future_done: bool = True


class BuyLane:
    def __init__(
        self,
        *,
        controller: BuyScanLaneController | None = None,
        quote_lane: LiveQuoteLane | None = None,
        intent_factory: BuyIntentFactory | None = None,
    ) -> None:
        self.controller = controller or BUY_SCAN_LANE_CONTROLLER
        self.quote_lane = quote_lane or LiveQuoteLane()
        self._intent_factory = intent_factory

    def run(
        self,
        *,
        due: bool,
        cycle_id: str,
        symbols: tuple[str, ...],
        settings,
        execution_token: str,
        budget: LaneBudget,
        now: datetime,
        context: dict[str, Any] | None = None,
    ) -> BuyLaneResult:
        if not due:
            return BuyLaneResult(due=False, skipped_reason="cadence_not_reached")
        context = context or {}
        min_remaining_seconds = float(
            context.get(
                "buy_min_remaining_budget_seconds",
                getattr(settings, "buy_scan_min_remaining_budget_seconds", 5.0),
            )
            or 0.0
        )
        if budget.remaining_seconds() <= max(0.0, min_remaining_seconds):
            return BuyLaneResult(due=True, skipped_reason="cycle_budget_low")
        decision = self.controller.try_start_scan(scan_id=f"{cycle_id}:buy_scan", now=now)
        if not decision.allowed:
            return BuyLaneResult(
                due=True,
                skipped_reason=decision.reason,
                previous_scan_id=decision.previous_scan_id,
            )
        if budget.expired():
            self.controller.guard.finish(scan_id=decision.scan_id)
            return BuyLaneResult(
                due=True,
                skipped_reason="cycle_budget_exceeded",
                started=True,
                guard_released=True,
                guard_release_reason="cycle_budget_exceeded",
            )

        prefetch_result: BuyScanQuotePrefetchResult | None = None
        try:
            quote_result = self.quote_lane.prefetch(
                symbols=symbols,
                settings=settings,
                execution_token=execution_token,
                budget=budget,
                context=context,
            )
            return self._finalize_prefetched(
                cycle_id=cycle_id,
                budget=budget,
                context=context,
                prefetch_result=quote_result.result,
                missing_result_reason=quote_result.skipped_reason,
            )
        except Exception as exc:
            return BuyLaneResult(
                due=True,
                skipped_reason="prefetch_exception",
                prefetch_result=prefetch_result,
                started=True,
                guard_released=True,
                guard_release_reason="prefetch_exception",
                exception=exc,
            )
        finally:
            self.controller.guard.finish(scan_id=decision.scan_id)

    def run_with_prefetched(
        self,
        *,
        cycle_id: str,
        settings,
        budget: LaneBudget,
        join_result: "BuyScanPrefetchJoinResult",
        context: dict[str, Any] | None = None,
    ) -> BuyLaneResult:
        """Consume an already-started async quote prefetch (S4 overlap path).

        The controller has already run/released the scan guard by the time the
        join returns, so this method does NOT touch the guard — it only maps the
        join outcome onto the SAME downstream logic ``run`` uses via the shared
        ``_finalize_prefetched`` tail. Used only when overlap is enabled.
        """
        context = context or {}
        if join_result.status == "timeout":
            # Worker still running; guard intentionally kept (previous_scan_running
            # next cycle). No intent, no order — read-only overlap invariant.
            return BuyLaneResult(
                due=True,
                skipped_reason="quote_prefetch_timeout",
                started=True,
                guard_released=False,
                guard_release_reason=join_result.guard_release_reason,
                worker_detached=bool(join_result.worker_detached),
                cleanup_nonblocking=bool(join_result.cleanup_nonblocking),
                future_done=bool(join_result.future_done),
            )
        if join_result.status == "failed":
            return BuyLaneResult(
                due=True,
                skipped_reason="prefetch_exception",
                started=True,
                guard_released=bool(join_result.guard_released),
                guard_release_reason=join_result.guard_release_reason,
                exception=join_result.error,
                cleanup_nonblocking=bool(join_result.cleanup_nonblocking),
                future_done=bool(join_result.future_done),
            )
        return self._finalize_prefetched(
            cycle_id=cycle_id,
            budget=budget,
            context=context,
            prefetch_result=join_result.result,
            missing_result_reason="quote_prefetch_failed",
            guard_released=bool(join_result.guard_released),
            guard_release_reason=join_result.guard_release_reason,
            cleanup_nonblocking=bool(join_result.cleanup_nonblocking),
            future_done=bool(join_result.future_done),
        )

    def _finalize_prefetched(
        self,
        *,
        cycle_id: str,
        budget: LaneBudget,
        context: dict[str, Any],
        prefetch_result: BuyScanQuotePrefetchResult | None,
        missing_result_reason: str | None,
        guard_released: bool = True,
        guard_release_reason: str | None = "prefetch_completed",
        cleanup_nonblocking: bool = False,
        future_done: bool = True,
    ) -> BuyLaneResult:
        """Shared post-prefetch tail used by BOTH ``run`` (sync) and
        ``run_with_prefetched`` (async overlap) so there is ONE copy of the
        None → skipped, empty → deadline/missing, else intent-factory branch."""
        if prefetch_result is None:
            return BuyLaneResult(
                due=True,
                skipped_reason=missing_result_reason or "quote_prefetch_failed",
                started=True,
                guard_released=guard_released,
                guard_release_reason=guard_release_reason,
                prefetch_result=None,
                cleanup_nonblocking=cleanup_nonblocking,
                future_done=future_done,
            )
        if not prefetch_result.price_data_by_symbol:
            return BuyLaneResult(
                due=True,
                skipped_reason=(
                    "quote_prefetch_deadline"
                    if prefetch_result.deadline_hit
                    else "quote_prefetch_missing"
                ),
                started=True,
                prefetch_result=prefetch_result,
                guard_released=guard_released,
                guard_release_reason=guard_release_reason,
                cleanup_nonblocking=cleanup_nonblocking,
                future_done=future_done,
            )
        intent = None
        if self._intent_factory is not None and not budget.expired():
            intent = self._intent_factory(
                cycle_id=cycle_id,
                budget=budget,
                context=context or {},
                prefetch_result=prefetch_result,
            )
        skipped_reason = None
        if intent is None:
            skipped_reason = str(context.get("buy_skipped_reason") or "no_buy_intent")
        return BuyLaneResult(
            due=True,
            intent=intent,
            skipped_reason=skipped_reason,
            prefetch_result=prefetch_result,
            started=True,
            guard_released=guard_released,
            guard_release_reason=guard_release_reason,
            cleanup_nonblocking=cleanup_nonblocking,
            future_done=future_done,
        )
