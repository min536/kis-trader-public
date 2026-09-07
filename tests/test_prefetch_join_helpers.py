"""Behavior-lock tests for the shared BUY quote prefetch join cores.

Stage B-1 (``docs/main_run_cycle_slimming_plan_20260703.md`` §3, gate C4): the
three ``run_cycle`` prefetch join blocks (scan_only join, normal join, finally
drain) share exactly two pure cores — the 16-field ``metrics`` flatten and the
guard-field copy. Everything mode-specific (rate-limit branch, symbol restrict,
skip-reason, backoff note) stays at the call site. These tests pin the two
shared cores so the extraction is provably behavior-preserving.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.runtime.cycle_phases.context import CycleContext
from app.runtime.cycle_phases.prefetch_join import (
    copy_join_guard_fields,
    flatten_prefetch_metrics,
)


def _full_metrics() -> dict[str, object]:
    return {
        "price_data_by_symbol": {"005930": {"price": 1}},
        "request_count": 7,
        "completed": 5,
        "failed": 2,
        "skipped_deadline": 1,
        "budget_skipped": 3,
        "timeout_count": 4,
        "deadline_hit": True,
        "elapsed_ms": 123.5,
        "success_ratio": 0.71,
        "deadline_seconds": 8.0,
        "request_timeout_seconds": 2.5,
        "max_attempts": 3,
        "response_ms": 42.0,
        "throttle_sleep_ms": 11.0,
        "quote_account_mode": "paper",
        "quote_account_env": "mock",
        # site-kept keys — helper must NOT touch these:
        "rate_limit_triggered": True,
        "rate_limit_symbol": "005930",
        "rate_limit_message": "429",
    }


def test_flatten_assigns_sixteen_fields_with_original_coercions() -> None:
    ctx = CycleContext()
    metrics = _full_metrics()

    flatten_prefetch_metrics(ctx, metrics)

    assert ctx.buy_quote_prefetch_request_count == 7
    assert isinstance(ctx.buy_quote_prefetch_request_count, int)
    assert ctx.buy_quote_prefetch_completed == 5
    assert ctx.buy_quote_prefetch_failed == 2
    assert ctx.buy_quote_prefetch_skipped_deadline == 1
    assert ctx.buy_quote_prefetch_budget_skipped == 3
    assert ctx.buy_quote_prefetch_timeout_count == 4
    assert ctx.buy_quote_prefetch_deadline_hit is True
    assert ctx.buy_quote_prefetch_elapsed_ms == 123.5
    assert isinstance(ctx.buy_quote_prefetch_elapsed_ms, float)
    assert ctx.buy_quote_prefetch_success_ratio == 0.71
    assert ctx.buy_quote_prefetch_request_timeout_seconds == 2.5
    assert ctx.buy_quote_prefetch_max_attempts == 3
    assert ctx.buy_quote_prefetch_deadline_seconds == 8.0
    assert isinstance(ctx.buy_quote_prefetch_deadline_seconds, float)
    assert ctx.buy_quote_prefetch_response_ms == 42.0
    assert ctx.buy_quote_prefetch_throttle_sleep_ms == 11.0
    assert ctx.buy_scan_quote_account_mode == "paper"
    assert isinstance(ctx.buy_scan_quote_account_mode, str)
    assert ctx.buy_scan_quote_account_env == "mock"

    # Site-kept fields must NOT be flattened by the helper.
    assert ctx.buy_quote_prefetch_price_data_by_symbol == {}
    assert ctx.rate_limit_triggered is False


def test_flatten_skips_deadline_seconds_when_metric_is_none() -> None:
    ctx = CycleContext()
    ctx.buy_quote_prefetch_deadline_seconds = 99.0
    metrics = _full_metrics()
    metrics["deadline_seconds"] = None

    flatten_prefetch_metrics(ctx, metrics)

    # deadline_seconds untouched when metric is None (original guard preserved).
    assert ctx.buy_quote_prefetch_deadline_seconds == 99.0


def test_copy_join_guard_fields_copies_and_accumulates_wait() -> None:
    ctx = CycleContext()
    ctx.buy_quote_prefetch_join_wait_ms = 40.0
    prefetch_join = SimpleNamespace(
        join_wait_ms=15.0,
        worker_detached=True,
        cleanup_nonblocking=True,
        future_done=True,
        guard_released=True,
        guard_release_reason="deadline",
    )

    copy_join_guard_fields(ctx, prefetch_join, lane_running=True)

    # join_wait_ms accumulates onto the existing value (+= semantics).
    assert ctx.buy_quote_prefetch_join_wait_ms == 55.0
    assert ctx.buy_lane_running is True
    assert ctx.buy_quote_prefetch_worker_detached is True
    assert ctx.buy_quote_prefetch_cleanup_nonblocking is True
    assert ctx.buy_quote_prefetch_future_done is True
    assert ctx.buy_scan_guard_released is True
    assert ctx.buy_scan_guard_release_reason == "deadline"
