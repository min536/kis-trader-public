# app/main.py split plan

> **Status: COMPLETE / FROZEN (2026-06-02).** The `app/main.py` split finished under the documented safe boundary. The "Current structure inventory" section below is the **pre-split baseline snapshot**; the realized end state (5,965 lines, 119 top-level functions) is recorded in the Stage 5 completion section. Preserved as the design+execution record — not an active plan.

Updated: 2026-06-02

This document is a design plan only. It does not authorize code movement, runtime
behavior changes, live scans, broker calls, or order-path changes. The first
goal is to make future refactoring small, reviewable, and reversible while
keeping `app/main.py` as the orchestration entrypoint.

## 1. Current structure inventory

### Size

| Metric | Value |
| :--- | :--- |
| Total lines | 8,659 |
| Import section | lines 1-257 |
| Top-level functions | 132 |
| `run_cycle()` | 4,095 lines (4323-8417) |
| `main()` | 236 lines (8420-8655) |

Largest helpers by line count:

| Function | Lines | Responsibility |
| :--- | :--- | :--- |
| `run_cycle` | 4,095 | Main cycle orchestration |
| `_run_sell_order_flow` | 522 | End-to-end SELL execution |
| `_build_buy_candidate_outcome_records` | 240 | Candidate outcome logging |
| `main` | 236 | Runtime loop entrypoint and session orchestration |
| `_print_applied_settings` | 149 | Settings dump |
| `_run_sell_test_cycle` | 148 | Test sell cycle |
| `_build_buy_cycle_funnel_stats` | 143 | Funnel statistics |
| `_build_runtime_rate_control` | 131 | Rate control |
| `_run_sell_guard_selftest` | 117 | Sell guard selftest |
| `_build_rebalance_buy_preview` | 88 | BUY-flow-adjacent rebalance preview, completed in Stage 3b |
| `_print_sell_decision` | 86 | SELL decision display |
| `_print_engine_schedule_state` | 75 | Engine schedule display |
| `_print_buy_scan_metrics` | 69 | BUY scan metrics display |
| `_build_scan_only_runtime_mode_preview` | 67 | Scan-only runtime preview |
| `_print_cycle_timing` | 60 | Cycle timing display |
| `_resolve_benchmark_snapshot` | 60 | Benchmark snapshot resolution |

### Major responsibilities

`app/main.py` currently combines these responsibilities:

- startup validation and per-account process locking
- scheduler tick calculation and run-once/repeating-loop orchestration
- market-session checks, including order-time rechecks before BUY/SELL submits
- API request/quote budget tracking, rate-limit backoff, transient API backoff,
  degraded/midday runtime pressure control, and execution-tail waits
- account scope sync, token issue, balance inquiry, and portfolio snapshot build
- broker/runtime reconciliation for pending sell intents and synced positions
- daily PnL brake, drawdown/regime calculation, and effective BUY setting
  overrides
- SELL watch prioritization, cursoring, budget capping, decision evaluation, and
  `_run_sell_order_flow()`
- BUY universe resolution from live snapshot or settings, layered scan universe,
  pre-gating, shallow/deep scan planning, candidate scoring, runtime guards, and
  BUY execution
- rebalance candidate selection and rebalance sell execution
- Slack order notifications, Slack runtime status snapshots, bottleneck alerts,
  Telegram rate-limit alerts
- cycle snapshot, candidate outcome, cycle stats, performance report, daily
  summary, and runtime state persistence
- console output and operator-facing status formatting

### Top-level function clusters (detailed)

#### A. Formatting & Display Helpers (~430 lines, 203-630)

Pure formatting functions with no side effects or state mutation.

`_sell_status_letter` (6), `_build_sell_strategy_summary` (7),
`_format_bps` (4), `_format_signed_krw` (9), `_format_signed_pct` (9),
`_build_cycle_id` (25), `_print_buy_strategy` (11),
`_print_buy_score_summary` (50), `_print_sell_decision` (88),
`_print_sell_preview` (25), `_print_buy_orderable_preview` (39),
`_build_preview_orderable_output_from_portfolio` (11),
`_build_math_sizing_context` (11)

#### B. Startup & Settings Display (~240 lines, 617-860)

`_print_runtime_mode` (8), `_print_applied_settings` (151),
`_print_runtime_parameter_validation` (45),
`_print_startup_sanity_report` (23), `_print_test_mode` (13),
`_sell_test_active` (4), `_run_sell_guard_selftest` (119)

#### C. Notification / Slack Helpers (~110 lines, 263-375)

`_get_slack_notifier` (10), `_get_bottleneck_aggregator` (7),
`_record_bottleneck` (25), `_record_main_loop_exception_if_needed` (6),
`_slack_event_type_for_order_action` (4),
`_send_order_slack_notification` (39),
`_slack_session_status_text` (4),
`_write_slack_runtime_status_snapshot` (17),
`_market_session_status_payload` (4)

#### D. Cycle Timing & Metrics (~275 lines, 980-1255)

`_print_cycle_header` (7), `_emit_status` (4),
`_format_cycle_summary` (34), `_request_metrics_delta` (42),
`_phase_timing_summary` (16), `_format_timing_line` (10),
`_print_cycle_timing` (62), `_print_api_usage` (29),
`_print_buy_scan_metrics` (71), `_print_sell_metrics` (40)

#### E. Engine Scheduling (~305 lines, 1295-1600)

`_is_due` (6), `_print_engine_schedule_state` (77),
`_log_engine_event` (25), `_build_scheduler_tick_decision` (51)

#### F. API Budget Management (~477 lines, 1597-2074)

25 functions: `_prune_recent_rate_limit_hits`, `_prune_recent_transient_api_errors`,
`_build_runtime_rate_control` (131), `_prune_api_budget_requests`,
`_build_api_budget_state`, `_summarize_api_budget_state` (49),
`_api_budget_backoff_active`, `_api_budget_transient_backoff_active`,
`_api_budget_backoff_remaining_seconds`,
`_api_budget_transient_backoff_remaining_seconds`,
`_api_budget_can_request` (24), `_api_budget_register_request`,
`_api_budget_register_requests`, `_api_budget_register_measured_extra_requests`,
`_api_budget_can_quote`, `_api_budget_remaining_requests`,
`_api_budget_request_window_size`, `_api_budget_remaining_quotes`,
`_buy_scan_reserve_active`, `_api_budget_preserves_buy_scan_reserve`,
`_api_budget_note_rate_limit` (31), `_api_budget_note_transient_api_error`,
`_api_budget_update_rate_limit_recovery_state`,
`_api_budget_update_transient_recovery_state`,
`_api_budget_min_wait_for_request_slot`,
`_wait_for_execution_request_budget` (40)

#### G. Error Classification Helpers (~156 lines, 2074-2230)

`_looks_like_rate_limit_error` (9), `_looks_like_buy_untradable_response` (17),
`_looks_like_no_position_sell_response` (12),
`_looks_like_transient_api_error` (7),
`_transient_api_source_from_exception` (21),
`_rate_limit_source_from_response_body` (10),
`_rate_limit_source_from_exception` (17),
`_should_downgrade_empty_buy_scan_to_backoff` (22),
`_build_buy_scan_rate_limit_degraded_reason` (25)

#### H. Daily PnL Brake & Manual Pause (~277 lines, 2214-2491)

`_clear_daily_pnl_pause_if_expired` (16),
`_manual_buy_pause_override_path` (12),
`_is_manual_buy_pause_override_active` (22),
`_build_daily_pnl_brake_state` (181), `_print_daily_pnl_brake_state` (46),
`_build_daily_pnl_brake_observability` (46)

#### I. Regime State (~174 lines, 2491-2665)

`_build_regime_state` (105), `_print_regime_state` (42),
`_print_premarket_wait_notice` (15), `_should_run_light_session_cycle` (8)

#### J. Scan-Only Diagnostics (~270 lines, 2661-2930)

`_print_scan_only_notice` (5),
`_build_scan_only_diagnostic_portfolio_snapshot` (41),
`_build_scan_only_diagnostic_sell_analyses` (32),
`_build_scan_only_runtime_mode_preview` (69),
`_print_scan_only_runtime_mode_preview` (15),
`_print_scan_only_diagnostic_buy_scan` (36),
`_build_scan_only_diagnostic_summary` (29),
`_print_scan_only_diagnostic_summary` (23)

#### K. Cycle Conclusion & Action Recording (~83 lines, 2911-2994)

`_print_cycle_conclusion` (16), `_print_last_action` (5),
`_record_cycle_action` (22), `_print_runtime_state_summary` (40)

#### L. Portfolio & Performance Analysis (~506 lines, 2994-3500)

`_resolve_benchmark_snapshot` (62), `_build_sell_analysis` (53),
`_build_sell_execution_snapshot` (16), `_is_same_korean_date` (10),
`_build_today_realized_summary` (56),
`_build_daily_pnl_brake_observability` (46),
`_print_portfolio_positions` (51),
`_print_account_balance_interpretation` (58),
`_print_today_performance_summary` (51),
`_print_today_bought_tracking` (53)

#### M. Position Sizing & Rebalance (~720 lines, 3450-4170)

`_position_sizing_requires_rebalance` (8),
`_build_position_sizing_block_context` (41),
`_build_buy_analysis_block_context` (21),
`_calculate_rebalance_sell_sizing` (51),
`_build_concentration_metrics` (49),
`_estimate_rebalance_concentration_preview` (55),
`_serialize_rebalance_holding_option` (18),
`_serialize_replacement_candidate_option` (14),
`_build_rebalance_pair_evaluation` (112),
`_build_rebalance_candidate` (154),
`_build_quality_rebalance_preview` (116),
`_print_rebalance_preview` (49),
`_print_quality_rebalance_preview` (31), `_print_rebalance_skip` (5)

#### N. Buy Scan Pre-Gating & Universe (~1,436 lines, 4174-5610)

`_count_symbol_buy_entries_today` (19),
`_is_symbol_in_reentry_cooldown` (21),
`_resolve_sell_exit_reason` (17),
`_build_buy_scan_pre_gating` (162),
`_print_buy_pre_gating_summary` (64),
`_normalize_pre_gating_payload` (72),
`_normalize_buy_funnel_reason` (57),
`_resolve_deep_eval_rejection_reason` (34),
`_resolve_buy_candidate_rejection_reason` (51),
`_resolve_buy_candidate_selection_outcome` (27),
`_build_buy_candidate_outcome_records` (242),
`_build_buy_cycle_funnel_stats` (145),
`_serialize_runtime_market_snapshot` (13),
`_update_recent_market_snapshots` (18),
`_take_circular_window` (17),
`_select_buy_scan_profile` (19),
`_build_buy_scan_layered_universe` (190),
`_build_buy_scan_shallow_plan` (126),
`_build_buy_scan_deep_eval_symbols` (22),
`_cap_buy_scan_deep_eval_symbols_for_api_budget` (45),
`_print_buy_scan_stage_summary` (72)

#### O. Reconciliation (~193 lines, 5607-5800)

`_build_positions_qty_map` (10), `_build_reconciliation_report` (104),
`_sync_reconciliation_state` (62), `_print_reconciliation_report` (17)

#### P. Rebalance BUY Preview (~140 lines, 5800-5940)

`_build_rebalance_buy_preview` (90), `_print_rebalance_buy_preview` (51)

#### Q. SELL Order Flow (~524 lines, 5941-6465)

`_run_sell_order_flow` (524) — end-to-end SELL execution: guard check,
broker call, logging, Slack notification, state mutation.

#### R. Buy Runtime Guards (~108 lines, 6465-6573)

`_apply_buy_runtime_guards_to_scan_results` (53),
`_print_buy_runtime_filter_summary` (55)

#### S. Sell Test Cycle (~148 lines, 6573-6721)

`_run_sell_test_cycle` (148)

#### T. run_cycle phases (4,097 lines, 6721-10817)

1. **Init & scheduling** (6721-6955): ~230 lines of variable init, cycle ID,
   state loading, ~100 mutable local variables
2. **Inner helpers** (6955-7025): `note()`, `resolve_buy_universe_symbols()`,
   `log_buy_universe_source()` — closure-captured nonlocal state
3. **Early exit checks** (7025-7255): self-test, sell test, session check,
   light cycle, API backoff
4. **Balance inquiry** (7255-7365): token issue, balance API, portfolio snapshot
5. **Scan-only diagnostic** (7365-7530): scan_only mode alternative path
6. **Portfolio display** (7530-7560): balance, reconciliation, positions
7. **SELL watch** (7560-7960): sell analysis per holding, rate limit handling,
   partial evaluation, cursor management
8. **Portfolio analysis** (7960-8055): PnL brake, regime, performance display
9. **SELL execution** (8055-8400): sell candidate selection, rebalance check,
   sell order flow invocation
10. **BUY scan setup** (8400-8660): universe, pre-gating, shallow/deep plans
11. **BUY scan execution** (8660-8845): symbol-by-symbol quote + analyze loop
12. **BUY candidate selection** (8845-9050): top candidate, no-candidate handling
13. **BUY pre-execution** (9050-9330): orderable cash, execution snapshot, sizing
14. **Rebalance flow** (9330-9680): evaluation, sell-for-rebalance, buy preview
15. **BUY guard checks** (9680-10080): daily limit, cooldown, budget, exposure
16. **BUY order execution** (10080-10300): `buy_market` call, success/failure
17. **Cycle finalization** (10300-10817): `finally` block with cycle snapshot,
    performance snapshot, state save, daily summary, Slack update

### Imported subsystems

`app/main.py` imports from most of the application:

- `app.auth`: settings, account scope, token/API error handling
- `app.core`: session lock, costs, formatters, market session, order log,
  runtime budget helpers, sell-watch budget/cursor, throttle, time utilities
- `app.domestic_stock`: balance, quote, orderable cash, mock order APIs
- `app.execution`: position sizing, order guards, execution snapshot schema
- `app.market_data`: live snapshot worker state and market snapshot schema
- `app.math_models`: math sizing overlay
- `app.notifications`: Slack notifier, bottleneck alerts, runtime status snapshot,
  runtime hook mappings
- `app.portfolio`: portfolio snapshot schema
- `app.reporting`: daily summary, cycle snapshots, candidate outcomes,
  performance reports, Telegram alerts
- `app.risk`: buy/sell risk guards
- `app.scanner`: scan execution, scoring, selection details, symbol names
- `app.strategy`: buy/sell decisions, reentry, core shadow diagnostics,
  sell-test scenarios
- `app.runtime_state`: load/save and many state mutation helpers

### Current pain points

- `run_cycle()` owns too many mutable locals (~100 variables), and the final
  snapshot/state write in the `finally` block depends on many of them being
  initialized correctly across early returns.
- Decision logic, IO, logging, notification, state mutation, sleeps, and API
  budget accounting are interleaved, making safe review difficult.
- BUY, SELL, rebalance, scanner, reporting, and runtime budget flows all depend
  on shared `state`, `settings`, `api_budget_state`, `session_status`, and
  `cycle_id` values.
- Order flow sequencing is critical and currently implicit in the body order:
  guard, risk check, market recheck, submitted log, state mutation, Slack
  notification, budget reservation, broker call, success/failure log, Slack
  result notification.
- Runtime-state schema and log payloads are broad and consumed by tests, Slack
  bot rendering, dashboards, replay tooling, and postrun analysis.
- Import-cycle risk is high if extracted modules import `app.main` for helpers.
- Several tests (35 files, ~2,753 lines) patch or inspect `app.main` symbols, so
  compatibility shims may be needed during extraction.
- 3 inner functions in `run_cycle` (`note`, `resolve_buy_universe_symbols`,
  `log_buy_universe_source`) capture nonlocal state and cannot be extracted
  without signature changes.

### Safety guards already present

These guards must be preserved exactly during any split:

