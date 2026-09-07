"""Shared pure cores for the three BUY quote prefetch join blocks in run_cycle.

Stage B-1 (``docs/main_run_cycle_slimming_plan_20260703.md`` §3, gate **C4**):
``app.main.run_cycle`` joins the BUY quote prefetch worker at three distinct
sites — the scan_only diagnostic join, the normal deep-scan join, and the
``finally`` cleanup drain. Those three blocks have *different* observable
behavior (rate-limit branch, symbol-restrict target, skip-reason/status, backoff
note, ``buy_scan_requested_count`` reset) and gate C4 forbids merging any of it.

Only two pieces are byte-for-byte identical across the three sites, and only
those are shared here:

1. ``copy_join_guard_fields`` — the guard/worker bookkeeping copied straight off
   the ``BuyScanPrefetchJoinResult`` right after ``join_active_prefetch``.
2. ``flatten_prefetch_metrics`` — the 16 ``metrics``-dict assignments (with their
   original ``int``/``float``/``bool``/``str`` coercions and the
   ``deadline_seconds is not None`` guard) that appear in the
   ``prefetch_join.result is not None`` branch of every site.

Everything mode-specific (``price_data_by_symbol``, ``quote_age_*``, the
``[info] BUY quote prefetch joined`` print, ``restrict_symbols_to_prefetched``,
the rate-limit branch, skip-reason/status, ``note`` calls) stays at the call
site — this module is not a merge of the three blocks.

Dependency-light by design: it must NOT import ``app.main`` and needs no
collaborators — the two helpers take only ``(ctx, prefetch_join, metrics,
lane_running)`` and mutate ``ctx`` in place.
"""

from __future__ import annotations

from typing import Any


def copy_join_guard_fields(ctx: Any, prefetch_join: Any, *, lane_running: Any) -> None:
    """Copy the guard/worker bookkeeping off ``prefetch_join`` onto ``ctx``.

    Mirrors the seven lines that follow ``join_active_prefetch`` at all three
    sites verbatim. ``buy_lane_running`` is passed in explicitly because its
    source at the call site is ``BUY_SCAN_LANE_CONTROLLER.running`` (the finally
    ``timed_out`` branch overrides it to ``True`` — that override stays at the
    call site, not here).
    """

    ctx.buy_quote_prefetch_join_wait_ms += prefetch_join.join_wait_ms
    ctx.buy_lane_running = lane_running
    ctx.buy_quote_prefetch_worker_detached = prefetch_join.worker_detached
    ctx.buy_quote_prefetch_cleanup_nonblocking = prefetch_join.cleanup_nonblocking
    ctx.buy_quote_prefetch_future_done = prefetch_join.future_done
    ctx.buy_scan_guard_released = prefetch_join.guard_released
    ctx.buy_scan_guard_release_reason = prefetch_join.guard_release_reason


def flatten_prefetch_metrics(ctx: Any, metrics: Any) -> None:
    """Flatten the 16 shared ``metrics`` assignments onto ``ctx`` verbatim.

    These 16 assignments are identical (same order, same coercions) across the
    scan_only join, the normal join, and the finally drain. ``price_data_by_symbol``,
    ``quote_age_*``, the join print, and every rate-limit / restrict / skip-reason
    line are site-specific and remain at the call site.
    """

    ctx.buy_quote_prefetch_request_count = int(metrics["request_count"])
    ctx.buy_quote_prefetch_completed = int(metrics["completed"])
    ctx.buy_quote_prefetch_failed = int(metrics["failed"])
    ctx.buy_quote_prefetch_skipped_deadline = int(metrics["skipped_deadline"])
    ctx.buy_quote_prefetch_budget_skipped = int(metrics["budget_skipped"])
    ctx.buy_quote_prefetch_timeout_count = int(metrics["timeout_count"])
    ctx.buy_quote_prefetch_deadline_hit = bool(metrics["deadline_hit"])
    ctx.buy_quote_prefetch_elapsed_ms = float(metrics["elapsed_ms"])
    ctx.buy_quote_prefetch_success_ratio = float(metrics["success_ratio"])
    ctx.buy_quote_prefetch_request_timeout_seconds = float(
        metrics["request_timeout_seconds"]
    )
    ctx.buy_quote_prefetch_max_attempts = int(metrics["max_attempts"])
    if metrics["deadline_seconds"] is not None:
        ctx.buy_quote_prefetch_deadline_seconds = float(metrics["deadline_seconds"])
    ctx.buy_quote_prefetch_response_ms = float(metrics["response_ms"])
    ctx.buy_quote_prefetch_throttle_sleep_ms = float(metrics["throttle_sleep_ms"])
    ctx.buy_scan_quote_account_mode = str(metrics["quote_account_mode"])
    ctx.buy_scan_quote_account_env = str(metrics["quote_account_env"])
