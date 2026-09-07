"""Cycle-scoped accumulator context for ``app.main.run_cycle``.

Stage B-2 (``docs/main_run_cycle_slimming_plan_20260703.md`` §3): the ~146 local
accumulators/flags declared at the top of ``run_cycle`` (plus the ``buy_scan_due``
parameter, which is mutated mid-cycle) are promoted onto this dataclass so that
subsequent phase-extraction slices can pass a single object between phase
functions instead of relying on shared function-local scope.

This module is intentionally dependency-light: it must NOT import ``app.main``.
Field types are deliberately permissive (``Any``) — the goal of this slice is a
behavior-preserving rename, not a typing pass.

Fields whose original initializer referenced a run_cycle parameter or a
preceding local (``settings``/``cycle_budget``/``BUY_SCAN_LANE_CONTROLLER`` etc.)
carry a neutral placeholder default here and are seeded explicitly at
``CycleContext(...)`` construction time in ``run_cycle``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CycleContext:
    timing_summary: Any = field(default_factory=dict)
    api_usage_summary: Any = field(default_factory=lambda: {})
    buy_scan_requested_count: Any = 0
    buy_scan_evaluated_count: Any = 0
    buy_scan_quote_request_count: Any = 0
    buy_scan_guard_wait_ms: Any = 0.0
    buy_scan_sleep_ms: Any = 0.0
    buy_scan_effective_total_sleep_ms: Any = 0.0
    buy_scan_quote_response_ms: Any = 0.0
    buy_scan_calc_ms: Any = 0.0
    buy_scan_parse_ms: Any = 0.0
    buy_scan_score_calc_ms: Any = 0.0
    buy_scan_candidate_build_ms: Any = 0.0
    buy_scan_logging_ms: Any = 0.0
    buy_scan_throttle_sleep_events: Any = 0
    buy_scan_throttle_min_sleep_ms: Any = None
    buy_scan_throttle_total_sleep_ms: Any = 0.0
    buy_scan_throttle_immediate_pass_count: Any = 0
    buy_scan_average_sleep_per_event_ms: Any = 0.0
    buy_scan_quote_account_mode: Any = 'not_run'
    buy_scan_quote_account_env: Any = ''
    buy_scan_separate_quote_lane: Any = False
    buy_quote_prefetch_future: Any = None
    buy_quote_prefetch_symbols: Any = None
    buy_quote_prefetch_snapshot_info: Any = None
    buy_quote_prefetch_price_data_by_symbol: Any = field(default_factory=lambda: {})
    buy_quote_prefetch_request_count: Any = 0
    buy_quote_prefetch_response_ms: Any = 0.0
    buy_quote_prefetch_throttle_sleep_ms: Any = 0.0
    buy_quote_prefetch_join_wait_ms: Any = 0.0
    buy_quote_prefetch_deadline_seconds: Any = 0.0
    buy_quote_prefetch_request_timeout_seconds: Any = 0.0
    buy_quote_prefetch_max_attempts: Any = 1
    buy_quote_prefetch_deadline_hit: Any = False
    buy_quote_prefetch_completed: Any = 0
    buy_quote_prefetch_failed: Any = 0
    buy_quote_prefetch_skipped_deadline: Any = 0
    buy_quote_prefetch_budget_skipped: Any = 0
    buy_quote_prefetch_timeout_count: Any = 0
    buy_quote_prefetch_elapsed_ms: Any = 0.0
    buy_quote_prefetch_success_ratio: Any = 0.0
    buy_quote_prefetch_worker_detached: Any = False
    buy_quote_prefetch_cleanup_nonblocking: Any = False
    buy_quote_prefetch_future_done: Any = True
    buy_scan_guard_released: Any = False
    buy_scan_guard_release_reason: Any = None
    buy_scan_sample_symbols: Any = field(default_factory=lambda: [])
    buy_scan_top_k_count: Any = 0
    buy_scan_skipped_reason: Any = None
    sell_watch_skipped_reason: Any = None
    buy_scan_budget_reserved: Any = False
    buy_scan_reserve_used: Any = False
    buy_scan_partial_budget: Any = False
    sell_watch_capped_for_buy_scan: Any = False
    buy_status_text: Any = 'not_run'
    sell_status_text: Any = 'not_run'
    sell_quote_request_count: Any = 0
    sell_evaluated_count: Any = 0
    sell_eval_guard_wait_ms: Any = 0.0
    sell_eval_quote_response_ms: Any = 0.0
    sell_eval_throttle_sleep_ms: Any = 0.0
    sell_eval_calc_ms: Any = 0.0
    sell_eval_effective_total_sleep_ms: Any = 0.0
    sell_watch_total_holdings: Any = 0
    sell_watch_cursor_before: Any = 0
    sell_watch_cursor_after: Any = 0
    sell_watch_partial: Any = False
    sell_watch_partial_reason: Any = None
    sell_watch_evaluated_symbols: Any = field(default_factory=lambda: [])
    sell_watch_skipped_symbols: Any = field(default_factory=lambda: [])
    # slice K gate K-1: assigned in the SELL-watch phase (國면 9), consumed by the
    # SELL-order defer block (國면 11) — promoted so the datum survives the phase
    # boundary. Neutral empty default preserves the original "not yet built" sense.
    prioritized_positions: Any = field(default_factory=lambda: [])
    sell_watch_priority_preview: Any = field(default_factory=lambda: [])
    sell_watch_budget_plan_pressure_level: Any = None
    sell_watch_budget_plan_reason: Any = None
    sell_watch_budget_plan_limit: Any = 0
    rate_limit_triggered: Any = False
    rate_limit_source: Any = None
    backoff_applied_seconds: Any = 0
    api_transient_error_source: Any = None
    api_transient_backoff_applied_seconds: Any = 0
    request_window_size_before_sell_watch: Any = 0
    request_window_size_before_buy_scan: Any = 0
    throttle_guard_triggered: Any = False
    throttle_guard_reason: Any = None
    adaptive_pacing_used: Any = False
    adaptive_pacing_extra_delay_ms: Any = 0.0
    rate_limit_partial_stop: Any = False
    rate_limit_partial_stop_symbol: Any = None
    rate_limit_partial_completed_count: Any = 0
    rate_limit_partial_remaining_count: Any = 0
    execution_tail_backoff_drain_ms: Any = 0.0
    sell_watch_backoff_drain_ms: Any = 0.0
    lane_scheduler_enabled: Any = False
    sell_lane_running: Any = False
    buy_lane_running: Any = False
    buy_lane_previous_scan_id: Any = None
    buy_lane_previous_started_at: Any = None
    order_gate_queue_depth: Any = 0
    order_gate_processed_count: Any = 0
    order_gate_last_decision: Any = None
    order_gate_last_intent_type: Any = None
    order_gate_last_symbol: Any = None
    order_gate_last_skip_reason: Any = None
    quote_age_max_ms: Any = None
    quote_age_avg_ms: Any = None
    suspected_shared_rate_bucket: Any = False
    cycle_budget_exceeded: Any = False
    budget_exceeded_stage: Any = None
    cycle_budget_ms: Any = 0.0
    buy_ranking_ms: Any = None
    rebalance_selection_ms: Any = None
    cycle_warning_count: Any = 0
    cycle_error_count: Any = 0
    state_write_ok: Any = False
    token: Any = None
    portfolio_snapshot: Any = None
    sell_analysis_results: Any = ()
    observed_market_snapshots: Any = field(default_factory=lambda: {})
    session_status: Any = None
    market_open: Any = False
    requested_buy_symbols: Any = ()
    raw_scan_results: Any = ()
    scan_results: Any = ()
    selection_details: Any = field(default_factory=lambda: {})
    selected_candidate: Any = None
    top_analysis_result: Any = None
    selected_sell_candidate: Any = None
    sell_watch_final_review: Any = None
    active_sell_analysis: Any = None
    execution_snapshot: Any = None
    position_sizing: Any = None
    sell_position_sizing: Any = None
    buy_risk_guard_payload: Any = None
    sell_risk_guard_payload: Any = None
    rebalance_buy_preview: Any = None
    quality_rebalance_preview: Any = None
    reconciliation_report: Any = None
    buy_pre_gating: Any = field(default_factory=lambda: {'scan_allowed': True, 'requested_symbols': (), 'allowed_symbols': (), 'rejected': [], 'reason_counts': {}, 'early_reject_count': 0})
    buy_scan_profile_state: Any = field(default_factory=lambda: {'profile': 'momentum', 'rotation_enabled': False, 'cursor_before': 0, 'cursor_after': 0})
    buy_scan_layered_universe: Any = field(default_factory=lambda: {'selected_symbols': (), 'layer_by_symbol': {}, 'core_count': 0, 'rotating_count': 0, 'exploration_count': 0, 'selected_core_count': 0, 'selected_rotating_count': 0, 'selected_exploration_count': 0, 'preview': {}})
    buy_scan_shallow_plan: Any = field(default_factory=lambda: {'ranked_count': 0, 'deep_eval_limit': 0, 'exploration_quota_used': 0, 'shortlist_symbols': (), 'shortlist_preview': []})
    daily_pnl_brake_state: Any = None
    regime_state: Any = None
    effective_buy_settings: Any = None
    cycle_error: Any = None
    cycle_environment: Any = 'mock'
    lane_scheduler_final_overrides: Any = field(default_factory=lambda: {})
    # gate C3: promoted from the run_cycle parameter (mutated at :1351/:1372, consumed at :2662)
    buy_scan_due: Any = True