- **Duplicate `app.main` process guard**: `main()` acquires a per-account fcntl
  lock via `acquire_app_main_lock()` before shared state access or broker/API
  work. A duplicate process for the same account signature exits with code 1.
- **Repeated SELL cooldown guard**: `_run_sell_order_flow()` uses
  `build_sell_cooldown_key(symbol, trigger)` for non-emergency repeated SELL
  cooldown, intentionally excluding qty so partial sell sizing changes do not
  bypass cooldown.
- **PID/process_id logging**: existing additive PID fields identify which process
  wrote order/runtime/cycle/Slack status artifacts.
- **40240000 no-position classification**:
  `_looks_like_no_position_sell_response()` classifies KIS no-position SELL
  failures and writes `failure_category="no_position_on_sell"` while preserving
  the original response.

## 2. Split principles

- No behavior change in the initial extraction.
- Pure extraction before redesign.
- Preserve the order of operations, especially around order submission.
- Preserve runtime state schema and existing state key names.
- Preserve log formats and payload shapes unless a later plan explicitly changes
  them.
- Preserve Slack notification semantics, event type mapping, and call timing.
- Preserve all safety guards listed above.
- Preserve and extend focused test coverage before moving high-risk code.
- Keep commits small; separate docs/test/code commits where possible.
- Keep `main.py` as orchestration only after the split; do not move live trading
  startup into a new entrypoint.
- Add compatibility shims in `app.main` when existing tests or operators rely on
  those symbols.

## 3. Proposed target module structure

The module names below are targets, not immediate work. Prefer expanding existing
modules when they already own the concept. Function clusters (§1) map to modules
as shown below.

### 3.1 Extraction targets

| Module | Cluster | Lines | Functions | Risk | Key tests |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `app/execution/order_notifications.py` | C (partial) | ~50 | `_slack_event_type_for_order_action`, payload part of `_send_order_slack_notification` | **Low** | `test_main_slack_order_hooks`, `test_slack_notifier`, `test_slack_bot` |
| `app/core/runtime_budget.py` (existing, expand) | F | ~477 | All 25 `_api_budget_*`, `_prune_*`, `_buy_scan_reserve_active`, `_wait_for_execution_request_budget`, `_build_runtime_rate_control` | **Medium-High** | `test_runtime_budget_helpers`, `test_runtime_rate_control`, `test_buy_scan_budget_cap`, `test_buy_order_submit_budget` |
| `app/core/error_classification.py` | G | ~156 | All 9 `_looks_like_*`, `_*_source_from_*`, `_should_downgrade_*`, `_build_buy_scan_rate_limit_degraded_reason` | **Low** | `test_main_rate_limit_body_sources`, `test_no_position_sell_detection`, `test_sell_watch_rate_limit_body` |
| `app/execution/sell_flow.py` | Q | 599 | Stage 3a complete: SELL order execution flow | **High** | `test_order_guard`, `test_no_position_sell_detection`, `test_main_slack_order_hooks`, `test_pid_logging` |
| `app/execution/buy_flow.py` | run_cycle §13-16 | 1,769 | Stage 3b complete: BUY execution flow, `_build_preview_orderable_output_from_portfolio`, `_build_math_sizing_context`, `_build_rebalance_buy_preview`, `_print_rebalance_buy_preview` | **High** | `test_buy_orderable_preview`, `test_buy_orderable_market_session_order`, `test_buy_order_submit_budget`, `test_order_guard` |
| `app/scanner/runtime_scan.py` | N (partial) | ~1,013 | Stage 2e complete: pre-gating, normalizers, profile/layered universe, shallow/deep plans, runtime guards, scan display, API budget cap | **Medium-High** | `test_runtime_scan_helpers`, `test_buy_scan_budget_cap`, `test_scanner_parse_error_skip`, `test_scan_only_diagnostic` |
| `app/reporting/runtime_snapshots.py` | N (partial) + K | ~520 | Stage 2f complete: `_build_buy_candidate_outcome_records`, `_build_buy_cycle_funnel_stats`, `_serialize_runtime_market_snapshot`, `_update_recent_market_snapshots`, `_record_cycle_action`, `_log_engine_event`, cycle/runtime display helpers | **Medium-High** | `test_runtime_snapshot_helpers`, `test_cycle_snapshots`, `test_pid_logging`, `test_main_benchmark_snapshot` |
| `app/risk/pnl_brake.py` | H | ~277 | `_clear_daily_pnl_pause_if_expired`, `_manual_buy_pause_override_path`, `_is_manual_buy_pause_override_active`, `_build_daily_pnl_brake_state`, `_print_daily_pnl_brake_state`, `_build_daily_pnl_brake_observability` | **Medium** | `test_daily_pnl_brake`, `test_daily_pnl_brake_logging` |
| `app/risk/regime.py` | I | ~174 | `_build_regime_state`, `_print_regime_state`, `_should_run_light_session_cycle`, `_print_premarket_wait_notice` | **Medium** | `test_regime_state` |
| `app/execution/rebalance.py` | M + P | ~860 | All 14 rebalance evaluation/display functions; BUY preview ownership moved with Stage 3b BUY flow | **Medium** | `test_rebalance_candidate` |
| `app/runtime/session_context.py` | A (partial) + run_cycle init | ~100 | `_build_cycle_id`, account-scope setup, state init helpers | **Medium** | `test_pid_logging`, `test_cycle_snapshots` |
| `app/runtime/session_loop.py` | E + main loop | ~300 | Loop from `main()`, `_is_due`, `_build_scheduler_tick_decision` | **High** | `test_session_lock`, `test_runtime_rate_control` |
| `app/runtime/heartbeat.py` | C (partial) | ~30 | `_write_slack_runtime_status_snapshot`, session status payload wrappers | **Low-Medium** | `test_runtime_status_snapshot`, `test_main_runtime_hooks` |
| `app/notifications/slack_runtime.py` | C (partial) | ~40 | `_record_bottleneck`, `_record_main_loop_exception_if_needed`, singleton accessors | **Medium** | `test_main_bottleneck_hooks` |
| (inline in `app/main.py`) | J, S | ~420 | Scan-only diagnostics, sell test cycle — low-frequency code paths, extract later | **Low** | `test_scan_only_diagnostic` |

### 3.2 Stays in `app/main.py`

| What | Why |
| :--- | :--- |
| `run_cycle()` | Orchestration body stays. Becomes shorter (~2,500-3,000 lines) as helpers move out, but control flow remains here. |
| `main()` | Entry point stays. Lock, startup validation, loop invocation. |
| 3 inner functions | `note()`, `resolve_buy_universe_symbols()`, `log_buy_universe_source()` — capture nonlocal state. Extract in redesign phase. |
| `_print_*` console functions | ~24 print-only functions (~800 lines). Low priority — they produce no return values. Extract in cleanup phase if desired. |

### 3.3 Design note: domain packages vs flat `main_helpers/`

Functions are placed into existing domain packages (`app/execution/`, `app/risk/`,
`app/scanner/`, etc.) rather than a flat `app/main_helpers/` package.
- Advantage: code lives where its concept belongs; no special-purpose package.
- Risk: import cycles if new modules try to import from `app.main`.
  Mitigation: extract downward only — new modules import schema/utility modules,
  not `app.main`. `app.main` imports the new modules.

## 4. Staged extraction plan

### Stage 0 — Characterization / guardrails

Goal: establish baseline test coverage before moving code.

| Task | Detail |
| :--- | :--- |
| Run full test suite | `pytest tests/ -v` — record baseline pass count |
| Verify `py_compile` | `python -m py_compile app/main.py` |
| Add smoke import test | `tests/test_main_helpers_import.py` if new packages are created |
| Audit test patches | Identify tests that `patch("app.main._xxx")` — these need shims |

- Files touched: tests only.
- Expected risk: low, but tests that patch `app.main` may reveal hidden coupling.
- Validation command:
  `pytest tests/ -v --tb=short`
- Rollback: revert the characterization commit; no runtime code depends on it.
- Commit boundary: one guardrail/test commit, separate from code extraction.

### Stage 1 — Low-risk pure helpers

Goal: extract helpers that do not call broker APIs, do not sleep, and do not
mutate runtime state beyond a returned value.

**Step 1a**: `app/core/error_classification.py` (~156 lines, cluster G)
- 9 pure classifier functions
- Dependencies: `ApiHttpError` from `app.auth.token`
- Well-tested via `test_main_rate_limit_body_sources`, `test_no_position_sell_detection`

**Step 1b**: `app/execution/order_notifications.py` (~50 lines, cluster C partial)
- Pure payload builder from `_send_order_slack_notification`
- Keep actual Slack send in wrapper or injectable
- Tested via `test_main_slack_order_hooks`

**Step 1c**: Expand `app/core/runtime_budget.py` with pure budget helpers
- Start with pure query functions: `_api_budget_backoff_active`,
  `_api_budget_remaining_requests`, `_summarize_api_budget_state`, etc.
- Avoid mutation functions in this step

**Step 1c-1 (done 2026-05-18)**: pure query helpers → `app/core/runtime_budget.py`
- `api_budget_backoff_active`, `api_budget_transient_backoff_active`,
  `api_budget_backoff_remaining_seconds`, `api_budget_transient_backoff_remaining_seconds`,
  `api_budget_can_quote`, `api_budget_remaining_quotes`,
  `api_budget_can_request`, `api_budget_remaining_requests`,
  `api_budget_request_window_size`, `api_budget_preserves_buy_scan_reserve`
- `app/main.py` imports these under private aliases (`_api_budget_backoff_active`, etc.)

**Step 1c-2 (done 2026-05-18)**: request-pruning and registration helpers →
`app/core/runtime_budget.py`
- `prune_api_budget_requests` (mutation: prunes `recent_requests` list in-place)
- `api_budget_register_request`, `api_budget_register_requests`,
  `api_budget_register_measured_extra_requests`

**Step 1c-3 (analysis 2026-05-20)**: classification of remaining cluster-F helpers

Remaining helpers still in `app/main.py` after Steps 1c-1 and 1c-2:

| Helper | Lines | Classification | Rationale |
| :--- | :--- | :--- | :--- |
| `_prune_recent_rate_limit_hits` | ~13 | **Stage 1 extractable** | Pure list filter + dict mutation of `recent_rate_limit_hit_times`. No I/O, no sleep, no notifications. Depends only on `_API_TRANSIENT_BACKOFF_WINDOW_SECONDS` constant (must move with it or be parameterised). Used by `_summarize_api_budget_state`, `_api_budget_note_rate_limit`, `_build_runtime_rate_control`. |
| `_prune_recent_transient_api_errors` | ~13 | **Stage 1 extractable** | Same pattern as above for `recent_transient_error_times`. Depends on `_API_TRANSIENT_BACKOFF_WINDOW_SECONDS`. |
| `_summarize_api_budget_state` | ~47 | **Stage 1 extractable** | Pure read/compute — calls `_prune_api_budget_requests` (already extracted), `_prune_recent_rate_limit_hits`, `_prune_recent_transient_api_errors`. No sleep, no print, no notifications. Extract only after the two prune helpers above are moved. |
| `_api_budget_register_request` | ~11 | **Deferred** | Full implementation still in `app/main.py`. Calls internal `_prune_api_budget_requests` (not the extracted public `prune_api_budget_requests`). Not a shim. Deferred at Stage 1c close-out (2026-05-20). |
| `_api_budget_register_requests` | ~13 | **Deferred** | Same. |
| `_api_budget_register_measured_extra_requests` | ~21 | **Deferred** | Same. |
| `_api_budget_update_rate_limit_recovery_state` | ~13 | **Stage 1 extractable** | Pure dict mutation, no I/O. Depends only on `_api_budget_backoff_active` (already extracted). Clears or updates `last_rate_limit_source` / `rate_limit_hits` during recovery. |
| `_api_budget_update_transient_recovery_state` | ~14 | **Stage 1 extractable** | Mirror of above for transient recovery. Depends on `_api_budget_transient_backoff_active` (already extracted). |
| `_api_budget_min_wait_for_request_slot` | ~17 | **Stage 1 extractable** | Pure computation — returns a float wait duration. No sleep, no print. Depends on `_prune_api_budget_requests` (already extracted). Safe to move once prune helpers are in the module. |
| `_api_budget_note_rate_limit` | ~29 | **Extract with care (Stage 1 boundary)** | Mutates `api_budget_state`, calls `note_rate_limit()` (from `app.core.throttle`) and `_record_bottleneck()` (from `app.main` singleton), and does a `print()`. The `_record_bottleneck` call is a closure over `app.main`'s module-level singleton — extracting this function requires injecting the bottleneck recorder or breaking the direct call. Move only after `_record_bottleneck` / bottleneck singleton is extracted (planned in `app/notifications/slack_runtime.py`). |
| `_api_budget_note_transient_api_error` | ~26 | **Extract with care (Stage 1 boundary)** | Mutates `api_budget_state`, does a `print()`. No bottleneck call, no Slack hook. Depends only on constants (`_API_TRANSIENT_*`) and `_prune_recent_transient_api_errors`. Can be extracted after the prune helper and constants are moved — no injected dependency required. Lower risk than `_api_budget_note_rate_limit`. |
| `_wait_for_execution_request_budget` | ~37 | **Stage 2/4 — defer** | Calls `time.sleep()` and the `note()` inner function from `run_cycle`. The `note()` dependency is a closure-captured nonlocal — it cannot be extracted without a signature change. Defer until `run_cycle` inner functions are addressed (Stage 4 or redesign). |
| `_build_runtime_rate_control` | ~129 | **Stage 2 — defer** | Large builder; mutates `api_budget_state` (consecutive backoff cycles, degraded mode), calls `_prune_recent_rate_limit_hits`, reads many `settings` fields, calls `_within_hhmm_window` (not a budget helper), and does a `print()`. Target module would be `app/runtime/session_context.py` or a new `app/core/runtime_pressure.py`, not `app/core/runtime_budget.py`. Extract after Stage 2 state helpers are stable. |
| `_build_scheduler_tick_decision` | ~49 | **Stage 4 — defer** | Pure logic but belongs to the session loop (cluster E in the split plan). Target module is `app/runtime/session_loop.py`. Do not extract ahead of the loop; it has no benefit in isolation before Stage 4. |
| `_buy_scan_reserve_active` | ~14 | **Stage 1 extractable (low priority)** | Pure boolean check on `settings` and `session_status`. No I/O, no side effects. Could move to `app/core/runtime_budget.py` or stay near `_build_buy_scan_layered_universe` (cluster N). Low urgency because it is only called once in `run_cycle` and has no test coverage of its own. |
| `_build_api_budget_state` | ~19 | **Stage 2 — defer (initializer)** | Constructs the initial `api_budget_state` dict from `settings`. Belongs with module-level API budget initialisation, ideally co-located with the full budget state schema in `app/core/runtime_budget.py`. Extract as part of a clean budget-module finalisation pass, not piecemeal. |

**Stage 1c execution status (2026-05-20 close-out):**

The planned batch of 7 helpers was split across two commits:

**Done — Step 1c-4a (`9a7cafe` 2026-05-20):**

1. `_prune_recent_rate_limit_hits` → `prune_recent_rate_limit_hits`
2. `_prune_recent_transient_api_errors` → `prune_recent_transient_api_errors`
3. `_summarize_api_budget_state` → `summarize_api_budget_state`

**Done — Step 1c-4b (`6abdd56` 2026-05-20):**

4. `_api_budget_update_rate_limit_recovery_state` → `api_budget_update_rate_limit_recovery_state`
5. `_api_budget_update_transient_recovery_state` → `api_budget_update_transient_recovery_state`

**Deferred — not extracted (still full implementations in `app/main.py`):**

