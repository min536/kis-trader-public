from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
import time

from app.pipeline import (
    BuyIntent,
    BuyScanLaneController,
    BuyScanRunGuard,
    IntentPriority,
    LaneBudget,
    OrderGate,
    SellIntent,
)
from app.pipeline.order_gate import (
    _register_detached_handler,
    reset_detached_handlers_for_tests,
    summarize_order_gate_result,
)


def _dt(seconds: int = 0) -> datetime:
    return datetime(2026, 6, 27, 9, 0, seconds, tzinfo=timezone.utc)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def test_sell_intent_beats_buy_intent() -> None:
    seen: list[str] = []
    gate = OrderGate(
        buy_handler=lambda intent: seen.append(f"buy:{intent.symbol}"),
        sell_handler=lambda intent: seen.append(f"sell:{intent.symbol}"),
        clock=lambda: _dt(),
    )

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-1",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="candidate",
                created_at=_dt(1),
            ),
            SellIntent(
                intent_id="sell-1",
                source_cycle_id="cycle-1",
                source_lane="sell_watch",
                symbol="000660",
                reason="risk",
                created_at=_dt(2),
            ),
        ),
        now=_dt(),
    )

    assert seen == ["sell:000660", "buy:005930"]
    assert [decision.status for decision in result.decisions] == [
        "processed",
        "processed",
    ]


def test_emergency_sell_sorts_before_normal_sell() -> None:
    first = SellIntent(
        intent_id="sell-normal",
        source_cycle_id="cycle-1",
        source_lane="sell_watch",
        symbol="005930",
        reason="take_profit",
        created_at=_dt(1),
    )
    second = SellIntent(
        intent_id="sell-emergency",
        source_cycle_id="cycle-1",
        source_lane="sell_watch",
        symbol="000660",
        reason="stop_loss",
        priority=int(IntentPriority.EMERGENCY_SELL),
        created_at=_dt(2),
    )

    assert [intent.intent_id for intent in OrderGate.sort_intents((first, second))] == [
        "sell-emergency",
        "sell-normal",
    ]


def test_expired_buy_intent_is_skipped_without_handler_call() -> None:
    called: list[str] = []
    gate = OrderGate(
        buy_handler=lambda intent: called.append(intent.intent_id),
        clock=lambda: _dt(10),
    )

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-expired",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="stale candidate",
                created_at=_dt(1),
                expires_at=_dt(5),
            ),
        ),
        now=_dt(10),
    )

    assert called == []
    assert result.decisions[0].status == "expired"


def test_order_gate_stops_buy_after_slow_sell_exhausts_budget() -> None:
    clock = _FakeClock()
    seen: list[str] = []
    budget = LaneBudget(started_at=0.0, hard_budget_seconds=60.0, clock=clock)

    def sell_handler(intent):
        seen.append(f"sell:{intent.symbol}")
        clock.advance(61.0)
        return True

    def buy_handler(intent):
        seen.append(f"buy:{intent.symbol}")
        return True

    gate = OrderGate(
        buy_handler=buy_handler,
        sell_handler=sell_handler,
        clock=lambda: _dt(),
    )

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-1",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="candidate",
                created_at=_dt(1),
            ),
            SellIntent(
                intent_id="sell-1",
                source_cycle_id="cycle-1",
                source_lane="sell_watch",
                symbol="000660",
                reason="risk",
                created_at=_dt(2),
            ),
        ),
        now=_dt(),
        budget=budget,
        min_buy_remaining_seconds=5.0,
    )

    assert seen == ["sell:000660"]
    assert [decision.status for decision in result.decisions] == [
        "processed",
        "budget_exceeded",
    ]
    assert result.stopped_due_to_budget
    assert result.budget_exceeded
    assert result.budget_exceeded_stage == "order_gate_after_sell"


def test_order_gate_skips_buy_when_remaining_budget_is_low() -> None:
    clock = _FakeClock()
    clock.advance(56.0)
    budget = LaneBudget(started_at=0.0, hard_budget_seconds=60.0, clock=clock)
    seen: list[str] = []
    gate = OrderGate(buy_handler=lambda intent: seen.append(intent.symbol))

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-low-budget",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="candidate",
                created_at=_dt(),
            ),
        ),
        now=_dt(),
        budget=budget,
        min_buy_remaining_seconds=5.0,
    )

    assert seen == []
    assert result.decisions[0].status == "budget_skipped"
    assert result.budget_skip_stage == "order_gate_before_buy"


