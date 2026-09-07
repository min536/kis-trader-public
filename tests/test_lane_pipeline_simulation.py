from __future__ import annotations

from app.pipeline.simulation import run_mock_lane_cycle


def test_normal_200_symbol_buy_sell_due_cycle_finishes_under_60s() -> None:
    result = run_mock_lane_cycle("normal", symbol_count=200)

    assert result.cycle_wall_ms <= 60_000
    assert result.buy_quote_prefetch_completed == 200
    assert not result.buy_quote_prefetch_deadline_hit
    assert result.real_broker_calls == 0


def test_slow_quote_deadline_cycle_finishes_under_60s_with_partial_result() -> None:
    result = run_mock_lane_cycle("slow_deadline", symbol_count=200)

    assert result.cycle_wall_ms <= 60_000
    assert result.buy_quote_prefetch_deadline_hit
    assert result.buy_quote_prefetch_skipped_deadline > 0
    assert result.real_broker_calls == 0


def test_previous_buy_scan_running_skips_new_scan_and_keeps_sell_budget() -> None:
    result = run_mock_lane_cycle("previous_running", symbol_count=200)

    assert result.cycle_wall_ms <= 60_000
    assert result.buy_scan_skipped_reason == "previous_scan_running"
    assert not result.second_buy_scan_started


def test_sell_and_buy_conflict_processes_sell_first() -> None:
    result = run_mock_lane_cycle("conflict", symbol_count=200)

    assert result.cycle_wall_ms <= 60_000
    assert result.order_gate_order == ("SELL:000660", "BUY:005930")
    assert result.order_gate_processed_count == 2


def test_hanging_quote_call_is_bounded_by_prefetch_timeout_policy() -> None:
    result = run_mock_lane_cycle("hanging_quote", symbol_count=200)

    assert result.cycle_wall_ms <= 60_000
    assert result.buy_quote_prefetch_deadline_hit
    assert result.buy_scan_skipped_reason == "quote_prefetch_timeout"
    assert result.real_broker_calls == 0