- `_api_budget_min_wait_for_request_slot` — deferred; three transient constants not yet moved to module; move alongside register helpers
- `_api_budget_note_transient_api_error` — deferred; same constant dependency
- `_api_budget_register_request`, `_api_budget_register_requests`, `_api_budget_register_measured_extra_requests` — full implementations in `app/main.py`, not shims; call internal `_prune_api_budget_requests`
- Three transient constants (`_API_TRANSIENT_BACKOFF_WINDOW_SECONDS`, `_API_TRANSIENT_BASE_BACKOFF_SECONDS`, `_API_TRANSIENT_MAX_BACKOFF_SECONDS`) — still in `app/main.py`; move in same commit as deferred helpers above

**Deferred to Stage 2 / later:**
- `_api_budget_note_rate_limit` — needs bottleneck singleton decoupling first
- `_build_runtime_rate_control` — target is a runtime-pressure module, not `runtime_budget.py`
- `_build_api_budget_state` — budget state initializer, extract in full-module cleanup

**Deferred to Stage 4:**
- `_wait_for_execution_request_budget` — calls `note()` inner function (closure-captured nonlocal); confirmed in code
- `_build_scheduler_tick_decision` — belongs to session loop module

- Avoid in this stage: `_wait_for_execution_request_budget`,
  `_api_budget_note_rate_limit`, `_run_sell_order_flow`, the BUY order submit
  block, `run_cycle()`, and `main()` loop movement.
- Expected risk: low to medium.
- Validation:
  ```
  python -m py_compile app/main.py app/core/error_classification.py
  pytest tests/test_main_rate_limit_body_sources.py tests/test_no_position_sell_detection.py tests/test_main_slack_order_hooks.py tests/test_runtime_budget_helpers.py -v
  ```
- Rollback: keep `app.main` shims, then revert the new module.
- Commit boundary: one small helper family per commit.

### Stage 2 — State & domain logic extraction

Goal: extract stateful helpers and domain-specific builders.

**Step 2a**: `app/risk/pnl_brake.py` (~277 lines, cluster H)
- 6 functions including `_build_daily_pnl_brake_state` (181 lines)
- File system interaction: manual pause override path
- Tested via `test_daily_pnl_brake` (597 lines)

**Step 2b**: `app/risk/regime.py` (~174 lines, cluster I)
- 4 functions including `_build_regime_state` (105 lines)
- Affects effective buy settings — must verify regime output is identical
- Tested via `test_regime_state`

**Step 2c**: `app/execution/rebalance.py` (~860 lines, clusters M + P)
- 16 functions, concentration metrics, pair evaluation
- Tested via `test_rebalance_candidate` (279 lines)

**Step 2d**: Reconciliation helpers (~193 lines, cluster O)
- 4 self-contained functions, no broker API calls
- Expand existing module or create `app/core/reconciliation.py`

**Step 2e**: `app/scanner/runtime_scan.py` (~500 lines, cluster N partial)
- BUY scan planning: universe, pre-gating, shallow/deep plans
- Many settings dependencies; largest extraction by function count
- Tested via `test_buy_scan_budget_cap`, `test_scan_only_diagnostic`

**Step 2f**: `app/reporting/runtime_snapshots.py` (~400 lines, cluster N partial + K)
- Candidate outcome records, funnel stats, cycle recording
- Tested via `test_cycle_snapshots`, `test_pid_logging`

- Expected risk: medium.
- Validation:
  ```
  pytest tests/test_daily_pnl_brake.py tests/test_regime_state.py tests/test_rebalance_candidate.py tests/test_buy_scan_budget_cap.py tests/test_cycle_snapshots.py tests/test_scan_only_diagnostic.py -v
  ```
- Rollback: `git revert` per sub-step.
- Commit boundary: one sub-step per commit.

#### Stage 2a feasibility analysis (2026-05-20)

**Target module:** `app/risk/pnl_brake.py` (new file)

**Functions (7 total, ~323 lines):**

| Function | Lines | Location | Role |
| :--- | :--- | :--- | :--- |
| `_daily_pnl_brake_display_status` | ~7 | 1411 | Pure status string mapper |
| `_clear_daily_pnl_pause_if_expired` | ~16 | 1853 | State dict mutation (pause expiry) |
| `_manual_buy_pause_override_path` | ~10 | 1869 | File path builder (reads `get_runtime_state_path`) |
| `_is_manual_buy_pause_override_active` | ~20 | 1881 | File I/O: reads JSON override file |
| `_build_daily_pnl_brake_state` | ~179 | 1903 | Core builder: state mutation + telegram alert |
| `_print_daily_pnl_brake_state` | ~45 | 2084 | Console display |
| `_build_daily_pnl_brake_observability` | ~44 | 2830 | Pure observability dict builder |

Note: `_build_daily_pnl_brake_observability` is physically in cluster L (line 2830),
not adjacent to cluster H (lines 1853-2128), but logically belongs with the brake domain.

**Dependencies (imports the new module would need):**

| Dependency | Source | Risk |
| :--- | :--- | :--- |
| `build_daily_pnl_state` | `app.reporting.performance` | Low — leaf module |
| `parse_recent_order_time` | `app.core.time_utils` | Low — leaf module |
| `get_runtime_state_path` | `app.runtime_state` | Low — path utility |
| `get_korean_now` | `app.core.time_utils` | Low — leaf module |
| `get_account_scope_context` | `app.auth.account_scope` | Low — read-only accessor |
| `send_telegram_alert` | `app.reporting.telegram` | Low — fire-and-forget |
| `format_krw` | `app.core.formatters` | Low — pure formatter |
| `_format_signed_krw`, `_format_signed_pct` | `app.main` (cluster A) | Medium — still in `app.main`; either extract first or pass as parameters |

No dependency on `app.main` helpers except `_format_signed_krw`/`_format_signed_pct` (cluster A
formatting) and `_daily_pnl_brake_display_status` (moving with the cluster). No import cycle risk
because the new module imports only from leaf modules — `app.main` imports the new module, not
the reverse.

**Side effects:**

- `_build_daily_pnl_brake_state` mutates `state` dict in-place (12 key writes: `current_brake_state`,
  `daily_pnl_pause_until/state/reason`, `intraday_pnl_*`, `daily_pnl_baseline_abnormal_warned`,
  `last_notified_pnl_state`).
- `_build_daily_pnl_brake_state` calls `send_telegram_alert` on brake escalation — external side effect.
- `_build_daily_pnl_brake_state` calls `get_account_scope_context` — read-only singleton access.
- `_clear_daily_pnl_pause_if_expired` mutates `state` dict (3 key clears).
- `_is_manual_buy_pause_override_active` reads a JSON file from disk.
- `_print_daily_pnl_brake_state` writes to stdout.
- No sleep, no broker API calls, no Slack notification, no bottleneck singleton.

**State mutations are safe to extract** because the `state` dict is passed explicitly as a parameter.
The telegram alert is fire-and-forget with no return value dependency. The file I/O for manual
override reads a well-defined path and returns a boolean.

**Test coverage:**

- `test_daily_pnl_brake.py` (597 lines): tests `_build_daily_pnl_brake_state` via
  `main_module._build_daily_pnl_brake_state(...)`. Patches `app.main.build_daily_pnl_state` and
  `app.main._is_manual_buy_pause_override_active`.
- `test_daily_pnl_brake_logging.py` (136 lines): tests print/display functions via `main_module`.
- Both test files import `from app import main as main_module` — compatibility shims in `app.main`
  will keep these working without test changes in the extraction commit.

**Compatibility shim needs:**

- `app.main._build_daily_pnl_brake_state` — needed (6 direct test calls)
- `app.main._is_manual_buy_pause_override_active` — needed (2 test patches)
- `app.main._daily_pnl_brake_display_status` — needed (2 call sites in `app.main` itself: line 2115
  in `_print_daily_pnl_brake_state` and line 2519 in `_build_regime_state` context)
- `app.main._clear_daily_pnl_pause_if_expired` — no external test reference, but called by
  `_build_daily_pnl_brake_state` internally; moves with it
- `app.main._manual_buy_pause_override_path` — no external test reference; moves internally
- `app.main._print_daily_pnl_brake_state` — no test patches on this symbol directly
- `app.main._build_daily_pnl_brake_observability` — no test patches on this symbol directly

**Extraction risk: Medium-low.**

- The `_format_signed_krw`/`_format_signed_pct` dependency requires either: (a) extract those two
  3-line formatters to `app.core.formatters` first (safest, one tiny commit), or (b) duplicate them
  in the new module (not recommended), or (c) pass formatted strings from callers (too invasive).
  Option (a) is recommended as a pre-step.
- The telegram alert in `_build_daily_pnl_brake_state` is a side effect but self-contained — it
  sends a message and does not affect the function's return value or state mutations.
- File I/O in `_is_manual_buy_pause_override_active` is read-only and well-tested.
- No broker API calls, no sleep, no closure captures.

**Recommended implementation slice:**

1. Pre-step: extract `_format_signed_krw` and `_format_signed_pct` to `app.core.formatters` (tiny,
   ~15 lines, add shims in `app.main`). Separate commit.
2. Create `app/risk/pnl_brake.py` with all 7 functions (public names, drop leading underscore).
3. Update `app/main.py` imports + add compatibility shims.
4. Run validation.

**Validation:**
```
python -m py_compile app/main.py app/risk/pnl_brake.py
pytest tests/test_daily_pnl_brake.py tests/test_daily_pnl_brake_logging.py -v
pytest tests/ -v --tb=short
```

**Rollback:** revert the extraction commit; compatibility shims ensure no downstream breakage.

#### Stage 2d feasibility analysis (2026-05-20)

**Target module:** `app/core/reconciliation.py` (new file)

**Functions (4 total, ~192 lines):**

| Function | Lines | Location | Role |
| :--- | :--- | :--- | :--- |
| `_build_positions_qty_map` | ~8 | 5246 | Pure: portfolio → {symbol: qty} dict |
| `_build_reconciliation_report` | ~102 | 5256 | Pure computation: compares expected vs actual positions |
| `_sync_reconciliation_state` | ~60 | 5360 | State mutation: updates state dict + calls `record_symbol_exit` |
| `_print_reconciliation_report` | ~16 | 5422 | Console display |

**Dependencies (imports the new module would need):**

| Dependency | Source | Risk |
| :--- | :--- | :--- |
| `parse_recent_order_time` | `app.core.time_utils` | Low — leaf module |
| `get_korean_now` | `app.core.time_utils` | Low — leaf module |
| `record_symbol_exit` | `app.runtime_state` | Low — state mutation utility |

No dependency on any `app.main` helper. No dependency on `settings`. No import cycle risk.

**Side effects:**

- `_build_positions_qty_map`: pure, no side effects.
- `_build_reconciliation_report`: pure computation. Reads `state` dict but does not mutate it.
  No I/O, no API calls, no print.
- `_sync_reconciliation_state`: mutates `state` dict in-place (pending sell intents adjustment,
  broker sync timestamps, reconciliation summary). Calls `record_symbol_exit` for detected
  position discrepancies — this writes to runtime state but is already an external function
  from `app.runtime_state`.
- `_print_reconciliation_report`: writes to stdout.
- No sleep, no broker API calls, no Slack, no telegram, no file I/O, no bottleneck singleton.

**Test coverage:**

- **No direct test file** for reconciliation functions. `grep -rn` across `tests/` found zero
  references to `_build_reconciliation_report`, `_sync_reconciliation_state`,
  `_build_positions_qty_map`, or `_print_reconciliation_report`.
- The functions are exercised only through `run_cycle` integration (lines 7002 and 7188).
- **This means extraction needs characterization tests first** (Stage 0 pattern) to ensure
  behavior is preserved.

**Compatibility shim needs:**

- None required for tests (no test references).
- `app.main` call sites (lines 7002 and 7188 in `run_cycle`) will be updated to import from
  the new module.

**Extraction risk: Low (code) / Medium (test gap).**

- The code itself is cleaner than pnl_brake: fewer dependencies, no telegram, no file I/O,
  no formatter coupling.
- The only elevated risk is the **lack of direct test coverage**. `_build_reconciliation_report`
  has complex logic (expected vs actual position comparison, pending intent checks, order activity
  filtering) that should be unit-tested before extraction.
- `_sync_reconciliation_state` calls `record_symbol_exit` for reconciliation events — this is a
  significant state mutation that must be verified.

**Recommended implementation slice:**

1. Pre-step: write characterization tests for `_build_reconciliation_report` and
   `_sync_reconciliation_state` (separate test commit, ~100-150 lines estimated).
2. Create `app/core/reconciliation.py` with all 4 functions (public names).
3. Update `app/main.py` imports.
4. Run validation.

**Validation:**
```
python -m py_compile app/main.py app/core/reconciliation.py
pytest tests/test_reconciliation.py -v  # new characterization tests
pytest tests/ -v --tb=short
```

**Rollback:** revert the extraction commit; no compatibility shims needed.

#### Stage 2b feasibility analysis (2026-05-20)

**Target module:** `app/risk/regime.py` (new file)

**Functions (4 total, ~174 lines):**

| Function | Lines | Location | Role |
| :--- | :--- | :--- | :--- |
| `_build_regime_state` | ~103 | 2130 | Core builder: regime from brake state + drawdown |
| `_print_regime_state` | ~40 | 2235 | Console display |
| `_print_premarket_wait_notice` | ~13 | 2277 | Console display (light session) |
| `_should_run_light_session_cycle` | ~6 | 2292 | Pure boolean: session + run_mode check |

**Dependencies (imports the new module would need):**

| Dependency | Source | Risk |
| :--- | :--- | :--- |
| `math.floor` | stdlib | None |
| `format_krw`, `format_qty` | `app.core.formatters` | Low — leaf module |
| `_format_signed_pct` | `app.main` (cluster A) | Medium — same as 2a; extract to `app.core.formatters` first |

No dependency on any `app.main` helper beyond `_format_signed_pct` (used only by
`_print_regime_state`). No dependency on `app.risk.guards` or other risk modules.

**Coupling with daily_pnl_brake / effective BUY settings / risk-off state:**

`_build_regime_state` is the **downstream consumer** of `_build_daily_pnl_brake_state` output.
It reads `daily_pnl_brake_state["display_status"]` (a string from the brake dict) to decide
the regime level (NORMAL / CAUTION / RISK_OFF). It does NOT call any brake function directly —
the coupling is through the dict value, not a function call.

The regime state dict drives all effective BUY parameter overrides:
- `effective_buy_max_budget_per_trade_krw` (multiplier-adjusted)
- `effective_buy_max_account_exposure_pct` (multiplier-adjusted)
- `effective_buy_max_qty_per_trade` (multiplier-adjusted with floor)
- `effective_rebuy_cooldown_minutes` (regime-scaled)
- `effective_same_symbol_max_buys_per_day` (regime-capped)
- `effective_buy_daily_max_order_submissions` (regime-capped: 15 CAUTION, 10 RISK_OFF)

These effective values are consumed throughout `run_cycle` to control BUY sizing, frequency,
and exposure limits. The regime output dict is self-contained — callers use the dict values,
not the regime function.

**Extraction implication:** If 2a (pnl_brake) and 2b (regime) are both extracted,
`app/risk/regime.py` does NOT need to import `app/risk/pnl_brake.py`. The brake state dict is
passed as a parameter. The two modules are sibling extractees with no import dependency between them.

**Side effects:**

- `_build_regime_state`: pure computation. Reads `settings` and two input dicts, returns a new dict.
  No state mutation, no I/O, no print, no sleep, no API calls.
- `_print_regime_state`: writes to stdout. Reads `format_krw`, `format_qty`, `_format_signed_pct`.
- `_print_premarket_wait_notice`: writes to stdout only.
- `_should_run_light_session_cycle`: pure boolean check.

**Test coverage:**

- `test_regime_state.py` (54 lines, 2 test cases): calls `main_module._build_regime_state` directly.
  Tests NORMAL regime and RISK_OFF-from-drawdown regime.
- Coverage is thin — only 2 cases for a function with 4 regime paths (NORMAL, CAUTION from brake,
  CAUTION from drawdown, RISK_OFF from brake, RISK_OFF from drawdown) and 6 effective parameter
  calculations. Additional characterization tests are recommended but not blocking.