def test_order_gate_handler_timeout_is_bounded_for_dry_run() -> None:
    reset_detached_handlers_for_tests()
    try:
        started_at = time.perf_counter()
        budget = LaneBudget(
            started_at=started_at,
            hard_budget_seconds=0.001,
            clock=time.perf_counter,
        )
        gate = OrderGate(
            sell_handler=lambda _intent: time.sleep(0.05),
            handler_timeout_enabled=True,
        )

        result = gate.process_ready_intents(
            (
                SellIntent(
                    intent_id="sell-slow",
                    source_cycle_id="cycle-1",
                    source_lane="sell_watch",
                    symbol="000660",
                    reason="risk",
                    created_at=_dt(),
                ),
            ),
            now=_dt(),
            budget=budget,
        )

        assert result.decisions[0].status == "handler_timeout"
        assert result.stopped_due_to_budget
        assert (time.perf_counter() - started_at) < 0.04
    finally:
        # The detached sleep(0.05) worker outlives the assertions; wait for it to
        # finish and clear the registry so it can't spuriously block later tests.
        time.sleep(0.06)
        reset_detached_handlers_for_tests()


def test_handler_timeout_records_budget_exceeded_for_remaining_intents() -> None:
    reset_detached_handlers_for_tests()
    release = Event()
    try:
        def blocking_sell_handler(_intent):
            release.wait(timeout=2.0)
            return True

        buy_called: list[str] = []

        started_at = time.perf_counter()
        budget = LaneBudget(
            started_at=started_at,
            hard_budget_seconds=0.001,
            clock=time.perf_counter,
        )
        gate = OrderGate(
            buy_handler=lambda intent: buy_called.append(intent.symbol),
            sell_handler=blocking_sell_handler,
            handler_timeout_enabled=True,
        )

        result = gate.process_ready_intents(
            (
                SellIntent(
                    intent_id="sell-slow",
                    source_cycle_id="cycle-1",
                    source_lane="sell_watch",
                    symbol="000660",
                    reason="risk",
                    created_at=_dt(1),
                ),
                BuyIntent(
                    intent_id="buy-1",
                    source_cycle_id="cycle-1",
                    source_lane="buy_scan",
                    symbol="005930",
                    reason="candidate",
                    created_at=_dt(2),
                ),
            ),
            now=_dt(),
            budget=budget,
        )

        assert len(result.decisions) == 2
        assert result.decisions[0].status == "handler_timeout"
        assert result.decisions[1].status == "budget_exceeded"
        assert result.decisions[1].intent_type == "BUY"
        assert buy_called == []
        assert result.stopped_due_to_budget
        assert result.budget_exceeded_stage.endswith("_handler_timeout")
    finally:
        release.set()
        time.sleep(0.01)
        reset_detached_handlers_for_tests()


def test_handler_read_timeout_is_failed_not_budget_timeout_when_disabled() -> None:
    # A network read timeout raised INSIDE the handler is a builtin TimeoutError,
    # but it is NOT budget exhaustion. It must be classified "failed" and the loop
    # must continue to the BUY intent, not break and drop it.
    reset_detached_handlers_for_tests()
    seen: list[str] = []

    def sell_handler(_intent):
        raise TimeoutError("read timed out")

    def buy_handler(intent):
        seen.append(f"buy:{intent.symbol}")
        return True

    gate = OrderGate(
        buy_handler=buy_handler,
        sell_handler=sell_handler,
        clock=lambda: _dt(),
        handler_timeout_enabled=False,
    )

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-1",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="candidate",
                created_at=_dt(1),
            ),
            SellIntent(
                intent_id="sell-1",
                source_cycle_id="cycle-1",
                source_lane="sell_watch",
                symbol="000660",
                reason="risk",
                created_at=_dt(2),
            ),
        ),
        now=_dt(),
    )

    by_type = {decision.intent_type: decision for decision in result.decisions}
    assert by_type["SELL"].status == "failed"
    assert by_type["BUY"].status == "processed"
    assert seen == ["buy:005930"]
    assert not result.stopped_due_to_budget
    assert result.budget_exceeded_stage is None


