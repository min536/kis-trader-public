from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.pipeline.cycle_budget import CycleBudget
from app.pipeline.intents import BuyIntent, SellIntent
from app.pipeline.order_gate import OrderGate
from app.pipeline.scan_lock import BuyScanRunGuard


@dataclass(frozen=True)
class MockLaneCycleResult:
    scenario: str
    cycle_wall_ms: float
    buy_quote_prefetch_completed: int = 0
    buy_quote_prefetch_failed: int = 0
    buy_quote_prefetch_deadline_hit: bool = False
    buy_quote_prefetch_skipped_deadline: int = 0
    buy_scan_skipped_reason: str | None = None
    order_gate_order: tuple[str, ...] = ()
    order_gate_processed_count: int = 0
    real_broker_calls: int = 0
    second_buy_scan_started: bool = False


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    def __call__(self) -> float:
        return self.now


def run_mock_lane_cycle(scenario: str, *, symbol_count: int = 200) -> MockLaneCycleResult:
    clock = _FakeClock()
    budget = CycleBudget(started_at=clock(), hard_budget_seconds=60.0, clock=clock)
    started_at = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)
    guard = BuyScanRunGuard()
    order_seen: list[str] = []
    completed = 0
    failed = 0
    skipped_deadline = 0
    deadline_hit = False
    skip_reason: str | None = None
    second_buy_scan_started = False

    if scenario == "previous_running":
        guard.try_start(scan_id="previous", now=started_at - timedelta(seconds=30))
        second = guard.try_start(scan_id="new", now=started_at)
        second_buy_scan_started = second.allowed
        skip_reason = second.reason
        clock.advance(2.0)
        return MockLaneCycleResult(
            scenario=scenario,
            cycle_wall_ms=budget.elapsed_ms,
            buy_scan_skipped_reason=skip_reason,
            second_buy_scan_started=second_buy_scan_started,
        )

    first = guard.try_start(scan_id="buy-scan", now=started_at)
    if not first.allowed:
        skip_reason = first.reason
    elif scenario == "normal":
        completed = symbol_count
        clock.advance(10.0)
        guard.finish(scan_id="buy-scan")
    elif scenario == "slow_deadline":
        completed = 80
        skipped_deadline = max(0, symbol_count - completed)
        deadline_hit = True
        clock.advance(18.0)
        guard.finish(scan_id="buy-scan")
    elif scenario == "hanging_quote":
        completed = 0
        failed = 1
        skipped_deadline = max(0, symbol_count - 1)
        deadline_hit = True
        skip_reason = "quote_prefetch_timeout"
        clock.advance(18.0)
        guard.finish(scan_id="buy-scan")
    elif scenario == "conflict":
        completed = symbol_count
        clock.advance(8.0)
        guard.finish(scan_id="buy-scan")
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    clock.advance(2.0)
    if scenario == "conflict":
        gate = OrderGate(
            sell_handler=lambda intent: order_seen.append(f"SELL:{intent.symbol}"),
            buy_handler=lambda intent: order_seen.append(f"BUY:{intent.symbol}"),
            clock=lambda: started_at,
        )
        result = gate.process_ready_intents(
            (
                BuyIntent(
                    intent_id="buy",
                    source_cycle_id="cycle",
                    source_lane="buy_scan",
                    symbol="005930",
                    reason="candidate",
                    created_at=started_at,
                ),
                SellIntent(
                    intent_id="sell",
                    source_cycle_id="cycle",
                    source_lane="sell_watch",
                    symbol="000660",
                    reason="risk",
                    created_at=started_at,
                ),
            ),
            now=started_at,
        )
        processed_count = result.processed_count
    else:
        processed_count = 0

    clock.advance(1.0)
    return MockLaneCycleResult(
        scenario=scenario,
        cycle_wall_ms=budget.elapsed_ms,
        buy_quote_prefetch_completed=completed,
        buy_quote_prefetch_failed=failed,
        buy_quote_prefetch_deadline_hit=deadline_hit,
        buy_quote_prefetch_skipped_deadline=skipped_deadline,
        buy_scan_skipped_reason=skip_reason,
        order_gate_order=tuple(order_seen),
        order_gate_processed_count=processed_count,
        real_broker_calls=0,
        second_buy_scan_started=second_buy_scan_started,
    )