- No test patches `_print_regime_state`, `_print_premarket_wait_notice`, or
  `_should_run_light_session_cycle`.

**Compatibility shim needs:**

- `app.main._build_regime_state` — needed (2 direct test calls in `test_regime_state.py`)
- `app.main._should_run_light_session_cycle` — no external test reference, but called at
  lines 6696 in `run_cycle`; import update only
- `app.main._print_regime_state` — no external test reference
- `app.main._print_premarket_wait_notice` — no external test reference

**Extraction risk: Low.**

- `_build_regime_state` is pure computation with explicit parameters and no side effects.
- The only complication is `_format_signed_pct` in `_print_regime_state` — same pre-step as 2a
  (extract to `app.core.formatters`).
- 4 call sites in `run_cycle` (lines 6696, 6713, 7061/7066, 7649/7657) are straightforward
  import path changes.
- Thin test coverage is a minor risk but the function is deterministic and testable.

**Recommended implementation slice:**

1. Shares pre-step with 2a: extract `_format_signed_pct` to `app.core.formatters`.
2. Create `app/risk/regime.py` with all 4 functions (public names).
3. Update `app/main.py` imports + add `_build_regime_state` shim for test compatibility.
4. Optionally add 2-3 characterization tests for CAUTION-from-brake and RISK_OFF-from-brake paths.
5. Run validation.

**Validation:**
```
python -m py_compile app/main.py app/risk/regime.py
pytest tests/test_regime_state.py -v
pytest tests/ -v --tb=short
```

**Rollback:** revert the extraction commit; one shim to remove.

#### Stage 2c feasibility analysis (2026-05-22)

**Status: Feasibility analysis and evaluation-side extraction complete.**

The Stage 2 extraction pattern is now proven (2a/2b/2d complete). This analysis maps all 16
rebalance functions, classifies them by purity and domain, and recommends a sliced extraction.

**Target module:** `app/execution/rebalance.py` (new file)

**Functions (16 total, ~913 lines):**

| Function | Lines | Location | Role | Purity |
| :--- | :--- | :--- | :--- | :--- |
| `_position_sizing_requires_rebalance` | 8 | 2690 | Gate: checks if position sizing triggered rebalance | Pure |
| `_build_position_sizing_block_context` | 41 | 2698 | Dict builder: block reason context with rebalance skip reason | Pure |
| `_build_buy_analysis_block_context` | 21 | 2739 | Dict builder: buy analysis block reason context | Pure |
| `_calculate_rebalance_sell_sizing` | 51 | 2760 | Sell sizing: computes rebalance sell quantity | Pure (calls `calculate_sell_position_sizing`) |
| `_build_concentration_metrics` | 49 | 2811 | Portfolio metrics: weight distribution by symbol | Pure |
| `_estimate_rebalance_concentration_preview` | 55 | 2860 | Concentration delta: before/after swap preview | Pure (calls `_build_concentration_metrics`) |
| `_serialize_rebalance_holding_option` | 18 | 2915 | Serializer: holding option → display dict | Pure |
| `_serialize_replacement_candidate_option` | 14 | 2933 | Serializer: replacement candidate → display dict | Pure |
| `_build_rebalance_pair_evaluation` | 112 | 2947 | Core evaluator: scores a sell/buy pair for rebalance | Pure (calls `calculate_selection_score`, `calculate_sell_position_sizing`, `_estimate_rebalance_concentration_preview`) |
| `_build_rebalance_candidate` | 154 | 3059 | Orchestrator: selects weakest holding for rebalance | Pure (calls `_build_rebalance_pair_evaluation`, serializers) |
| `_build_quality_rebalance_preview` | 116 | 3213 | Preview builder: multi-pair quality improvement comparison | Pure (calls `_build_rebalance_pair_evaluation`, serializers) |
| `_print_rebalance_preview` | 49 | 3329 | Console display: rebalance decision summary | Side effect: stdout |
| `_print_quality_rebalance_preview` | 31 | 3378 | Console display: quality rebalance preview | Side effect: stdout |
| `_print_rebalance_skip` | 5 | 3409 | Console display: skip reason one-liner | Side effect: stdout |
| `_build_rebalance_buy_preview` | 139 | 4863 | BUY re-evaluation: synthetic snapshot after sell | Pure (calls `_build_math_sizing_context`, `calculate_position_sizing`, `_estimate_rebalance_concentration_preview`) |
| `_print_rebalance_buy_preview` | 50 | 4953 | Console display: rebalance buy preview | Side effect: stdout |

**Purity classification summary:**

- **Pure logic (12 functions, ~778 lines):** no I/O, no state mutation, no side effects.
  All take explicit parameters and return dicts or dataclass values.
- **Stdout display (4 functions, ~135 lines):** `_print_rebalance_preview`,
  `_print_quality_rebalance_preview`, `_print_rebalance_skip`, `_print_rebalance_buy_preview`.
  Write to stdout only. No state mutation, no API calls, no file I/O.

**Dependencies (imports the new module would need):**

| Dependency | Source | Risk | Used by |
| :--- | :--- | :--- | :--- |
| `calculate_sell_position_sizing` | `app.execution` | Low — leaf module | `_calculate_rebalance_sell_sizing`, `_build_rebalance_pair_evaluation` |
| `calculate_position_sizing` | `app.execution` | Low — leaf module | `_build_rebalance_buy_preview` |
| `calculate_selection_score` | `app.scanner` | Low — leaf module | `_build_rebalance_pair_evaluation` |
| `ExecutionSnapshot` | `app.execution.schema` | Low — dataclass | `_build_rebalance_buy_preview` |
| `SellAnalysisResult` | `app.strategy.sell_decision` | Low — dataclass | Type annotations on multiple functions |
| `format_krw`, `format_qty` | `app.core.formatters` | Low — already extracted | Print helpers |
| `_format_bps` | `app.main` | **Medium** — still in `app.main` | `_build_rebalance_pair_evaluation` (2 calls), print helpers (5 calls) |
| `_build_math_sizing_context` | `app.main` | **Medium** — still in `app.main` (line 630) | `_build_rebalance_buy_preview` only |
| `math.ceil` | stdlib | None | `_calculate_rebalance_sell_sizing` |
| `dataclasses.replace` | stdlib | None | `_calculate_rebalance_sell_sizing`, `_build_rebalance_buy_preview` |

**Critical dependency: `_format_bps` (line 271, 6 lines)**

This is a 6-line pure formatter (`"{value:+.1f} bps"` with sign). Used by 7 call sites in
the rebalance cluster. Same pattern as the `_format_signed_krw`/`_format_signed_pct` pre-step
in Stage 2a. Must be extracted to `app.core.formatters` before rebalance extraction.

**Critical dependency: `_build_math_sizing_context` (line 630, ~50 lines)**

Used by `_build_rebalance_buy_preview` (1 call) and by the BUY execution path in `run_cycle`
(1 call at line 8355). This function computes math-based position sizing overrides from
candidate statistics. It belongs to the BUY flow domain, not rebalance specifically.

Options:
1. **Extract `_build_math_sizing_context` to `app/execution/` first** — creates a clean import
   path. Low risk (pure function, no side effects).
2. **Leave `_build_math_sizing_context` in `app.main`** and import it from the rebalance module.
   This creates a reverse dependency (rebalance → main), which is acceptable as a temporary
   shim but undesirable long-term.
3. **Defer `_build_rebalance_buy_preview`** to Stage 3 (buy flow extraction). This removes
   the `_build_math_sizing_context` dependency entirely from Stage 2c.

**Recommended: Option 3 (defer `_build_rebalance_buy_preview`).** See decision section below.

**Side effects:**

- No state mutations: all 16 functions receive explicit parameters and return values.
  None mutate `state` dict, `api_budget_state`, or any module-level variable.
- No broker/KIS API calls: none of the 16 functions call `buy_market`, `sell_market`,
  `get_orderable`, or any broker function.
- No file I/O: none read or write files.
- No sleep: none call `time.sleep`.
- No Slack/telegram: none send notifications.
- Stdout only: 4 print helpers write to console only.

**State mutations: None.** This is the cleanest cluster in Stage 2. Unlike 2a (pnl_brake:
12 state key mutations, telegram alerts) and 2d (reconciliation: state mutation +
`record_symbol_exit`), the rebalance cluster is **entirely pure computation**.

**Import-cycle risk: None.**

All dependencies flow one direction: `app.execution.rebalance` → leaf modules
(`app.execution`, `app.scanner`, `app.strategy.sell_decision`, `app.core.formatters`).
`app.main` imports `app.execution.rebalance`, not the reverse. No circular dependency.

Exception: if `_build_rebalance_buy_preview` is included and imports
`_build_math_sizing_context` from `app.main`, that creates a cycle risk. This is
avoided by deferring `_build_rebalance_buy_preview` (Option 3 above).

**Call sites in `run_cycle`:**

| Function | Call site lines | Context |
| :--- | :--- | :--- |
| `_position_sizing_requires_rebalance` | 8414, 8698 | BUY path: checks if cash-insufficient triggers rebalance |
| `_build_position_sizing_block_context` | 8401 | BUY path: builds block context dict |
| `_build_buy_analysis_block_context` | 8060 | BUY path: builds analysis block context dict |
| `_calculate_rebalance_sell_sizing` | 8513 | BUY path: computes rebalance sell quantity |
| `_build_rebalance_candidate` | 8439 | BUY path: selects weakest holding for rebalance |
| `_build_quality_rebalance_preview` | 8714 | BUY path: quality rebalance preview |
| `_build_rebalance_buy_preview` | 8575 | BUY path: synthetic BUY re-evaluation after sell |
| `_print_rebalance_preview` | 8649 | BUY path: prints rebalance decision |
| `_print_quality_rebalance_preview` | 8721 | BUY path: prints quality rebalance preview |
| `_print_rebalance_skip` | 7388, 8430, 8461, 8509, 8710, 8748, 8752 | BUY/SELL paths: prints skip reasons |
| `_print_rebalance_buy_preview` | 8692 | BUY path: prints rebalance buy preview |

All call sites are in the BUY decision path of `run_cycle` (lines 8060-8752), except one
`_print_rebalance_skip` call at line 7388 in the SELL path context.

**Test coverage:**

| Test file | Lines | Functions tested |
| :--- | :--- | :--- |
| `test_rebalance_candidate.py` | 279 | `_build_rebalance_candidate` only |

`test_rebalance_candidate.py` covers:
- ✅ `_build_rebalance_candidate` — coverage handling (13 test cases)
  - sell_watch incomplete deferral
  - unevaluated holding cap
  - no eligible holdings
  - candidate exclusion from held set
  - blocked reason codes (5 codes)
  - reason_code → cycle_action mapping contract

Not covered by any direct test:
- ❌ `_position_sizing_requires_rebalance`
- ❌ `_build_position_sizing_block_context`
- ❌ `_build_buy_analysis_block_context`
- ❌ `_calculate_rebalance_sell_sizing`
- ❌ `_build_concentration_metrics`
- ❌ `_estimate_rebalance_concentration_preview`
- ❌ `_serialize_rebalance_holding_option`
- ❌ `_serialize_replacement_candidate_option`
- ❌ `_build_rebalance_pair_evaluation`
- ❌ `_build_quality_rebalance_preview`
- ❌ `_print_rebalance_preview`
- ❌ `_print_quality_rebalance_preview`
- ❌ `_print_rebalance_skip`
- ❌ `_build_rebalance_buy_preview`
- ❌ `_print_rebalance_buy_preview`

**15 of 16 functions have zero direct test coverage.** The existing test file covers only
`_build_rebalance_candidate` through a mock of `_build_rebalance_pair_evaluation`. The
pair evaluation, concentration metrics, sell sizing, and all print/serialization helpers
are exercised only through `run_cycle` integration.

**This is the largest test coverage gap in any Stage 2 target.**

**Compatibility shim needs:**

| Symbol | Shim needed | Reason |
| :--- | :--- | :--- |
| `_build_rebalance_candidate` | Yes | `test_rebalance_candidate.py` imports `from app.main import _build_rebalance_candidate` |
| `_build_rebalance_pair_evaluation` | Yes | `test_rebalance_candidate.py` patches `app.main._build_rebalance_pair_evaluation` |
| All other 14 functions | No | No external test references; `run_cycle` call sites updated via import |

**Extraction risk: Medium.**

Risk factors:
- (+) All functions are pure computation — no state mutations, no I/O, no API calls.
- (+) No import cycle risk (with `_build_rebalance_buy_preview` deferred).
- (+) Dependencies are all leaf modules already imported by `app.main`.
- (+) Stage 2 extraction pattern is proven (3 successful extractions).
- (-) 15 of 16 functions have zero direct test coverage — largest gap in Stage 2.
- (-) `_build_rebalance_pair_evaluation` (112 lines) is complex: calls `calculate_selection_score`,
  `calculate_sell_position_sizing`, `_estimate_rebalance_concentration_preview`, and computes
  `replaceability_score` and `quality_optimizer_score` with multi-factor formulas.
- (-) `_build_rebalance_buy_preview` depends on `_build_math_sizing_context` (still in `app.main`)
  and creates synthetic `ExecutionSnapshot`/portfolio snapshots — execution-adjacent behavior.
- (-) 913 lines is >2x the size of any previous Stage 2 extraction.

**Decision: `_build_rebalance_buy_preview` should NOT move with Stage 2c.**

Rationale:
1. `_build_rebalance_buy_preview` is the only function that depends on `_build_math_sizing_context`
   (a BUY-flow helper at line 630). Extracting it creates either a reverse dependency on
   `app.main` or forces premature extraction of `_build_math_sizing_context`.
2. It creates **synthetic** `ExecutionSnapshot` and portfolio snapshots — this is execution-flow
   behavior, not rebalance evaluation logic. It belongs with Stage 3b (BUY execution flow).
3. Its companion `_print_rebalance_buy_preview` should stay with it for cohesion.
4. Removing these 2 functions (189 lines) reduces Stage 2c to 14 functions / ~724 lines,
   which is more manageable and stays within the rebalance evaluation domain.

**Decision: `_build_position_sizing_block_context` and `_build_buy_analysis_block_context` scope.**

These two functions (62 lines) are **not rebalance-specific**. They build context dicts for
ANY blocked BUY, not just rebalance-triggered blocks. However:
- `_build_position_sizing_block_context` includes `rebalance_skip_reason` in every return dict.
- `_position_sizing_requires_rebalance` reads the same `position_sizing` object.
- All three are called together in the same BUY path block (lines 8401-8414).
- They have no other logical home until Stage 3b (BUY flow extraction).

**Recommended: include them in Stage 2c** as they are small, pure, tightly coupled to the
rebalance gate decision, and have no better extraction target before Stage 3.

**Recommended target module:** `app/execution/rebalance.py`

This matches §3.1 of the split plan. The `app/execution/` package already contains
`calculate_position_sizing` and `calculate_sell_position_sizing` — the rebalance evaluator
is a natural neighbor.

**Recommended slicing (3 slices):**

**Slice 2c-pre: Extract `_format_bps` to `app.core.formatters` (~6 lines)**
- Same pattern as Stage 2a pre-step (`_format_signed_krw`/`_format_signed_pct`).
- ✅ Complete — `08b34a1`
- Add shim in `app.main`.
- Separate tiny commit.

**Slice 2c-0: Characterization tests (~400-500 lines estimated)**
- ✅ Complete — `2c4bdfd`

Priority test targets (functions with complex logic and zero coverage):
1. `_build_concentration_metrics` — weight calculation, edge cases (empty portfolio, zero equity)
2. `_estimate_rebalance_concentration_preview` — before/after delta, concentration penalty
3. `_calculate_rebalance_sell_sizing` — partial sell quantity, needed cash calculation
4. `_build_rebalance_pair_evaluation` — multi-factor scoring (replaceability, quality optimizer)
5. `_position_sizing_requires_rebalance` — gate condition
6. `_build_position_sizing_block_context` — 4 block reason branches
7. `_build_quality_rebalance_preview` — multi-pair ranking