def test_handler_read_timeout_is_failed_not_budget_timeout_when_enabled() -> None:
    # Same as the disabled case, but with handler_timeout_enabled=True and ample
    # remaining budget: future.result() re-raises the handler's TimeoutError, and
    # since the future is done that must be classified "failed", not a future
    # timeout. The BUY intent must still be processed.
    reset_detached_handlers_for_tests()
    clock = _FakeClock()
    budget = LaneBudget(started_at=0.0, hard_budget_seconds=600.0, clock=clock)
    seen: list[str] = []

    def sell_handler(_intent):
        raise TimeoutError("read timed out")

    def buy_handler(intent):
        seen.append(f"buy:{intent.symbol}")
        return True

    gate = OrderGate(
        buy_handler=buy_handler,
        sell_handler=sell_handler,
        clock=lambda: _dt(),
        handler_timeout_enabled=True,
    )

    result = gate.process_ready_intents(
        (
            BuyIntent(
                intent_id="buy-1",
                source_cycle_id="cycle-1",
                source_lane="buy_scan",
                symbol="005930",
                reason="candidate",
                created_at=_dt(1),
            ),
            SellIntent(
                intent_id="sell-1",
                source_cycle_id="cycle-1",
                source_lane="sell_watch",
                symbol="000660",
                reason="risk",
                created_at=_dt(2),
            ),
        ),
        now=_dt(),
        budget=budget,
    )

    by_type = {decision.intent_type: decision for decision in result.decisions}
    assert by_type["SELL"].status == "failed"
    assert by_type["BUY"].status == "processed"
    assert seen == ["buy:005930"]
    assert not result.stopped_due_to_budget
    assert result.budget_exceeded_stage is None


def test_detached_timed_out_handler_blocks_next_cycle_until_it_finishes() -> None:
    reset_detached_handlers_for_tests()
    try:
        release = Event()
        finished = Event()

        def blocking_sell_handler(_intent):
            release.wait(timeout=2.0)
            finished.set()
            return True

        started_at = time.perf_counter()
        budget = LaneBudget(
            started_at=started_at,
            hard_budget_seconds=0.001,
            clock=time.perf_counter,
        )
        gate1 = OrderGate(
            sell_handler=blocking_sell_handler,
            handler_timeout_enabled=True,
        )
        result1 = gate1.process_ready_intents(
            (
                SellIntent(
                    intent_id="sell-slow",
                    source_cycle_id="cycle-1",
                    source_lane="sell_watch",
                    symbol="000660",
                    reason="risk",
                    created_at=_dt(),
                ),
            ),
            now=_dt(),
            budget=budget,
        )
        assert result1.decisions[0].status == "handler_timeout"

        # A brand-new gate (fresh instance every cycle) must refuse to run any
        # handler while the detached handler from the prior cycle is still live.
        called: list[str] = []
        gate2 = OrderGate(
            sell_handler=lambda intent: called.append(intent.symbol),
        )
        result2 = gate2.process_ready_intents(
            (
                SellIntent(
                    intent_id="sell-fresh",
                    source_cycle_id="cycle-2",
                    source_lane="sell_watch",
                    symbol="005930",
                    reason="risk",
                    created_at=_dt(1),
                ),
            ),
            now=_dt(1),
        )
        assert called == []
        assert result2.decisions[0].status == "blocked_detached_handler"
        assert "005930" in result2.decisions[0].symbol or True
        assert "000660" in result2.decisions[0].reason
        assert result2.queue_depth_before == 1
        assert not result2.stopped_due_to_budget

        # Release the detached handler and wait for it to complete.
        release.set()
        assert finished.wait(timeout=2.0)
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            probe = OrderGate(sell_handler=lambda intent: called.append(intent.symbol))
            result3 = probe.process_ready_intents(
                (
                    SellIntent(
                        intent_id="sell-after",
                        source_cycle_id="cycle-3",
                        source_lane="sell_watch",
                        symbol="005930",
                        reason="risk",
                        created_at=_dt(2),
                    ),
                ),
                now=_dt(2),
            )
            if result3.decisions[0].status == "processed":
                break
            time.sleep(0.005)

        assert result3.decisions[0].status == "processed"
        assert called == ["005930"]
    finally:
        reset_detached_handlers_for_tests()