Lower priority (serializers and print helpers — simple, low risk):
8. `_serialize_rebalance_holding_option` — field rounding
9. `_serialize_replacement_candidate_option` — field extraction
10. `_print_rebalance_skip` — trivial (5 lines)

Separate test commit. This is the **most important slice** because 15/16 functions
currently have zero direct test coverage.

**Slice 2c-1: Extract 14 functions to `app/execution/rebalance.py`**
- ✅ Complete across four behavior-preserving extraction commits:
  - 2c-1a pure helpers — `baceedb`
  - 2c-1b context builders — `00837fa`
  - 2c-1c sell sizing, concentration preview, pair evaluation — `22c1251`
  - 2c-1d candidate, quality preview, display helpers — `11d0100`

Functions to extract (public names, drop leading underscore):
1. `position_sizing_requires_rebalance`
2. `build_position_sizing_block_context`
3. `build_buy_analysis_block_context`
4. `calculate_rebalance_sell_sizing`
5. `build_concentration_metrics`
6. `estimate_rebalance_concentration_preview`
7. `serialize_rebalance_holding_option`
8. `serialize_replacement_candidate_option`
9. `build_rebalance_pair_evaluation`
10. `build_rebalance_candidate`
11. `build_quality_rebalance_preview`
12. `print_rebalance_preview`
13. `print_quality_rebalance_preview`
14. `print_rebalance_skip`

Functions intentionally left out of Stage 2c and completed in Stage 3b:
- `_build_rebalance_buy_preview` — depends on `_build_math_sizing_context`
- `_print_rebalance_buy_preview` — display companion to above

**Stage 2c completion note (2026-05-24):** the rebalance evaluation/display
logic is now owned by `app/execution/rebalance.py`, while `app/main.py` keeps
private compatibility wrappers for existing call sites and tests. The rebalance
BUY preview helpers were intentionally deferred from Stage 2c because they
depend on BUY-flow-adjacent synthetic `ExecutionSnapshot` behavior; they moved
with Stage 3b BUY execution work.

Add compatibility shims in `app.main` for:
- `_build_rebalance_candidate` (test import)
- `_build_rebalance_pair_evaluation` (test patch target)

Update `run_cycle` call sites to import from `app.execution.rebalance`.

**Validation commands:**
```
python -m py_compile app/main.py app/execution/rebalance.py
pytest tests/test_rebalance_candidate.py -v
pytest tests/test_rebalance_*.py -v  # includes new characterization tests
pytest tests/ -v --tb=short
```

**Rollback strategy:**
- Revert the extraction commit; compatibility shims ensure no downstream breakage.
- Test file changes (characterization tests) are in a separate commit — independent rollback.
- `_format_bps` pre-step is also a separate commit — independent rollback.

**Relationship to Stage 2e/2f:**

Stage 2e (runtime scan) and Stage 2f (runtime snapshots) are independent extraction targets.
They do not depend on rebalance functions, and rebalance does not depend on them. Stage 2c
can proceed before, after, or in parallel with 2e/2f analysis.

**Relationship to Stage 3 SELL/BUY flow:**

- Stage 2c extracts rebalance **evaluation** logic only — the decision of whether to rebalance
  and which pair to select.
- Stage 3a (SELL flow) and Stage 3b (BUY flow) handle the actual order execution.
- `_build_rebalance_buy_preview` and `_print_rebalance_buy_preview` moved in Stage 3b
  because they create synthetic execution snapshots for the BUY flow.
- The `run_cycle` rebalance block (lines 8414-8752) will still orchestrate the evaluation
  (calling extracted functions) and execution (calling functions still in `app.main`) in the
  same sequence. No execution flow changes.

#### Stage 2e implementation status (2026-05-26)

**Status: Complete — 2e-0/1/2/3 are implemented and pushed.**

| Sub-stage | Status | Commit | Notes |
| :--- | :--- | :--- | :--- |
| 2e-0: characterization tests | ✅ Complete | `e7c8d07` | Runtime scan helper characterization tests added to `tests/test_runtime_scan_helpers.py` |
| 2e-1: pre-gating + normalizers | ✅ Complete | `b29b07c` | 9 helpers extracted; thin wrappers in `app/main.py` |
| 2e-2: layered universe + shallow/deep plans | ✅ Complete | `f704c80` | 5 helpers + `_take_circular_window` extracted; thin wrappers in `app/main.py` |
| 2e-3: runtime guards + display/log helpers | ✅ Complete | `8af57fa` | Runtime guard/display helpers extracted; print helpers use callback-injected engine logging to avoid importing `app.main` |

`app/scanner/runtime_scan.py` now owns the Stage 2e runtime scan helpers
(1,013 lines). `app/main.py` retains private compatibility wrappers for all
moved helpers. No new module imports `app.main`.

#### Stage 2e feasibility analysis (2026-05-24)

**Status: Feasibility analysis complete — see implementation status above.**

Stage 2e is scanner planning/filtering only. It must not move the BUY order
execution tail, change scan universe selection semantics, alter scan cadence, or
change `scan_symbols_max_per_cycle`.

**Recommended target module:** `app/scanner/runtime_scan.py` (new file)

**Functions (18 total, ~1,106 lines):**

| Function | Lines | Current location | Role | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `_count_symbol_buy_entries_today` | ~19 | 2855 | Counts submitted BUY entries for same-symbol limits | Reads `state` only |
| `_is_symbol_in_reentry_cooldown` | ~21 | 2874 | Checks recent BUY order timestamps for cooldown | Reads `state`, time helper |
| `_resolve_sell_exit_reason` | ~17 | 2895 | Normalizes sell/rebalance exit reason | Uses sell decision fields |
| `_build_buy_scan_pre_gating` | ~162 | 2912 | Applies configured exclusions, untradable symbols, daily PnL pause, re-entry guard | Mutates re-entry tracking via `record_reentry_decision` |
| `_print_buy_pre_gating_summary` | ~64 | 3074 | Console summary and engine-event logging for pre-gating | stdout + order-log event |
| `_normalize_pre_gating_payload` | ~72 | 3138 | Normalizes pre-gating payload for snapshots/logs | Pure |
| `_normalize_buy_funnel_reason` | ~57 | 3210 | Buckets free-form rejection reasons | Pure |
| `_resolve_deep_eval_rejection_reason` | ~34 | 3267 | Converts deep-eval rejection into funnel reason | Pure |
| `_resolve_buy_candidate_rejection_reason` | ~51 | 3301 | Chooses per-symbol rejection reason | Pure |
| `_resolve_buy_candidate_selection_outcome` | ~27 | 3352 | Chooses per-symbol selection outcome | Pure |
| `_select_buy_scan_profile` | ~19 | 3814 | Rotates momentum/pullback/recovery profile | Mutates scan profile cursor state |
| `_build_buy_scan_layered_universe` | ~190 | 3833 | Splits raw universe into core/rotating/exploration layers | Mutates layer cursors |
| `_build_buy_scan_shallow_plan` | ~126 | 4023 | Builds shallow-ranked candidates and deep-eval shortlist | Uses shallow scanner and cached snapshots |
| `_build_buy_scan_deep_eval_symbols` | ~22 | 4149 | Prioritizes rescued core symbols in deep-eval order | Pure |
| `_cap_buy_scan_deep_eval_symbols_for_api_budget` | ~45 | 4171 | Caps deep-eval symbols by quote/request budget | Pure, reads budget state |
| `_print_buy_scan_stage_summary` | ~72 | 4216 | Console summary of staged scan | stdout only |
| `_apply_buy_runtime_guards_to_scan_results` | ~53 | 4969 | Applies re-entry guard to candidate scan results | Mutates re-entry tracking via `record_reentry_decision` |
| `_print_buy_runtime_filter_summary` | ~55 | 5022 | Console summary and engine-event logging for runtime guard filters | stdout + order-log event |

**Dependencies:**

| Dependency | Source | Risk | Used by |
| :--- | :--- | :--- | :--- |
| `Counter` | stdlib | Low | pre-gating reason counters |
| `datetime`, `math.ceil`, `dataclasses.replace` | stdlib | Low | API budget cap, shallow plan, guard result replacement |
| `get_korean_now`, `parse_recent_order_time` | `app.core.time_utils` | Low | cooldown, pre-gating, runtime guard timestamps |
| `normalize_exit_reason`, `evaluate_reentry_eligibility` | `app.strategy.reentry` | Medium | exit reason and re-entry guard decisions |
| `record_reentry_decision` | `app.runtime_state` | Medium | writes re-entry diagnostic state |
| `build_shallow_scan_candidates` | `app.scanner.service` | Medium | shallow ranking from cached snapshots |
| `api_budget_remaining_quotes`, `api_budget_remaining_requests` | `app.core.runtime_budget` | Low | deep-eval budget cap |
| `format_qty` | `app.core.formatters` | Low | display helpers if moved |
| `log_order_event` or extracted `_log_engine_event` | `app.core.order_log` / Stage 2f | Medium | pre-gating/runtime-filter print helpers |

**Settings dependencies:**

- Universe/layering: `scan_symbols_max_per_cycle`, `buy_scan_core_fraction`,
  `buy_scan_core_max`, `buy_scan_rotating_fraction`,
  `buy_scan_profile_rotation_enabled`, `buy_scan_deep_eval_limit`,
  `buy_scan_exploration_ratio`, `buy_scan_shallow_top_k`.
- Cache freshness: `live_snapshot_ttl_seconds`.
- Filters/re-entry: `buy_excluded_symbols` plus settings consumed by
  `evaluate_reentry_eligibility`.

**State dependencies and mutations:**

- Reads: `recent_orders`, `buy_entries_by_symbol_today`,
  `buy_untradable_symbols_today`, `recent_market_snapshots_by_symbol`,
  `last_reentry_state_by_symbol`, `last_reentry_reason_by_symbol`,
  `last_exit_reason_by_symbol`.
- Mutates: `buy_scan_profile_cursor`, `buy_scan_last_profile`,
  `buy_scan_core_cursor`, `buy_scan_rotating_cursor`,
  `buy_scan_exploration_cursor`, and re-entry tracking fields through
  `record_reentry_decision`.
- Does **not** mutate runtime state schema shape; extraction must preserve all
  keys and payload field names.

**Live snapshot / API budget dependencies:**

- Stage 2e helpers do not call live snapshot workers or broker/KIS APIs.
  `run_cycle.resolve_buy_universe_symbols()` still resolves live snapshot
  symbols before these helpers run.
- `_build_buy_scan_shallow_plan` consumes cached market snapshots from
  `state["recent_market_snapshots_by_symbol"]` only.
- `_cap_buy_scan_deep_eval_symbols_for_api_budget` reads budget state and
  helper functions, but does not register requests or sleep.

**Side effects and broker/API risk:**

- No broker order APIs, no quote APIs, no sleeps, no scan execution calls.
- State mutation is limited to scan cursors and re-entry diagnostics.
- Print helpers write stdout; `_print_buy_pre_gating_summary` and
  `_print_buy_runtime_filter_summary` also log engine events. To avoid an
  `app.scanner.runtime_scan -> app.main` cycle, either:
  1. extract `_log_engine_event` to `app/reporting/runtime_snapshots.py` first,
     or
  2. keep these two print helpers as `app.main` wrappers until Stage 2f, or
  3. pass an explicit `log_engine_event` callback.

**Import-cycle risk: Medium if print helpers move too early; Low for pure builders.**
The new module must import only leaf modules. It must not import `app.main`.

**Call sites in `run_cycle`:**

- Scan setup: profile/layer/pre-gate/shallow/deep-eval planning at roughly
  lines 6958-7143.
- Runtime guard filtering: candidate result filtering at roughly lines
  6676/7333 and summary printing at 6684/7341.
- Rejection/outcome normalizers are also consumed by Stage 2f builders around
  candidate outcome and cycle stats construction.

**Compatibility shim needs:**

Keep private wrappers in `app.main` for all moved helpers until Stage 5. Direct
tests currently import or patch `app.main` symbols for `_build_buy_scan_pre_gating`,
`_normalize_pre_gating_payload`, `_normalize_buy_funnel_reason`, and
`_cap_buy_scan_deep_eval_symbols_for_api_budget`.

**Test coverage:**

- `tests/test_buy_scan_budget_cap.py`: direct coverage for
  `_cap_buy_scan_deep_eval_symbols_for_api_budget` request/quote caps and reserve
  floor behavior.
- `tests/test_scan_only_diagnostic.py`: direct coverage for
  `_build_buy_scan_pre_gating`, `_normalize_pre_gating_payload`, and
  `_normalize_buy_funnel_reason` for configured/runtime exclusions.
- `tests/test_scanner_shallow_cache.py`: leaf coverage for
  `build_shallow_scan_candidates` cache freshness behavior.
- `tests/test_scanner_parse_error_skip.py`: scanner service parse/rate-limit
  behavior; not direct coverage of runtime scan planning.

**Test gaps:**

- No direct tests for layered universe cursor mutation, profile rotation,
  shallow shortlist quota/core rescue, runtime guard filtering, or pre-gating
  re-entry state mutation.
- No direct stdout/engine-event tests for scan-stage/pre-gating/runtime-filter
  print helpers.

**Recommended slicing:**

1. **Stage 2e-0: characterization tests.** Add tests for profile rotation,
   layered universe cursor behavior, pre-gating daily PnL pause/re-entry blocks,
   shallow shortlist/core rescue, runtime guard filtering, and print/log summary
   behavior. No production code.
2. **Stage 2e-1: pre-gating + normalizers.** Move `_count_symbol_buy_entries_today`,
   `_is_symbol_in_reentry_cooldown`, `_resolve_sell_exit_reason`,
   `_build_buy_scan_pre_gating`, `_normalize_pre_gating_payload`,
   `_normalize_buy_funnel_reason`, `_resolve_deep_eval_rejection_reason`,
   `_resolve_buy_candidate_rejection_reason`, and
   `_resolve_buy_candidate_selection_outcome`.
3. **Stage 2e-2: layered universe + shallow/deep scan plans.** Move
   `_take_circular_window` with this slice even though it is listed in reporting
   candidates; it is a direct dependency of `_build_buy_scan_layered_universe`.
   Move `_select_buy_scan_profile`, `_build_buy_scan_layered_universe`,
   `_build_buy_scan_shallow_plan`, `_build_buy_scan_deep_eval_symbols`, and
   `_cap_buy_scan_deep_eval_symbols_for_api_budget`.
4. **Stage 2e-3: runtime guard filters + display/log helpers.** Move
   `_apply_buy_runtime_guards_to_scan_results` and `_print_buy_scan_stage_summary`.
   Move `_print_buy_pre_gating_summary` and `_print_buy_runtime_filter_summary`
   only after `_log_engine_event` is extracted or via a callback/wrapper pattern.

**Validation commands:**

```
.venv/bin/python -m py_compile app/main.py app/scanner/runtime_scan.py
.venv/bin/python -m pytest tests/test_buy_scan_budget_cap.py tests/test_scan_only_diagnostic.py tests/test_scanner_shallow_cache.py -v
.venv/bin/python -m pytest tests/test_buy_scan_budget_cap.py tests/test_scan_only_diagnostic.py tests/test_scanner_parse_error_skip.py tests/test_scanner_shallow_cache.py -q
.venv/bin/python -m pytest tests/ -x -q --tb=short
```

**Rollback strategy:** keep `app.main` wrappers, commit characterization tests
separately, and revert one extraction slice at a time. No live scan or
`app.main` execution should be used for validation.

#### Stage 2f feasibility analysis (2026-05-24)

**Status: 2f-0 characterization tests complete — implementation pending.**

Stage 2f production extraction has not started. `app/reporting/runtime_snapshots.py`
does not exist yet, and the Stage 2f helpers remain full implementations in
`app/main.py` by design.

| Sub-stage | Status | Commit | Notes |
| :--- | :--- | :--- | :--- |
| 2f-0: runtime snapshot/reporting characterization tests | ✅ Complete | `8198bf3` | Added `tests/test_runtime_snapshot_helpers.py`; full suite passed with 1,314 tests + 22 subtests |
| 2f-1: candidate outcome records + funnel stats extraction | ✅ Complete | `2f5a61b` | Extracted `_build_buy_candidate_outcome_records` and `_build_buy_cycle_funnel_stats` to `app/reporting/runtime_snapshots.py` |
| 2f-2: runtime market snapshots + cycle action/log/display helpers | ✅ Complete | `d03ca21` | Extracted `_serialize_runtime_market_snapshot`, `_update_recent_market_snapshots`, `_log_engine_event`, `_record_cycle_action`, `_print_last_action`, `_print_cycle_conclusion`, `_print_runtime_state_summary` to `app/reporting/runtime_snapshots.py` |

Stage 2f is runtime snapshot/reporting payload construction. It must preserve
runtime state schema, cycle snapshot payloads, candidate outcome rows, cycle
stats rows, PID/process identity fields, and downstream tool compatibility.

**Recommended target module:** `app/reporting/runtime_snapshots.py` (new file)

**Functions (9 production helpers, ~520 lines):**

| Function | Lines | Current location | Role | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `_log_engine_event` | ~25 | 1403 | Writes order-log engine event rows | File I/O via `log_order_event` |
| `_print_cycle_conclusion` | ~16 | 2183 | Console conclusion display | stdout |
| `_print_last_action` | ~5 | 2199 | Console last action display | stdout |
| `_record_cycle_action` | ~22 | 2204 | Updates last decision in runtime state and prints action | Mutates `state` via `set_last_decision` |
| `_print_runtime_state_summary` | ~40 | 2226 | Console runtime-state summary | stdout; reads summary schema |
| `_build_buy_candidate_outcome_records` | ~240 | 2964 | Builds per-symbol candidate outcome rows | Log schema critical |
| `_build_buy_cycle_funnel_stats` | ~143 | 3206 | Builds cycle stats / funnel summary row | Log/schema critical |
| `_serialize_runtime_market_snapshot` | ~13 | 3351 | Serializes market snapshot payload | Runtime state schema |
| `_update_recent_market_snapshots` | ~18 | 3364 | Updates `recent_market_snapshots_by_symbol` in state | Mutates `state` |

**Dependencies:**

| Dependency | Source | Risk | Used by |
| :--- | :--- | :--- | :--- |
| `Any`, `Counter` | stdlib/typing | Low | row construction, reason counters |
| `get_korean_now` | `app.core.time_utils` | Low | snapshot observed time |
| `get_symbol_name` | `app.scanner.symbol_names` | Low | candidate outcome rows |
| `build_core_shadow_fields` | `app.strategy.core_shadow` | Medium | candidate outcome diagnostics |
| `log_order_event` | `app.core.order_log` | Medium | engine events |
| `set_last_decision`, `runtime_state_summary` | `app.runtime_state` | Medium | cycle action and summary |
| `format_qty` | `app.core.formatters` | Low | display helper |
| Stage 2e normalizers | `app.scanner.runtime_scan` | Medium | candidate outcome and funnel reason bucketing |

**State/log payload dependencies:**

- Candidate outcome rows are consumed by `app.reporting.candidate_outcome_logger`,
  backtest reconstruction/parity tools, export tools, postrun diagnostics,
  overnight/core-bucket analysis, validation scripts, dashboards, and research
  reports.
- Cycle stats rows feed `build_cycle_stats_daily_summary()` and daily console
  summary lines.
- Cycle snapshots expose `buy_funnel_summary`, `pre_gate_rejection_counts`,
  staged scan fields, `executed_order_count`, and `sell_triggered_count`.
- Runtime status snapshots and Slack bot rendering read
  `recent_market_snapshots_by_symbol` for position price context.
- PID/process identity behavior is covered in `tests/test_pid_logging.py`; do
  not remove or reorder PID fields in existing log/snapshot builders.

**Side effects and state mutations:**

- `_log_engine_event`: writes an order-log JSONL row with
  `order_type="engine_event"`.
- `_record_cycle_action`: mutates runtime `state` through `set_last_decision`
  and prints last action.
- `_update_recent_market_snapshots`: mutates
  `state["recent_market_snapshots_by_symbol"]`.
- `_print_cycle_conclusion`, `_print_last_action`, and
  `_print_runtime_state_summary`: stdout only.
- `_build_buy_candidate_outcome_records` and `_build_buy_cycle_funnel_stats` are
  pure builders but produce high-value schemas.

**Broker/API risk: Low.** Stage 2f helpers do not call broker/KIS APIs, do not
submit orders, and do not fetch quotes. They prepare or log payloads from data
already available in `run_cycle`.

**Import-cycle risk: Medium.** The target module must not import `app.main`.
Because Stage 2f builders call Stage 2e normalizers, the cleanest order is
Stage 2e-1 before Stage 2f-1. If Stage 2f starts first, duplicate or relocated
normalizers would create churn.

**Call sites in `run_cycle`:**

- `_record_cycle_action` has many call sites across early exits, SELL flow,
  BUY scan blocks, rebalance blocks, and BUY execution outcomes.
- `_log_engine_event` is used for scheduler/scan/budget/rate-limit events across
  the loop.
- Candidate outcome and funnel builders run in the `finally` block around lines
  9123-9189 before persisting cycle snapshots and account-scoped JSONL logs.
- `_update_recent_market_snapshots` runs after snapshot/log persistence and
  before `save_runtime_state`.
- Display helpers are used for conclusions and final runtime summary.

**Compatibility shim needs:**

Keep private wrappers in `app.main` for every moved helper until Stage 5 because
call sites are numerous and some tests import `app.main` directly. Preserve
patchability for `_log_engine_event`, `_record_cycle_action`, and payload
builders during extraction.

**Test coverage:**

- `tests/test_candidate_outcome_logger.py`: validates writer aliases
  (`stage`/`stage_reached`, `outcome`/`selection_outcome`) but not the builder.
- `tests/test_cycle_snapshots.py`: validates cycle snapshot integrity warnings
  and staged scan payload consumers.
- `tests/test_pid_logging.py`: validates PID fields in order logs, runtime
  state, cycle snapshots, and Slack status snapshots.
- `tests/test_main_benchmark_snapshot.py`: adjacent runtime snapshot/cache
  behavior for benchmark lookup.
- Downstream tool tests (`test_engine_backtest_parity.py`,
  `test_export_signal_dataset.py`, proposal/report tests, Slack bot tests)
  exercise candidate outcome and runtime-state schema assumptions indirectly.

**Test gaps:**

- No direct tests for `_build_buy_candidate_outcome_records` stage progression,
  rejection reason precedence, core shadow fields, re-entry fields, or selected
  candidate execution outcomes.
- No direct tests for `_build_buy_cycle_funnel_stats` reason histograms,
  core/non-core breakdowns, buy non-execution reason, or API count fields.
- No direct tests for `_update_recent_market_snapshots` merge semantics or
  `_record_cycle_action` wrapper behavior.

**Recommended slicing:**

1. **Stage 2f-0: characterization tests.** Add direct tests for candidate
   outcome rows, funnel stats, market snapshot serialization/update, cycle
   action mutation, and engine-event log payloads. No production code.
2. **Stage 2f-1: candidate outcome records + funnel stats.** Move
   `_build_buy_candidate_outcome_records` and `_build_buy_cycle_funnel_stats`
   after Stage 2e normalizers are available in `app.scanner.runtime_scan`.
   Keep exact field names and payload shapes.
3. **Stage 2f-2: runtime market snapshots + cycle action/log/display helpers.**
   Move `_serialize_runtime_market_snapshot`, `_update_recent_market_snapshots`,
   `_log_engine_event`, `_record_cycle_action`, `_print_last_action`,
   `_print_cycle_conclusion`, and `_print_runtime_state_summary`. Consider
   moving `_print_cycle_conclusion` and `_print_runtime_state_summary` last in
   this slice because they have broad call-site reach but low behavior risk.
4. **Reclassify `_take_circular_window` to Stage 2e-2.** It is a direct
   dependency of layered universe selection, not a reporting helper.

**Validation commands:**

```
.venv/bin/python -m py_compile app/main.py app/reporting/runtime_snapshots.py
.venv/bin/python -m pytest tests/test_candidate_outcome_logger.py tests/test_cycle_snapshots.py tests/test_pid_logging.py tests/test_main_benchmark_snapshot.py -v
.venv/bin/python -m pytest tests/test_runtime_status_snapshot.py tests/test_slack_bot.py tests/test_engine_backtest_parity.py tests/test_export_signal_dataset.py -q
.venv/bin/python -m pytest tests/ -x -q --tb=short
```

**Rollback strategy:** keep `app.main` wrappers, keep tests in a separate
characterization commit, and revert one extraction slice at a time. Because log
schemas are consumed by dashboards/postrun tooling, any field-shape regression
should be treated as a blocker and reverted rather than repaired in a broad
follow-up.

#### Stage 2 implementation order recommendation (2026-05-20)

**Stage 2 is complete.** All state and domain logic extraction targets have been
implemented as test-first, behavior-preserving extractions: Stage 2a (daily PnL
brake), Stage 2b (regime), Stage 2c (rebalance evaluation/display), Stage 2d
(reconciliation), Stage 2e (runtime scan), Stage 2f (runtime snapshots/reporting),
and Stage 2g (remaining runtime budget helpers). The rebalance BUY preview
helpers were deferred from Stage 2 and completed with Stage 3b by design.

Rationale:
- **Stage 2d first:** Completed via characterization tests (`ccf820b`) and extraction to
  `app/core/reconciliation.py` (`cd2354d`). This proved the Stage 2 extraction pattern at
  the lowest risk point.
- **Stage 2b second:** Completed via expanded regime characterization tests (`c5c7a06`) and
  extraction to `app/risk/regime.py` (`b22c280`). `app/main.py` keeps private compatibility
  shims for existing call sites and tests.
- **Stage 2a third:** Completed via signed formatter pre-step (`26cd401`), daily PnL brake
  characterization reinforcement (`cf9be77`), and extraction to `app/risk/pnl_brake.py`
  (`6086b00`). `app/main.py` keeps private compatibility shims for existing call sites and tests.
- **Stage 2c fourth:** Completed via feasibility analysis (`9937ab0`), formatter
  pre-step (`08b34a1`), characterization tests (`2c4bdfd`), and four
  extraction slices ending with rebalance candidate/display helpers (`11d0100`).
  `app/main.py` keeps private compatibility wrappers. Rebalance BUY preview
  was completed later with Stage 3b.
- **Stage 2e fifth:** Completed via runtime scan characterization tests
  (`e7c8d07`) and three extraction slices through runtime guard/display helpers
  (`8af57fa`). `app/main.py` keeps private compatibility wrappers. The print
  helpers use callback-injected engine logging rather than importing `app.main`.

Implementation sequence:
1. ✅ Write reconciliation characterization tests (test-only commit `ccf820b`)
2. ✅ Extract reconciliation helpers to `app/core/reconciliation.py` (code commit `cd2354d`)
3. ✅ Write regime state characterization tests (test-only commit `c5c7a06`)
4. ✅ Extract regime helpers to `app/risk/regime.py` (code commit `b22c280`)
5. ✅ Extract signed formatter helpers to `app/core/formatters.py` (pre-step commit `26cd401`)
6. ✅ Strengthen daily PnL brake characterization tests (test-only commit `cf9be77`)
7. ✅ Extract daily PnL brake helpers to `app/risk/pnl_brake.py` (code commit `6086b00`)
8. ✅ Stage 2c rebalance feasibility analysis (docs commit `9937ab0`)
9. ✅ Stage 2c-pre — extract `_format_bps` to `app.core.formatters` (`08b34a1`)
10. ✅ Stage 2c-0 — characterization tests for rebalance helpers (`2c4bdfd`)
11. ✅ Stage 2c-1a — extract pure rebalance helpers (`baceedb`)
12. ✅ Stage 2c-1b — extract rebalance context builders (`00837fa`)
13. ✅ Stage 2c-1c — extract sell sizing, concentration preview, pair evaluation (`22c1251`)
14. ✅ Stage 2c-1d — extract candidate, quality preview, display helpers (`11d0100`)
15. ✅ Completed in Stage 3b: `_build_rebalance_buy_preview` + `_print_rebalance_buy_preview`
16. ✅ Stage 2e/2f runtime scan/snapshot feasibility analysis (docs-only)
17. ✅ Stage 2e-0 — runtime scan characterization tests (`e7c8d07`)
18. ✅ Stage 2e-1 — extract pre-gating + normalizers (`b29b07c`)
19. ✅ Stage 2e-2 — extract scan profile/layered universe/shallow/deep plans (`f704c80`)
20. ✅ Stage 2e-3 — extract runtime guard/display helpers (`8af57fa`)
21. ✅ Stage 2f-0 — runtime snapshot/reporting characterization tests (`8198bf3`)
22. ✅ Stage 2f-1 — runtime snapshot funnel helpers extraction (`2f5a61b`)
23. ✅ Stage 2f-2 — runtime snapshot action helpers extraction (`d03ca21`)
24. ✅ Stage 2g — remaining runtime budget helpers extraction (`a035781`)

### Stage 3 — Execution flow extraction (highest risk)

Status: ✅ Complete. SELL and BUY execution flows were moved only after focused
guard/characterization tests existed and passed.

Goal: move SELL and BUY execution flows only after guard tests exist and pass.

**Step 3a**: `app/execution/sell_flow.py` (~524 lines, cluster Q)
- ✅ Complete — characterization tests (`43e8623`) and extraction (`9ebc5f0`)
- `_run_sell_order_flow` — the most sensitive function
- Contains broker API calls (`sell_market`), state mutation, order logging, Slack
- Must preserve: cooldown key behavior, order-time market recheck,
  `failure_category="no_position_on_sell"`, log/state/Slack sequencing,
  API budget reservation
- Extract last among domain logic because all other helpers must be stable first

**Step 3b**: BUY execution tail → `app/execution/buy_flow.py` (~600 lines)
- ✅ Complete — characterization tests (`88237da`) and extraction (`98ea0f9`)
- Selected-candidate execution block from `run_cycle` phases 13-16
- Contains: orderable cash lookup, position sizing, risk guard checks,
  `buy_market` call, success/failure handling, reentry decisions
- Must preserve: reentry decision logging, orderable lookup/preview choice,
  order-time market recheck, submitted log/state/Slack sequence,
  `buy_untradable_detected`, budget reservation

- Expected risk: **high**.
- Validation:
  ```
  pytest tests/test_order_guard.py tests/test_no_position_sell_detection.py tests/test_main_slack_order_hooks.py tests/test_buy_orderable_market_session_order.py tests/test_buy_order_submit_budget.py tests/test_pid_logging.py -v
  ```
- Rollback: leave extracted functions unused first, then switch one call
  site at a time. If behavior diverges, restore the `app.main` call site.
- Commit boundary: SELL extraction and BUY extraction must be separate commits.
  This boundary was preserved.

### Stage 4 — Runtime loop extraction

Status: Complete under the safe completion boundary. Stage 4-0 feasibility
analysis, Stage 4-1/4-2/4-3 helper extraction, Stage 4-4a main-loop
characterization, and Stage 4-4b repeated session-loop extraction are complete.
`run_cycle()` internals remain orchestration and are explicitly deferred.

Goal: extract the scheduler/session loop after execution flows and pure helpers
are stable, while retaining high-risk `run_cycle()` orchestration in
`app/main.py`.