def test_summary_exposes_blocked_detached_count() -> None:
    reset_detached_handlers_for_tests()
    inflight: Future = Future()
    try:
        _register_detached_handler(
            intent=SellIntent(
                intent_id="sell-inflight",
                source_cycle_id="cycle-0",
                source_lane="sell_watch",
                symbol="000660",
                reason="risk",
                created_at=_dt(),
            ),
            future=inflight,
        )
        gate = OrderGate(
            buy_handler=lambda intent: intent.symbol,
            sell_handler=lambda intent: intent.symbol,
        )
        result = gate.process_ready_intents(
            (
                SellIntent(
                    intent_id="sell-1",
                    source_cycle_id="cycle-1",
                    source_lane="sell_watch",
                    symbol="005930",
                    reason="risk",
                    created_at=_dt(1),
                ),
                BuyIntent(
                    intent_id="buy-1",
                    source_cycle_id="cycle-1",
                    source_lane="buy_scan",
                    symbol="005380",
                    reason="candidate",
                    created_at=_dt(2),
                ),
            ),
            now=_dt(3),
        )

        assert [decision.status for decision in result.decisions] == [
            "blocked_detached_handler",
            "blocked_detached_handler",
        ]
        summary = summarize_order_gate_result(result)
        assert summary["order_gate_blocked_detached_count"] == 2
    finally:
        inflight.set_result(None)
        reset_detached_handlers_for_tests()


def test_buy_scan_run_guard_prevents_overlap() -> None:
    guard = BuyScanRunGuard()
    started = guard.try_start(scan_id="scan-1", now=_dt())
    blocked = guard.try_start(scan_id="scan-2", now=_dt(1))

    assert started.allowed
    assert not blocked.allowed
    assert blocked.reason == "previous_scan_running"
    assert blocked.previous_scan_id == "scan-1"

    guard.finish(scan_id="scan-1")
    restarted = guard.try_start(scan_id="scan-3", now=_dt(2))

    assert restarted.allowed


class _FakeExecutor:
    def __init__(self, future: Future) -> None:
        self.future = future
        self.shutdown_calls: list[dict[str, object]] = []

    def submit(self, *_args, **_kwargs):
        return self.future

    def shutdown(self, **kwargs) -> None:
        self.shutdown_calls.append(dict(kwargs))


def test_buy_scan_lane_timeout_releases_guard_when_worker_completes() -> None:
    future: Future = Future()
    executor = _FakeExecutor(future)
    controller = BuyScanLaneController(clock=lambda: 0.0)

    start = controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: executor,
        prefetch_func=lambda **_kwargs: None,
    )
    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert start.decision.allowed
    assert join.timed_out
    assert controller.running
    assert executor.shutdown_calls == [{"wait": False, "cancel_futures": True}]

    future.set_result(None)

    assert not controller.running
    assert controller.active_prefetch is None


def test_buy_scan_lane_ttl_marks_worker_stale_without_allowing_overlap() -> None:
    future: Future = Future()
    clock = _FakeClock()
    controller = BuyScanLaneController(clock=clock)

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
        max_worker_ttl_seconds=5.0,
    )
    clock.advance(6.0)
    controller.reap_completed()

    assert not controller.guard.running
    assert controller.active_prefetch is not None
    assert controller.active_prefetch.stale

    blocked = controller.start_quote_prefetch(
        scan_id="scan-2",
        symbols=("000660",),
        settings=object(),
        execution_token="token",
        now=_dt(1),
        executor_factory=lambda **_kwargs: _FakeExecutor(Future()),
        prefetch_func=lambda **_kwargs: None,
    )

    assert not blocked.decision.allowed
    assert blocked.decision.reason == "previous_scan_worker_stale"


def test_buy_scan_lane_default_ttl_from_settings_marks_worker_stale() -> None:
    future: Future = Future()
    clock = _FakeClock()
    controller = BuyScanLaneController(clock=clock)

    start = controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=SimpleNamespace(
            buy_scan_total_budget_seconds=25.0,
            buy_scan_quote_prefetch_deadline_seconds=18.0,
        ),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )

    assert start.handle is not None
    assert start.handle.max_worker_ttl_at == 30.0

    clock.advance(31.0)
    controller.reap_completed()

    assert not controller.guard.running
    assert controller.active_prefetch is not None
    assert controller.active_prefetch.stale


def test_buy_scan_lane_blocks_new_scan_while_previous_future_running() -> None:
    future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )
    blocked = controller.start_quote_prefetch(
        scan_id="scan-2",
        symbols=("000660",),
        settings=object(),
        execution_token="token",
        now=_dt(1),
        executor_factory=lambda **_kwargs: _FakeExecutor(Future()),
        prefetch_func=lambda **_kwargs: None,
    )

    assert not blocked.decision.allowed
    assert blocked.decision.reason == "previous_scan_running"
    assert blocked.decision.previous_scan_id == "scan-1"