- Implemented files: `app/runtime/session_loop.py`, `app/main.py`, and
  characterization tests. `app/runtime/session_context.py` and
  `app/runtime/heartbeat.py` were not needed for the safe completion boundary.
- First commit should be analysis only: map `run_cycle` sections, closure
  dependencies, mutable locals, `finally` behavior, and validation gates.
- Do not bundle runtime loop work with execution flow. Stage 3 is complete and
  should stay closed.
- Keep `main()` responsible for `get_settings()`, `acquire_app_main_lock()`,
  startup validation, and invoking the extracted loop.
- Preserve `run_once` behavior, base tick calculation, last SELL/BUY due times,
  effective runtime-rate settings replacement, exception handling, bottleneck
  recording, and sleep timing.
- Expected risk: **highest**.
- Validation:
  ```
  pytest tests/test_session_lock.py tests/test_runtime_rate_control.py tests/test_main_note_call_arity.py -v
  ```
- Rollback: revert the repeated-loop extraction commit if validation diverges;
  `main()` remains a narrow owner of locking, validation, `run_once`, and the
  call into the extracted loop.
- Commit boundary: one loop extraction commit, no unrelated cleanup.
- Validation is test-only. Do not run `app.main`, `run_session.sh`, live scans,
  broker/KIS APIs, or order APIs.

#### Stage 4-0 feasibility analysis (2026-05-29)

Current `app/main.py` shape on the Stage 4 analysis baseline:

- Total line count: 6,486 lines.
- Remaining top-level definitions: 132 top-level functions, 0 classes, plus 3
  `run_cycle()` inner functions.
- `run_cycle()` line range: 3317-6244, 2,928 lines.
- `main()` line range: 6247-6482, 236 lines.
- Remaining large helper clusters:
  - console/status rendering: `_print_applied_settings`,
    `_print_engine_schedule_state`, `_print_cycle_timing`,
    `_print_buy_scan_metrics`, `_print_sell_decision`,
    `_print_account_balance_interpretation`, `_print_today_bought_tracking`.
  - runtime scheduler/rate control: `_is_due`,
    `_build_runtime_rate_control`, `_build_scheduler_tick_decision`,
    `_parse_hhmm_window`, `_within_hhmm_window`.
  - account/session artifacts: `_build_cycle_id`, account scope setup in
    `run_cycle()`, `start_cycle()`, Slack runtime status snapshots,
    cycle snapshot persistence, runtime state persistence.
  - diagnostic/test paths: `_run_sell_guard_selftest`,
    `_run_sell_test_cycle`, scan-only diagnostic helpers.

Stage 4 target areas and boundaries:

| Area | Current owner | Stage 4 recommendation | Risk |
| :--- | :--- | :--- | :--- |
| `main()` app lock/startup validation | `app.main.main()` lines 6247-6291 | Keep in `main()` until the session loop is already extracted and tested. The per-account `acquire_app_main_lock()` handle must remain alive for process lifetime. | High |
| `main()` run-once path | `app.main.main()` lines 6292-6347 | Extract only with the loop/session context, preserving `RUN_ONCE_FULL_CYCLE`, full SELL/BUY due flags, exception downgrading, and `_record_main_loop_exception_if_needed()`. | High |
| `main()` infinite loop | `app.main.main()` lines 6349-6482 | Best first code extraction target after docs: move to `app/runtime/session_loop.py` behind a thin `main()` shim. Preserve `base_tick_seconds`, last SELL/BUY due timestamps, skip-cycle sleeps, and completed-at timestamp updates. | High |
| scheduler helpers | `_is_due`, `_build_scheduler_tick_decision` | Extractable in Stage 4b with compatibility wrappers in `app.main`; already covered by `tests/test_runtime_rate_control.py`. | Medium |
| runtime rate control | `_parse_hhmm_window`, `_within_hhmm_window`, `_build_runtime_rate_control` | Extractable in Stage 4b/4c as a runtime pressure/rate-control module, but it mutates `api_budget_state` (`consecutive_backoff_cycles`, degraded mode keys). | Medium |
| engine schedule printing | `_print_engine_schedule_state` | Defer or move with session loop as a print-side-effect helper. Do not mix with pure scheduler extraction. | Medium |
| `_log_engine_event` | wrapper around `app.runtime.snapshots.log_engine_event` | Already a wrapper. Keep `app.main` compatibility while tests patch/import it; later call the runtime snapshot module directly. | Low |
| execution request wait | `_wait_for_execution_request_budget` | Defer. It calls `note(...)` and `time.sleep()`, so extraction requires explicit logger/counter injection. | High |
| rate-limit note | `_api_budget_note_rate_limit` | Defer until bottleneck ownership is settled. It mutates API budget state, calls `note_rate_limit()`, and records `KIS_RATE_LIMIT_BACKOFF` through `_record_bottleneck()`. | Medium |
| `run_cycle()` orchestration | `app.main.run_cycle()` | Do not extract wholesale in Stage 4b/4c. First reduce closure/finally dependencies or introduce a documented context object in a later slice. | Highest |

Closure blockers:

- `note(level, message)` at lines 3551-3557 captures
  `cycle_warning_count` and `cycle_error_count` through `nonlocal` and is used
  throughout normal flow, exception handling, and the final persistence block.
  `tests/test_main_note_call_arity.py` guards every `note(...)` call in
  `app/main.py`, so changing this surface needs an explicit validation step.
- `resolve_buy_universe_symbols()` at lines 3559-3570 captures `settings` and
  combines live snapshot symbols, snapshot status, and worker health. It is
  called from multiple BUY scan branches.
- `log_buy_universe_source()` at lines 3572-3619 captures `settings` and
  calls `note()`. It turns live snapshot fallback/worker-health state into
  operator-facing status lines. Extract only after `note()` is parameterized.

`finally` block dependency set:

- Final `run_cycle()` persistence/reporting starts at line 5795 and runs through
  line 6244. It depends on mutable locals initialized before the main `try` so
  early returns can still persist a complete cycle shape.
- Critical inputs include `state`, `api_budget_state`, `scheduler_state`,
  `timing_summary`, `api_usage_summary`, `cycle_id`, `cycle_started_at`,
  `cycle_environment`, `cycle_error`, `session_status`, `portfolio_snapshot`,
  `sell_analysis_results`, `observed_market_snapshots`, raw/filtered BUY scan
  results, selection details, selected BUY/SELL candidates, execution snapshots,
  sizing payloads, risk guard payloads, rebalance previews, daily PnL brake and
  regime state, rate-limit/backoff flags, SELL watch cursor/partial state, BUY
  scan counters, throttle metrics, and warning/error counters.
- The block updates runtime state schema keys, API budget recovery state,
  cycle snapshots, candidate outcomes, cycle stats, performance reports, Slack
  runtime status snapshots, daily summaries, and console metrics. Moving it
  before a `CycleContext`/`CycleArtifacts` object exists is too risky.

Extractable now vs. deferred:

- Extractable in Stage 4b with wrappers: `_is_due`,
  `_build_scheduler_tick_decision`, `_parse_hhmm_window`,
  `_within_hhmm_window`, and possibly `_build_runtime_rate_control`.
- Extractable only with the session-loop move: `main()` loop construction,
  `base_tick_seconds`, `last_sell_check_at`, `last_buy_scan_at`, skip-cycle
  sleep handling, and `replace(settings, ...)` effective runtime settings.
- Keep as wrappers for now: `_log_engine_event` and
  `_print_engine_schedule_state`.
- Deferred until closure/context work: `_wait_for_execution_request_budget`,
  `_api_budget_note_rate_limit`, `note()`, `resolve_buy_universe_symbols()`,
  `log_buy_universe_source()`, and the final `run_cycle()` persistence block.
- Recommended deferral: do not extract `run_cycle()` itself during the first
  Stage 4 implementation pass. Treat it as orchestration until the final block
  can accept a typed context/artifacts object or a narrower dependency bundle.

Safety risks to preserve explicitly:

- Duplicate `app.main` process lock: `acquire_app_main_lock()` must run before
  shared file access and the lock handle must stay alive.
- `run_once` behavior: run one full cycle with both due flags true, then return.
- Scheduler timing: preserve `base_tick_seconds`, SELL/BUY due calculations,
  skip-cycle sleeps, and timestamp updates only after attempted cycle
  completion.
- Backoff behavior: transient backoff skips the whole cycle; KIS rate-limit
  backoff can preserve due SELL watch while deferring BUY scan.
- Exception handling: `run_once` and loop exceptions are downgraded to
  `DEGRADED`, recorded through `_record_main_loop_exception_if_needed()`, and
  the process continues/returns as today.
- Bottleneck recording: `_api_budget_note_rate_limit()` and main-loop
  exception recording must continue to route through the existing bottleneck
  aggregator behavior.
- Slack runtime status snapshots: preserve start, session, and final writes.
- Cycle snapshot/finally persistence: do not change runtime state schema keys,
  candidate outcome logging, cycle stats logging, performance persistence, or
  final summary printing in the first loop extraction.
- Order execution path: Stage 3 SELL/BUY extraction is closed. Stage 4 must not
  touch `app/execution/sell_flow.py`, `app/execution/buy_flow.py`, or order API
  call paths.

Recommended Stage 4 slicing:

1. **4a docs/analysis:** this feasibility analysis only. No production code,
   tests, runtime modules, live scans, `app.main`, or `run_session.sh`.
2. **4b scheduler helper extraction:** create `app/runtime/session_loop.py` or
   a narrower scheduler module for `_is_due` and
   `_build_scheduler_tick_decision`; keep `app.main` wrappers for test patch
   compatibility. Optional in the same slice only if still small:
   `_parse_hhmm_window` and `_within_hhmm_window`.
3. **4c runtime loop/session extraction:** move only the repeated loop into a
   session-loop entry that accepts `settings`, `run_cycle`, time/sleep
   functions, exception hooks, scheduler helpers, and API budget builders as
   explicit callbacks. Keep `main()` responsible for `get_settings()`,
   app-main lock acquisition, startup validation, the `run_once` path, and
   calling the extracted repeated loop.
4. **4d run-cycle dependency reduction:** defer beyond the Stage 4 completion
   boundary. A future redesign may introduce a small
   `CycleRuntimeCounters`/`CycleArtifacts` context for warning/error counts and
   final persistence inputs. Do not extract full `run_cycle()` unless this
   context proves low-risk.

Stage 4 hard stops:

- Stop if an extraction requires touching SELL/BUY order execution logic.
- Stop if a loop change alters `run_once`, duplicate process lock behavior,
  scheduler due timestamps, or backoff skip semantics.
- Stop if the final `run_cycle()` block needs runtime state schema changes.
- Stop if tests require running `app.main`, `run_session.sh`, live scans,
  broker/KIS APIs, or order APIs.

Stage 4 validation commands:

```bash
PYTHONPATH=. pytest tests/test_session_lock.py tests/test_session_lock_runtime.py -v
PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_main_note_call_arity.py -v
PYTHONPATH=. pytest tests/test_cycle_snapshots.py tests/test_pid_logging.py -v
PYTHONPATH=. pytest tests/test_buy_order_flow_characterization.py tests/test_sell_order_flow_characterization.py -v
PYTHONPATH=. python -m py_compile app/main.py app/runtime/session_loop.py
```

Do not run `app.main`, `run_session.sh`, live scan config, broker/KIS data
fetches, or order APIs during Stage 4 validation unless the operator explicitly
requests it.

#### Stage 4 progress sync (2026-06-02)

Completed and pushed:

| Slice | Commit | Result |
| :--- | :--- | :--- |
| 4-0 feasibility analysis | `9abee45` | Documented scheduler boundaries, closure blockers, mutable locals, `finally` dependencies, validation gates, and stop conditions. |
| 4-1 scheduler timing helpers | `1743173` | Moved `is_due`, `build_scheduler_tick_decision`, `parse_hhmm_window`, and `within_hhmm_window` to `app/runtime/session_loop.py`; retained `app.main` wrappers. |
| 4-2 runtime loop status helper | `ae4f303` | Moved `print_engine_schedule_state` to `app/runtime/session_loop.py`; retained the `app.main` wrapper and existing call sites. |
| 4-3 runtime rate control helper | `b0025f2`, `c1947b4` | Added characterization tests and moved `build_runtime_rate_control` to `app/runtime/session_loop.py`; preserved `api_budget_state` mutations and retained the `app.main` wrapper. |
| 4-4a main-loop characterization | `3c5b26a` | Added deterministic tests for `run_once`, base tick calculation, transient/KIS backoff behavior, due timestamp updates, exception handling, and app-main lock ownership. |
| 4-4b repeated session loop | `252aa34` | Moved only the repeated loop to `run_session_loop()` in `app/runtime/session_loop.py`; retained `main()` ownership of app-main locking, startup validation, and `run_once`; passed `run_cycle` and loop dependencies as explicit callbacks. |

Current post-4-4b shape:

- `app/main.py`: 6,164 lines, 133 top-level functions.
- `run_cycle()`: lines 3128-6055, 2,928 lines.
- `main()`: lines 6058-6160, 103 lines.
- `app/runtime/session_loop.py`: 455 lines.

Stage 4 is complete under the safe extraction boundary. Completed scope:

- Scheduler timing helpers and HHMM window helpers.
- Scheduler status output helper.
- Runtime rate-control helper with preserved `api_budget_state` mutations.
- Main-loop characterization tests.
- Repeated session-loop extraction behind `app.main._run_session_loop()`.
- `main()` retention of app-main lock ownership, startup validation, and the
  `run_once` path.

Explicitly deferred beyond the Stage 4 completion boundary:

- `_api_budget_note_rate_limit`: bottleneck recording ownership and
  `_record_bottleneck()` dependency.
- `_wait_for_execution_request_budget`: `note()` closure, sleep/callback
  boundary, and order-execution pacing.
- `run_cycle()` internals: broad mutable-local, `finally`, persistence, and
  runtime-schema coupling. Keep `run_cycle()` as the orchestrator.
- The final persistence/reporting block inside `run_cycle()`: runtime snapshot,
  cycle snapshot, Slack status, daily summary, and broad artifact dependencies.
- `note()`, `resolve_buy_universe_symbols()`, and
  `log_buy_universe_source()`: closure-heavy helpers that remain inside
  `run_cycle()`.
- Any context-object redesign for these items belongs to a future explicitly
  scoped effort, not Stage 5 cleanup.

### Stage 5 — Cleanup

Status: ✅ Complete (2026-06-02) under the safe boundary. Pushed baseline:
`eddfc7b`.

Goal: remove compatibility shims only after downstream imports/tests have moved.

- Files touched: `app/main.py`, extracted modules, tests, docs.
- Cleanup items: remove dead inline helpers, reduce imports, update docs, retire
  temporary compatibility shims, document new ownership boundaries.
- Optional: extract ~24 `_print_*` console display functions (~800 lines) into
  a display module. Low priority — they produce no return values. Not done;
  deferred as an optional future cleanup.
- Expected risk: medium because import and patch paths change.
- Validation: `pytest tests/ -v --tb=short`
- Rollback: restore compatibility shims for any externally referenced symbol.
- Commit boundary: cleanup/docs commit separate from extraction commits.

#### Stage 5 completion (2026-06-02)

| Slice | Commit | Result |
| :--- | :--- | :--- |
| 5-1 dead shim removal | `3894aea` | Removed dead `app.main` compatibility shims with no remaining callers. |
| 5-2 test patch migration | `af91312` | Migrated split-helper test patch targets off `app.main` onto the owning modules. |
| 5-3 migrated wrapper removal | `2b3fd63` | Removed `app.main` compatibility wrappers whose tests had migrated. |
| unused import cleanup | `eddfc7b` | Removed the unused `_looks_like_buy_untradable_response` import alias from `app/main.py`. |

`app/main.py` final role: orchestration entrypoint; owner of the app-main fcntl
lock (`acquire_app_main_lock()`) and startup validation; `run_cycle()`
orchestrator; holder of the deferred closure-heavy runtime pieces that remain
inside `run_cycle()`. Final shape: 5,965 lines, 119 top-level functions.

Remaining intentional `app.main` shims/wrappers (kept on purpose):

- `_run_session_loop` → `app/runtime/session_loop.py`
- `_run_sell_order_flow` → `app/execution/sell_flow.py`
- `_run_buy_order_flow` / BUY orchestration callback boundary →
  `app/execution/buy_flow.py`
- `_log_engine_event` → `app/reporting/runtime_snapshots.py`
- scheduler / runtime-budget callbacks consumed by the session loop
- Slack / bottleneck hooks

Deferred / not moved (still owned inside `app/main.py`):

- `_api_budget_note_rate_limit` — bottleneck recording ownership; the
  `_record_bottleneck()` dependency is not decoupled from `app.main`.
- `_wait_for_execution_request_budget` — order-execution pacing; couples the
  `note()` closure and sleep/callback behavior.
- `run_cycle()` internals and its `finally` persistence/reporting block — broad
  mutable-local, persistence, and runtime-schema coupling.
- `note()`, `resolve_buy_universe_symbols()`, `log_buy_universe_source()` —
  closure-heavy inner helpers capturing nonlocal `run_cycle()` state.

Any context-object redesign for these deferred items is a future explicitly
scoped effort, not part of the completed Stage 5 cleanup.

Final test status: `1367 passed, 22 subtests passed` (full suite).

## 5. Explicit non-goals

- Do not change strategy logic.
- Do not change expected return buffer behavior.
- Do not change BUY daily limits.
- Do not change SELL cooldown logic.
- Do not change live universe or scan cadence.
- Do not change `scan_symbols_max_per_cycle`.
- Do not alter account signature or fcntl lock behavior.
- Do not change Slack bot behavior in this split.
- Do not connect ETF BUY path.
- Do not add 실전투자 code.
- Do not change `run_session.sh` or start `app.main` as part of this split.

## 6. Validation plan

Safe validation commands for future implementation work:

```bash
PYTHONPATH=. pytest tests/test_session_lock.py tests/test_session_lock_runtime.py -v
PYTHONPATH=. pytest tests/test_order_guard.py tests/test_buy_orderable_market_session_order.py tests/test_buy_order_submit_budget.py -v
PYTHONPATH=. pytest tests/test_pid_logging.py tests/test_cycle_snapshots.py tests/test_runtime_status_snapshot.py -v
PYTHONPATH=. pytest tests/test_main_slack_order_hooks.py tests/test_slack_bot.py tests/test_slack_notifier.py -v
PYTHONPATH=. pytest tests/test_no_position_sell_detection.py tests/test_main_rate_limit_body_sources.py -v
PYTHONPATH=. pytest tests/test_buy_scan_budget_cap.py tests/test_scanner_parse_error_skip.py tests/test_scanner_shallow_cache.py tests/test_scan_only_diagnostic.py -v
PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_runtime_budget_helpers.py tests/test_sell_watch_budget.py tests/test_live_snapshot_runtime_pressure.py -v
PYTHONPATH=. python -m py_compile app/main.py
```

Targeted coverage map:

- Session lock: `tests/test_session_lock.py`, `tests/test_session_lock_runtime.py`
- Order guard and SELL cooldown: `tests/test_order_guard.py`
- PID logging: `tests/test_pid_logging.py`, `tests/test_cycle_snapshots.py`,
  `tests/test_runtime_status_snapshot.py`
- Slack order/runtime rendering: `tests/test_main_slack_order_hooks.py`,
  `tests/test_slack_bot.py`, `tests/test_slack_notifier.py`
- No-position classification: `tests/test_no_position_sell_detection.py`
- Scanner candidate logic if touched: `tests/test_buy_scan_budget_cap.py`,
  `tests/test_scanner_parse_error_skip.py`, `tests/test_scanner_shallow_cache.py`,
  `tests/test_scan_only_diagnostic.py`

Do not validate by running `app.main`, `run_session.sh`, broker/KIS APIs, live
scans, or broad operational scripts unless explicitly requested by the operator.

## 7. Recommended first implementation slice

**Stage 1a: extract `app/core/error_classification.py`**

- 9 pure classifier functions, ~156 lines total
- Zero side effects, zero state mutation
- Dependencies: only `ApiHttpError` from `app.auth.token`
- No broker API calls, no file I/O, no Slack, no sleep
- Independently testable with simple unit tests
- Establishes the extraction pattern for all subsequent stages

Functions to move:
- `_looks_like_rate_limit_error(exc)` (9 lines)
- `_looks_like_buy_untradable_response(response)` (17 lines)
- `_looks_like_no_position_sell_response(response)` (12 lines)
- `_looks_like_transient_api_error(exc)` (7 lines)
- `_transient_api_source_from_exception(exc)` (21 lines)
- `_rate_limit_source_from_response_body(body)` (10 lines)
- `_rate_limit_source_from_exception(exc)` (17 lines)
- `_should_downgrade_empty_buy_scan_to_backoff(...)` (22 lines)
- `_build_buy_scan_rate_limit_degraded_reason(...)` (25 lines)

After extraction, `app/main.py` changes to:
```python
from app.core.error_classification import (
    looks_like_rate_limit_error,
    looks_like_buy_untradable_response,
    looks_like_no_position_sell_response,
    looks_like_transient_api_error,
    # ...
)
```

Note: leading underscore dropped when functions become part of a public module API.
Compatibility shims in `app.main` preserve existing test patches.

Validation:
```
python -m py_compile app/main.py app/core/error_classification.py
pytest tests/test_main_rate_limit_body_sources.py tests/test_no_position_sell_detection.py tests/test_sell_watch_rate_limit_body.py -v
pytest tests/ -v --tb=short
```

Do not start with `_run_sell_order_flow`, the BUY order submit block, or the
session loop.

## 8. Open questions / risks

### 8.1 Import cycles

- New modules in `app/execution/`, `app/risk/`, `app/scanner/` will import from
  `app/strategy/schema.py`, `app/execution/schema.py`, `app/portfolio/schema.py`.
- Risk of circular imports is low because these are leaf schema modules.
- `SellAnalysisResult` from `app/strategy/sell_decision.py` is used by formatting,
  display, sell flow, and rebalance. If this creates a cycle, use
  `from __future__ import annotations` to defer type resolution.
- Mitigation: extract downward only — new modules never import from `app.main`.

### 8.2 Singleton state

- `_get_slack_notifier()` and `_get_bottleneck_aggregator()` use module-level
  closure variables.
- When extracted, the singleton instance lives in the new module.
- Risk: if both `main.py` and the new module instantiate separately.
  Mitigation: route all access through the extracted module.

### 8.3 `run_cycle` inner functions

- 3 inner functions (`note`, `resolve_buy_universe_symbols`,
  `log_buy_universe_source`) capture nonlocal variables (`cycle_warning_count`,
  `cycle_error_count`, `settings`).
- Cannot be extracted without changing signature (explicit parameters instead of
  closure captures).
- Recommendation: leave inside `run_cycle` for now. Extract in redesign phase.

### 8.4 Mutable dict state

- Many functions receive `state: dict`, `api_budget_state: dict` and mutate
  them in place. Extraction preserves this pattern.
- A future phase could introduce typed state objects, but that is out of scope.
- API budget state is shared between ticks; moving helper code without
  documenting keys can break backoff recovery or BUY reserve behavior.

### 8.5 Test patch surface

- 35 test files reference `app.main` or `from app.main import`.
- Compatibility shims should remain until tests move to new import paths.
- After extraction, re-export functions from `app/main.py` during a transition
  period, then update test patches in a cleanup commit.

### 8.6 `_run_sell_order_flow` complexity

- At 524 lines with 3 exception handling paths (rt_cd failure, ApiHttpError,
  generic Exception), each with its own state mutation and Slack notification.
- Extraction preserves all 3 paths. No simplification during extraction.
- A dedicated test should be added before the extraction commit.

### 8.7 `run_cycle` `finally` block coupling

- The `finally` block (lines ~10366-10817) depends on ~100 variables initialized
  across many early returns.
- A `CycleOutcome`/`CycleArtifacts` object would reduce risk, but that would be
  a redesign and should come after pure extraction.

### 8.8 Scanner/runtime scan coupling

- Scanner logic has hidden coupling to live snapshot status,
  `scan_symbols_max_per_cycle`, shallow/deep caps, and cycle stats payloads.
- Must verify all coupling points before extracting to `app/scanner/runtime_scan.py`.

### 8.9 Rebalance cross-cutting

- Rebalance mixes BUY candidate quality with SELL execution and should not be
  extracted until both BUY scan and SELL flow boundaries are stable.

## 9. Estimated impact

| Metric | Before | After (all stages) |
| :--- | :--- | :--- |
| `app/main.py` lines | 11,057 | ~3,500-4,000 |
| `app/main.py` functions | 134 | ~5 (`run_cycle`, `main`, 3 inner) + `_print_*` if kept |
| New/expanded modules | 0 | ~12-15 |
| Behavior change | - | None |
| Test changes | - | Import path updates + compatibility shims |

Realized end state (2026-06-02, `eddfc7b`): `app/main.py` is 5,965 lines and 119
top-level functions, not the ~3,500-4,000 / ~5 estimate, because `run_cycle()`
internals, its `finally` persistence block, and the closure-heavy inner helpers
were intentionally deferred (see "Stage 5 — Cleanup" above). 12 modules were
extracted and behavior is unchanged.

## 10. Status

| Item | Status |
| :--- | :--- |
| Split plan document created | 2026-05-17 |
| Detailed function inventory | 2026-05-17 |
| Stage 0 — Characterization | Pending |
| Stage 1a — Error classification helpers | 2026-05-17 |
| Stage 1b — Order notification helpers | 2026-05-17 |
| Stage 1c-1 — Runtime budget query helpers | 2026-05-18 |
| Stage 1c-2 — Runtime budget request/prune helpers | 2026-05-18 |
| Stage 1c-3 — Runtime budget split boundary analysis | 2026-05-20 |
| Stage 1c-4a — Runtime budget summary helpers | `9a7cafe` 2026-05-20 |
| Stage 1c-4b — Runtime budget recovery update helpers | `6abdd56` 2026-05-20 |
| Stage 1c — Low-risk pure helper extraction | ✅ Complete (1c-1 through 1c-4b) |
| Stage 1 — Remaining / deferred runtime helpers | Deferred — see §4 Stage 1c deferred list |
| Stage 2a-pre — Signed formatter extraction | ✅ Complete — `26cd401` |
| Stage 2a-0 — Daily PnL brake characterization reinforcement | ✅ Complete — `cf9be77` |
| Stage 2a-1 — Daily PnL brake helper extraction | ✅ Complete — `6086b00` |
| Stage 2a — Daily PnL brake implementation | ✅ Complete |
| Stage 2b-0 — Regime state characterization tests | ✅ Complete — `c5c7a06` |
| Stage 2b-1 — Regime state helper extraction | ✅ Complete — `b22c280` |
| Stage 2c — Rebalance feasibility analysis | ✅ Complete — `9937ab0` |
| Stage 2c-pre — Bps formatter extraction | ✅ Complete — `08b34a1` |
| Stage 2c-0 — Rebalance characterization tests | ✅ Complete — `2c4bdfd` |
| Stage 2c-1a — Pure rebalance helper extraction | ✅ Complete — `baceedb` |
| Stage 2c-1b — Rebalance context builder extraction | ✅ Complete — `00837fa` |
| Stage 2c-1c — Sell sizing, concentration preview, pair evaluation extraction | ✅ Complete — `22c1251` |
| Stage 2c-1d — Candidate, quality preview, display helper extraction | ✅ Complete — `11d0100` |
| Stage 2c — Rebalance evaluation/display extraction | ✅ Complete (14 helpers in `app/execution/rebalance.py`; BUY preview completed later in Stage 3b) |
| Stage 2d-0 — Reconciliation characterization tests | ✅ Complete — `ccf820b` |
| Stage 2d-1 — Reconciliation helper extraction | ✅ Complete — `cd2354d` |
| Stage 2e / 2f — Runtime scan and snapshot feasibility analysis | ✅ Complete |
| Stage 2e-0 — Runtime scan characterization tests | ✅ Complete — `e7c8d07` |
| Stage 2e-1 — Runtime scan pre-gating + normalizers extraction | ✅ Complete — `b29b07c` |
| Stage 2e-2 — Runtime scan planning helper extraction | ✅ Complete — `f704c80` |
| Stage 2e-3 — Runtime scan guard/display helper extraction | ✅ Complete — `8af57fa` |
| Stage 2e — Runtime scan extraction | ✅ Complete |
| Stage 2f-0 — Runtime snapshot/reporting characterization tests | ✅ Complete — `8198bf3` |
| Stage 2f-1 — Runtime snapshot funnel helpers extraction | ✅ Complete — `2f5a61b` |
| Stage 2f-2 — Runtime snapshot action helpers extraction | ✅ Complete — `d03ca21` |
| Stage 2f — Runtime snapshot/reporting extraction | ✅ Complete |
| Stage 2g — Remaining runtime-budget helper extraction | ✅ Complete — `a035781` |
| Stage 2 — State & domain logic extraction | ✅ Complete (2a/2b/2c/2d/2e/2f/2g; rebalance BUY preview completed later in Stage 3b) |
| Stage 3a — SELL execution flow | ✅ Complete — `43e8623` tests, `9ebc5f0` extraction |
| Stage 3b — BUY execution flow | ✅ Complete — `88237da` tests, `98ea0f9` extraction |
| Stage 3 — Execution flows | ✅ Complete |
| Stage 4 — Runtime loop | ✅ Complete under safe boundary — 4-0/4-1/4-2/4-3/4-4a/4-4b complete; high-risk `run_cycle()` internals explicitly deferred |
| Stage 5 — Cleanup | ✅ Complete (2026-06-02) — 5-1 `3894aea`, 5-2 `af91312`, 5-3 `2b3fd63`, unused import cleanup `eddfc7b`; deferred `run_cycle()` internals/closures intentionally retained |

### Stage 1 close-out note (2026-05-20)

Stage 1 is closed as **low-risk pure helper extraction**, not as complete runtime budget behavior extraction.

Extracted to `app/core/runtime_budget.py` across Stage 1c:
- Pure query helpers: `api_budget_backoff_active`, `api_budget_transient_backoff_active`, `api_budget_backoff_remaining_seconds`, `api_budget_transient_backoff_remaining_seconds`, `api_budget_can_quote`, `api_budget_remaining_quotes`, `api_budget_can_request`, `api_budget_remaining_requests`, `api_budget_request_window_size`, `api_budget_preserves_buy_scan_reserve` (1c-1)
- `prune_api_budget_requests` (1c-2)
- `prune_recent_rate_limit_hits`, `prune_recent_transient_api_errors`, `summarize_api_budget_state` (1c-4a)
- `api_budget_update_rate_limit_recovery_state`, `api_budget_update_transient_recovery_state` (1c-4b)

Still in `app/main.py` as full implementations — deferred to Stage 2 or later:
- Register helpers (`_api_budget_register_request`, `_api_budget_register_requests`, `_api_budget_register_measured_extra_requests`) — not shims
- `_api_budget_min_wait_for_request_slot`, `_api_budget_note_transient_api_error` — pure but constants not yet moved
- Three `_API_TRANSIENT_*` constants — still in `app/main.py`
- `_api_budget_note_rate_limit` — bottleneck singleton dependency (Stage 2)
- `_build_runtime_rate_control` — runtime-pressure module target (Stage 2)
- `_build_api_budget_state` — budget initializer (Stage 2 module cleanup)
- `_wait_for_execution_request_budget` — `note()` closure dependency (Stage 4)
- `_build_scheduler_tick_decision` — session loop module (Stage 4)