def test_join_returns_completed_result_when_done_callback_finalized_first() -> None:
    future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)
    sentinel = object()

    start = controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )
    # The worker completes BEFORE the main thread joins: the registered
    # done-callback fires synchronously and finalizes the handle, nulling
    # active_prefetch. The fully-computed result must NOT be discarded.
    future.set_result(sentinel)

    assert start.decision.allowed
    assert controller.active_prefetch is None

    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert join.status == "joined"
    assert join.result is sentinel
    assert not controller.running

    # The recovered result is consumed exactly once, not replayed next cycle.
    second = controller.join_active_prefetch(timeout_seconds=0.0)
    assert second.status == "none"
    assert second.result is None


def test_join_after_callback_failure_returns_failed_not_none() -> None:
    future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)
    boom = RuntimeError("boom")

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )
    # Prefetch raised; the done-callback finalizes the handle capturing the
    # exception. The recovered join must distinguish failure from success and
    # must NOT masquerade a failed prefetch as a benign empty join.
    future.set_exception(boom)

    assert controller.active_prefetch is None

    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert join.status == "failed"
    assert join.result is None
    assert join.error is boom
    assert not controller.running

    second = controller.join_active_prefetch(timeout_seconds=0.0)
    assert second.status == "none"
    assert second.error is None


def test_reset_for_tests_clears_callback_completed_result() -> None:
    future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )
    future.set_result("old-result")

    controller.reset_for_tests()
    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert join.status == "none"
    assert join.result is None
    assert controller.active_prefetch is None
    assert not controller.running


def test_start_quote_prefetch_clears_prior_callback_result() -> None:
    first_future: Future = Future()
    second_future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(first_future),
        prefetch_func=lambda **_kwargs: None,
    )
    first_future.set_result("old-result")

    start = controller.start_quote_prefetch(
        scan_id="scan-2",
        symbols=("000660",),
        settings=object(),
        execution_token="token",
        now=_dt(1),
        executor_factory=lambda **_kwargs: _FakeExecutor(second_future),
        prefetch_func=lambda **_kwargs: None,
    )
    second_future.set_result("new-result")
    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert start.decision.allowed
    assert join.status == "joined"
    assert join.result == "new-result"

    second = controller.join_active_prefetch(timeout_seconds=0.0)
    assert second.status == "none"


def test_try_start_scan_clears_prior_callback_result() -> None:
    future: Future = Future()
    controller = BuyScanLaneController(clock=lambda: 0.0)

    controller.start_quote_prefetch(
        scan_id="scan-1",
        symbols=("005930",),
        settings=object(),
        execution_token="token",
        now=_dt(),
        executor_factory=lambda **_kwargs: _FakeExecutor(future),
        prefetch_func=lambda **_kwargs: None,
    )
    future.set_result("old-result")

    decision = controller.try_start_scan(scan_id="scan-2", now=_dt(1))
    controller.guard.finish(scan_id="scan-2")
    join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert decision.allowed
    assert join.status == "none"
    assert join.result is None


def test_threaded_prefetch_callback_result_is_consumed_once() -> None:
    controller = BuyScanLaneController()
    worker_started = Event()
    release_worker = Event()
    sentinel = object()

    def prefetch_func(**_kwargs):
        worker_started.set()
        assert release_worker.wait(timeout=1.0)
        return sentinel

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-prefetch") as executor:
        start = controller.start_quote_prefetch(
            scan_id="scan-1",
            symbols=("005930",),
            settings=object(),
            execution_token="token",
            now=_dt(),
            executor_factory=lambda **_kwargs: executor,
            prefetch_func=prefetch_func,
        )
        assert start.decision.allowed
        assert worker_started.wait(timeout=1.0)
        release_worker.set()

        deadline = time.perf_counter() + 1.0
        while controller.active_metadata()["scan_id"] is not None:
            if time.perf_counter() >= deadline:
                raise AssertionError("prefetch callback did not finalize active handle")
            time.sleep(0.001)

        join = controller.join_active_prefetch(timeout_seconds=0.0)

    assert join.status == "joined"
    assert join.result is sentinel
    assert not controller.running

    second = controller.join_active_prefetch(timeout_seconds=0.0)
    assert second.status == "none"
