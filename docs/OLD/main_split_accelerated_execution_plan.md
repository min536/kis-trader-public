# Accelerated app/main.py Split Execution Plan

> **Status: COMPLETE / FROZEN (2026-06-02).** All stages (1–5) finished under the documented safe boundary; `app/main.py` split is complete. Preserved as the execution record — not an active plan.

Updated: 2026-06-02

## 1. Purpose

This document is an acceleration overlay on top of `docs/main_split_plan.md`.
It does not replace the safety boundaries, split principles, or target module
structure defined there. It compresses the remaining extraction work into
larger, safe day-sized chunks so the split finishes in ~8 working days instead
of ~15.

Key acceleration levers:
- Bundle same-domain pure helpers into single extraction commits.
- Combine characterization tests and extraction into same-day work.
- Reduce docs-only checkpoint commits to the minimum needed.
- Keep hard safety boundaries: SELL and BUY execution on separate days,
  execution flow never bundled with runtime loop.

This is a planning document only. It does not authorize code movement, runtime
behavior changes, live scans, broker calls, or order-path changes.

## 2. Current State

| Item | Value |
| :--- | :--- |
| Branch | `main` (clean, synced with origin) |
| Latest pushed commit | `eddfc7b refactor: remove unused buy error classifier alias` |
| `app/main.py` size | 5,965 lines |
| Top-level definitions in `app/main.py` | 119 |
| Total test count | 1,367 tests + 22 subtests |
| Stage 2 status | ✅ Complete (2a/2b/2c/2d/2e/2f/2g all done; rebalance BUY preview completed later in Stage 3b) |
| Stage 3 status | ✅ Complete (3a SELL flow, 3b BUY flow) |
| Stage 4 status | ✅ Complete under safe boundary (4-0/4-1/4-2/4-3/4-4a/4-4b complete) |
| Stage 5 status | ✅ Complete (5-1 dead shim removal, 5-2 test patch migration, 5-3 migrated wrapper removal, plus unused import cleanup) |
| Next stage | None — `app/main.py` split complete under the safe boundary |

### Extracted modules (12 modules)

| Module | Lines | Stage | Functions |
| :--- | :--- | :--- | :--- |
| `app/core/error_classification.py` | 161 | 1a | 9 classifiers |
| `app/core/runtime_budget.py` | 259 | 1c | 15 budget helpers |
| `app/core/reconciliation.py` | 198 | 2d | 4 reconciliation |
| `app/core/formatters.py` | 28 | 2c-pre | `format_bps` + signed formatters |
| `app/risk/pnl_brake.py` | 342 | 2a | 7 PnL brake |
| `app/risk/regime.py` | 182 | 2b | 4 regime |
| `app/execution/rebalance.py` | 732 | 2c-pre/1a/1b/1c/1d | 14 rebalance evaluation/display helpers |
| `app/scanner/runtime_scan.py` | 1,013 | 2e-0/1/2/3 | 18 runtime scan helpers (pre-gating, normalizers, layered universe/profile/shallow/deep plans, guard filtering, scan display, API budget cap) |
| `app/reporting/runtime_snapshots.py` | ~520 | 2f-1/2f-2 | 9 runtime snapshot/reporting helpers (candidate outcomes, funnel stats, market snapshots, cycle action, engine events, display) |
| `app/execution/sell_flow.py` | 599 | 3a | SELL order execution flow |
| `app/execution/buy_flow.py` | 1,769 | 3b | BUY execution flow and BUY-adjacent rebalance preview helpers |
| `app/runtime/session_loop.py` | 455 | 4-1/4-2/4-3/4-4b | Scheduler timing, schedule status display, runtime rate control, repeated session loop |

### Remaining high-level stages

| Stage | Status | Risk |
| :--- | :--- | :--- |
| 2c rebalance evaluation/display | ✅ Complete — `9937ab0`, `08b34a1`, `2c4bdfd`, `baceedb`, `00837fa`, `22c1251`, `11d0100`; BUY preview completed later in Stage 3b | Medium |
| 2e runtime scan | ✅ Complete — `e7c8d07`, `b29b07c`, `f704c80`, `8af57fa` | Medium |
| 2f runtime snapshots/reporting | ✅ Complete — `8198bf3` (tests), `2f5a61b` (funnel helpers), `d03ca21` (action helpers) | Medium |
| 2g remaining runtime budget | ✅ Complete — `a035781` | Medium |
| **Stage 2 overall** | **✅ Complete** | — |
| 3a SELL execution flow | ✅ Complete — `43e8623` (tests), `9ebc5f0` (extraction) | **High** |
| 3b BUY execution flow | ✅ Complete — `88237da` (tests), `98ea0f9` (extraction) | **High** |
| **Stage 3 overall** | **✅ Complete** | — |
| 4 runtime loop | ✅ Complete under safe boundary — 4-0/4-1/4-2/4-3/4-4a/4-4b complete; `run_cycle()` internals explicitly deferred | **Highest** |
| 5 cleanup/shim removal | ✅ Complete — `3894aea`, `af91312`, `2b3fd63`, `eddfc7b` | Low |

## 3. Acceleration Principles

1. **Bundle same-domain pure helpers.** Functions in the same cluster that share
   the same target module, same test files, and same risk level can move in a
   single extraction commit.

2. **Characterization tests before extraction for risky groups.** Stages 2e, 2f,
   3a, 3b require characterization tests if direct test coverage is thin. These
   can be written and committed on the same day as the extraction.

3. **Push after each extraction commit.** Every extraction commit that passes the
   full test suite gets pushed immediately. Do not batch multiple extraction
   commits before pushing.

4. **Full suite validation after extraction commits.** Run
   `.venv/bin/python -m pytest tests/ -v --tb=short` after every extraction
   commit. Targeted test runs are acceptable during development but the full
   suite is the gate before push.

5. **Never bundle order execution with runtime loop.** Stage 3 (SELL/BUY
   execution) and Stage 4 (runtime loop) must be on separate days with at
   least one clean push between them.

6. **Never bundle SELL and BUY execution.** Stage 3a and Stage 3b must be on
   separate days.

7. **Keep `app.main` compatibility wrappers.** Thin delegation wrappers in
   `app/main.py` remain until Stage 5 cleanup. Tests that patch
   `app.main._xxx` symbols continue to work without modification during
   extraction stages.

8. **Docs-only commits are optional checkpoints.** Feasibility analyses for 2e/2f
   can be lightweight inline notes in this document rather than separate MD
   files, unless complexity warrants a full analysis.

## 4. Stage 2c Completion Status

Stage 2c evaluation-side extraction is complete. `app/execution/rebalance.py`
now owns the rebalance evaluation/display helpers, while `app/main.py` keeps
private compatibility wrappers for existing call sites and tests. Full Stage 2
remains in progress because Stage 2f implementation and Stage 2g runtime-budget
cleanup are still pending.

### Completed Stage 2c commits

| Slice | Commit | Result |
| :--- | :--- | :--- |
| Feasibility analysis | `9937ab0` | Mapped the rebalance dependency graph and selected the Stage 3b deferral boundary. |
| 2c-pre | `08b34a1` | Extracted `_format_bps` to `app.core.formatters`. |
| 2c-0 | `2c4bdfd` | Added rebalance helper characterization tests. |
| 2c-1a | `baceedb` | Extracted pure rebalance helpers. |
| 2c-1b | `00837fa` | Extracted position-sizing and BUY-analysis block context builders. |
| 2c-1c | `22c1251` | Extracted rebalance sell sizing, concentration preview, and pair evaluation helpers. |
| 2c-1d | `11d0100` | Extracted rebalance candidate, quality preview, and display helpers. |

### Stage 2c-1c: rebalance evaluation helpers (complete)

**Scope:** 3 functions, ~218 lines

| Function | Line | Lines | Dependencies |
| :--- | :--- | :--- | :--- |
| `_calculate_rebalance_sell_sizing` | 2703 | ~51 | `calculate_sell_position_sizing`, `math.ceil` |
| `_estimate_rebalance_concentration_preview` | 2766 | ~55 | `build_concentration_metrics` (already in module) |
| `_build_rebalance_pair_evaluation` | 2831 | ~112 | `calculate_selection_score`, `calculate_sell_position_sizing`, `_estimate_rebalance_concentration_preview`, `_format_bps` |

**Why these three together:** `_build_rebalance_pair_evaluation` calls
`_estimate_rebalance_concentration_preview` internally, and both are called by
`_build_rebalance_candidate`. Moving them together avoids cross-module calls
back into `app.main`.

**Target file:** `app/execution/rebalance.py`

**New imports needed in `app/execution/rebalance.py`:**
- `math.ceil`
- `calculate_sell_position_sizing` from `app.execution.sell_position_sizing`
- `calculate_selection_score` from `app.scanner.scoring`
- `format_bps` from `app.core.formatters`

**Tests:**
- `tests/test_rebalance_helpers.py` (46 tests) — existing characterization tests
  cover `_build_rebalance_pair_evaluation` (7 tests) and concentration metrics
- `tests/test_rebalance_candidate.py` (12 tests) — exercises
  `_build_rebalance_candidate` which calls both helpers
- Add extraction contract tests for the 3 new public functions

**Commit:** `22c1251 refactor: extract rebalance pair evaluation helpers`

### Stage 2c-1d: rebalance candidate + display (complete)

**Scope:** 5 functions, ~355 lines

| Function | Line | Lines | Dependencies |
| :--- | :--- | :--- | :--- |
| `_build_rebalance_candidate` | 2943 | ~154 | `_build_rebalance_pair_evaluation` (now in module), `_serialize_*` (already in module) |
| `_build_quality_rebalance_preview` | 3097 | ~116 | `_build_rebalance_pair_evaluation`, `_serialize_*` |
| `_print_rebalance_preview` | 3213 | ~49 | `format_krw`, `format_qty`, `format_bps` |
| `_print_quality_rebalance_preview` | 3262 | ~31 | `format_krw`, `format_qty` |
| `_print_rebalance_skip` | 3293 | ~5 | None |

**Why these five together:** `_build_rebalance_candidate` and
`_build_quality_rebalance_preview` both call `_build_rebalance_pair_evaluation`
(moved in 2c-1c). The three print functions are trivial display-only helpers
that belong with the builders they render. All dependencies are already in the
target module or leaf formatters.

**Target file:** `app/execution/rebalance.py`

**New imports needed:** `format_krw`, `format_qty` from `app.core.formatters`

**Tests:**
- `tests/test_rebalance_helpers.py` — existing characterization tests cover
  `_build_quality_rebalance_preview` (4 tests), `_print_rebalance_preview`
  (4 tests), `_print_quality_rebalance_preview` (2 tests),
  `_print_rebalance_skip` (1 test)
- `tests/test_rebalance_candidate.py` — 12 tests exercise
  `_build_rebalance_candidate` directly
- Add extraction contract tests for new public functions

**Commit:** `11d0100 refactor: extract rebalance candidate helpers`

### Stage 2c docs update

This checkpoint documents Stage 2c completion after both extraction commits
passed targeted rebalance tests and the full suite.

### Explicitly deferred from Stage 2c

| Function | Line | Reason |
| :--- | :--- | :--- |
| `_build_rebalance_buy_preview` | 4747 | Depends on `_build_math_sizing_context` and synthetic `ExecutionSnapshot` construction. BUY-flow-adjacent — completed in Stage 3b. |
| `_print_rebalance_buy_preview` | 4837 | Display companion of `_build_rebalance_buy_preview`. Completed in Stage 3b. |

These two helpers were intentionally left out of Stage 2c because their behavior
is BUY-flow-adjacent and tied to synthetic execution snapshot construction, not
the Stage 2c rebalance evaluation/display slice. They moved with Stage 3b.

## 5. Remaining Stage 2e/2f Plan

Stage 2e is complete. Stage 2f feasibility analysis and characterization
tests are complete; the next commits should be Stage 2f implementation work,
not more discovery. Keep these as scanner/reporting extractions only: no live
scan config changes, no BUY order tail, no SELL order flow, and no runtime
state schema changes.

### Stage 2e: runtime scan helpers

**Target module:** `app/scanner/runtime_scan.py` (new)

**Status:** Complete — `e7c8d07`, `b29b07c`, `f704c80`, `8af57fa`.

**Scope:** 18 helpers, ~1,106 lines. This is scanner planning/filtering, not
order execution. The helpers consume runtime state, settings, cached market
snapshots, and API budget state that `run_cycle` already assembled. They do not
call broker/KIS APIs or run live scans.

| Sub-stage | Functions | Risk | Notes |
| :--- | :--- | :--- | :--- |
| 2e-0: characterization tests | Profile rotation, layered universe cursoring, pre-gating re-entry/PnL pause, shallow shortlist, runtime guard filtering, print/log summaries | Medium | Test-only commit before code movement |
| 2e-1: pre-gating + normalizers | `_count_symbol_buy_entries_today`, `_is_symbol_in_reentry_cooldown`, `_resolve_sell_exit_reason`, `_build_buy_scan_pre_gating`, `_normalize_pre_gating_payload`, `_normalize_buy_funnel_reason`, `_resolve_deep_eval_rejection_reason`, `_resolve_buy_candidate_rejection_reason`, `_resolve_buy_candidate_selection_outcome` | Medium | Preserve re-entry diagnostics and existing rejection buckets |
| 2e-2: layered universe + shallow/deep plans | `_take_circular_window`, `_select_buy_scan_profile`, `_build_buy_scan_layered_universe`, `_build_buy_scan_shallow_plan`, `_build_buy_scan_deep_eval_symbols`, `_cap_buy_scan_deep_eval_symbols_for_api_budget` | Medium | Preserve scan cursors, profile rotation, API budget cap behavior, and `scan_symbols_max_per_cycle` semantics |
| 2e-3: runtime guards + scan display | `_apply_buy_runtime_guards_to_scan_results`, `_print_buy_scan_stage_summary`, `_print_buy_pre_gating_summary`, `_print_buy_runtime_filter_summary` | Medium | Complete via callback wrapper for engine-event logging; no `app.main` import cycle |

**Key dependencies:** `app.strategy.reentry`, `app.runtime_state`,
`app.scanner.service.build_shallow_scan_candidates`, `app.core.runtime_budget`,
`app.core.time_utils`, and `app.core.formatters`.

**State and side effects:** reads recent orders, symbol-level buy counts,
untradable symbols, cached snapshots, and re-entry state; mutates scan cursors
and re-entry diagnostic state only. Print helpers write stdout and two helpers
log engine events. Keep `app.main` private compatibility wrappers until Stage 5.

**Test coverage:** existing direct coverage lives in
`tests/test_buy_scan_budget_cap.py` and `tests/test_scan_only_diagnostic.py`;
leaf scanner behavior is covered by `tests/test_scanner_shallow_cache.py` and
`tests/test_scanner_parse_error_skip.py`. Add characterization tests before
moving layered universe, runtime guard, and print/log behavior.

**Validation:**

```
.venv/bin/python -m py_compile app/main.py app/scanner/runtime_scan.py
.venv/bin/python -m pytest tests/test_buy_scan_budget_cap.py tests/test_scan_only_diagnostic.py tests/test_scanner_shallow_cache.py -v
.venv/bin/python -m pytest tests/test_buy_scan_budget_cap.py tests/test_scan_only_diagnostic.py tests/test_scanner_parse_error_skip.py tests/test_scanner_shallow_cache.py -q
.venv/bin/python -m pytest tests/ -x -q --tb=short
```

### Stage 2f: runtime snapshots/reporting

**Target module:** `app/reporting/runtime_snapshots.py` (new)

**Status:** ✅ Complete — `8198bf3` (characterization tests), `2f5a61b` (funnel
helpers), `d03ca21` (action helpers). `app/reporting/runtime_snapshots.py` owns
all 9 production helpers. `app/main.py` retains private compatibility wrappers.

**Scope:** 9 production helpers, ~520 lines. This is payload construction, runtime
state/reporting mutation, and display/logging only. It must preserve candidate
outcome rows, cycle stats, runtime snapshots, cycle snapshots, Slack/dashboard
consumers, and PID/process identity fields.

| Sub-stage | Functions | Risk | Notes |
| :--- | :--- | :--- | :--- |
| 2f-0: characterization tests | Candidate outcome rows, funnel stats, market snapshot serialization/update, cycle action mutation, engine-event log payloads | Medium | ✅ Complete — `8198bf3`; `tests/test_runtime_snapshot_helpers.py` |
| 2f-1: candidate outcomes + funnel stats | `_build_buy_candidate_outcome_records`, `_build_buy_cycle_funnel_stats` | Medium | ✅ Complete — `2f5a61b`; extracted to `app/reporting/runtime_snapshots.py` |
| 2f-2: runtime market snapshots + cycle action/log/display | `_serialize_runtime_market_snapshot`, `_update_recent_market_snapshots`, `_log_engine_event`, `_record_cycle_action`, `_print_last_action`, `_print_cycle_conclusion`, `_print_runtime_state_summary` | Medium | ✅ Complete — `d03ca21`; extracted to `app/reporting/runtime_snapshots.py` |

`_take_circular_window` is reclassified to Stage 2e-2 because it is a direct
dependency of layered universe selection, not reporting.

**Key dependencies:** `app.core.time_utils.get_korean_now`,
`app.scanner.symbol_names.get_symbol_name`,
`app.strategy.core_shadow.build_core_shadow_fields`, `app.core.order_log`,
`app.runtime_state`, `app.core.formatters`, and Stage 2e normalizers.

**State and side effects:** `_log_engine_event` writes order-log engine events,
`_record_cycle_action` mutates last-decision state, and
`_update_recent_market_snapshots` mutates cached market snapshots. Builder
helpers are pure but schema-critical. No broker/KIS APIs are called.

**Test coverage:** existing coverage is indirect through
`tests/test_candidate_outcome_logger.py`, `tests/test_cycle_snapshots.py`,
`tests/test_pid_logging.py`, `tests/test_main_benchmark_snapshot.py`, Slack
runtime status tests, and downstream analysis/export tests. Add direct builder
characterization before extraction.

**Validation:**

```
.venv/bin/python -m py_compile app/main.py app/reporting/runtime_snapshots.py
.venv/bin/python -m pytest tests/test_candidate_outcome_logger.py tests/test_cycle_snapshots.py tests/test_pid_logging.py tests/test_main_benchmark_snapshot.py -v
.venv/bin/python -m pytest tests/test_runtime_status_snapshot.py tests/test_slack_bot.py tests/test_engine_backtest_parity.py tests/test_export_signal_dataset.py -q
.venv/bin/python -m pytest tests/ -x -q --tb=short
```

## 6. Stage 2g: Remaining Runtime Budget Helpers

**Target module:** `app/core/runtime_budget.py` (expand existing)

**Status:** ✅ Complete — `a035781`.

Extracted 6 helpers and 3 transient constants to `app/core/runtime_budget.py`:
- `api_budget_register_request`, `api_budget_register_requests`,
  `api_budget_register_measured_extra_requests`
- `api_budget_min_wait_for_request_slot`
- `api_budget_note_transient_api_error`
- `build_api_budget_state`
- `API_TRANSIENT_BACKOFF_WINDOW_SECONDS`, `API_TRANSIENT_BASE_BACKOFF_SECONDS`,
  `API_TRANSIENT_MAX_BACKOFF_SECONDS`

`app/main.py` retains private compatibility wrappers for all moved helpers.

### Still deferred (blocked)

| Function | Lines | Blocker | Target stage |
| :--- | :--- | :--- | :--- |
| `_api_budget_note_rate_limit` | ~29 | Calls `_record_bottleneck()` — bottleneck singleton not yet extracted | Stage 3/4 |
| `_wait_for_execution_request_budget` | ~37 | Calls `note()` inner function — closure-captured nonlocal in `run_cycle` | Stage 4 |
| `_build_runtime_rate_control` | ~131 | Large builder with runtime pressure logic; target is a runtime-pressure module, not `runtime_budget.py` | Stage 4 |
| `_build_scheduler_tick_decision` | ~49 | Belongs to session loop module | Stage 4 |

**Tests:** `tests/test_runtime_budget_helpers.py`, `tests/test_runtime_rate_control.py`

## 7. Stage 3: Execution Flow Plan

### Stage 3a: SELL execution flow (separate day)

**Status:** ✅ Complete — characterization tests (`43e8623`) and extraction (`9ebc5f0`) are pushed.

**Scope:** `_run_sell_order_flow` (524 lines) + `_run_sell_test_cycle` (148 lines)

**Target module:** `app/execution/sell_flow.py` (new)

**Risk:** **High.** This function calls broker APIs (sell order submission),
mutates runtime state (order log, cycle state), sends Slack notifications, and
enforces order guards (cooldown, no-position detection). The order of operations
is critical and must not change.

**Pre-requisites:**
1. Characterization tests covering:
   - Guard check sequences
   - Slack notification call order
   - State mutation side effects
   - No-position sell classification
2. Review of all test files that patch `app.main._run_sell_order_flow`:
   - `tests/test_order_guard.py` (14 tests, 379 lines)
   - `tests/test_no_position_sell_detection.py` (18 tests, 148 lines)
   - `tests/test_main_slack_order_hooks.py` (10 tests, 267 lines)
   - `tests/test_pid_logging.py` (7 tests, 168 lines)

**Execution:**
1. Characterization test commit.
2. Extraction commit with full compatibility shims.
3. Full test suite validation.
4. Push.

**Commit:** `refactor: extract sell order flow`

### Stage 3b: BUY execution flow (separate day, after 3a)

**Status:** ✅ Complete — characterization tests (`88237da`) and extraction (`98ea0f9`) are pushed.

**Scope:** BUY execution tail from `run_cycle` (~600 lines, lines 9050-10300) +
BUY-adjacent helpers:
- `_build_rebalance_buy_preview` (90 lines)
- `_print_rebalance_buy_preview` (51 lines)
- `_build_preview_orderable_output_from_portfolio` (11 lines)
- `_build_math_sizing_context` (11 lines)

**Target module:** `app/execution/buy_flow.py` (new)

**Risk:** **High.** BUY order submission, position sizing, execution snapshot
construction, math sizing overlay.

**Pre-requisites:**
1. Stage 3a must be complete and stable.
2. Characterization tests for BUY execution tail.
3. Review of test files:
   - `tests/test_buy_orderable_market_session_order.py` (3 tests, 47 lines)
   - `tests/test_buy_order_submit_budget.py` (3 tests, 42 lines)
   - `tests/test_order_guard.py` (14 tests, 379 lines)

**Execution:**
1. Characterization test commit.
2. Extraction commit with full compatibility shims.
3. Full test suite validation.
4. Push.

**Commit:** `refactor: extract buy execution flow`

### Stage 3 safety rules

- **Never bundle SELL and BUY execution in the same day or commit.**
- **Never bundle execution flow with runtime loop.** Stage 3 is now complete;
  Stage 4 must begin with separate runtime-loop analysis, not extraction-first.
- **Never change order submission sequence** (guard check → market session
  recheck → order submit → state mutation → notification).
- **Never use broker/KIS API calls for validation.** Tests only.
- **Never modify order guard logic** during extraction. Pure code movement only.

## 8. Stage 4: Runtime Loop Plan

### Pre-requisites

Stage 3 (both SELL and BUY) must be complete, pushed, and stable before
starting Stage 4. This prerequisite is now satisfied as of `98ea0f9`.

Stage 4 started with runtime-loop feasibility analysis and closure boundary
review. This prerequisite was satisfied by `9abee45` before any
scheduler/session-loop code moved.

### Analysis phase (1 commit)

Produce a feasibility analysis covering:
- `run_cycle()` structure (currently 2,928 lines): identify which sections become calls
  to extracted modules vs. which remain as orchestration.
- Inner function closure analysis:
  - `note()` — captures warning/error counters through `nonlocal`
  - `resolve_buy_universe_symbols()` — captures `settings`
  - `log_buy_universe_source()` — captures `settings` and calls `note()`
- Mutable local variable inventory (~100 variables).
- `finally` block dependencies.
- Identify extractable scheduler/tick helpers:
  - `_is_due` (6 lines)
  - `_build_scheduler_tick_decision` (51 lines)
  - `_print_engine_schedule_state` (77 lines)
  - `_log_engine_event` (25 lines)
  - `_parse_hhmm_window`, `_within_hhmm_window`

#### Stage 4-0 feasibility result (2026-05-29)

Current baseline is materially smaller than the original split inventory:
`app/main.py` is 6,486 lines with 132 top-level functions. `run_cycle()` is
lines 3317-6244 (2,928 lines), and `main()` is lines 6247-6482 (236 lines).
The Stage 4 risk remains **highest** because the remaining runtime loop is
coupled to process locking, scheduler due timestamps, API backoff state, Slack
runtime snapshots, cycle snapshot persistence, and mutable locals that feed the
`run_cycle()` final block.

Safe extraction candidates for the next code slice:

- `_is_due` and `_build_scheduler_tick_decision` into the session-loop module,
  with `app.main` wrappers retained until tests move.
- `_parse_hhmm_window` and `_within_hhmm_window` with runtime rate-control
  tests.
- `_build_runtime_rate_control` only if the slice explicitly preserves its
  `api_budget_state` mutations (`consecutive_backoff_cycles`,
  `degraded_mode_until`, `degraded_mode_reason`).

Deferred candidates:

- `_wait_for_execution_request_budget` because it calls `note(...)` and sleeps.
- `_api_budget_note_rate_limit` because it mutates API budget state and records
  `KIS_RATE_LIMIT_BACKOFF` through the bottleneck aggregator.
- `_print_engine_schedule_state` because it is print-side-effect display logic,
  not pure scheduler logic.
- `_log_engine_event` because it is already a wrapper over
  `app.runtime.snapshots.log_engine_event`; keep the wrapper for test patch
  compatibility.
- `run_cycle()` extraction itself. First reduce closure/finally dependencies.

Hard blockers before extracting `run_cycle()`:

- `note()` captures `cycle_warning_count` and `cycle_error_count` through
  `nonlocal`; all calls are guarded by `tests/test_main_note_call_arity.py`.
- `resolve_buy_universe_symbols()` and `log_buy_universe_source()` capture
  `settings`; `log_buy_universe_source()` also calls `note()`.
- The `run_cycle()` final block (5795-6244) consumes state, API budget state,
  scheduler state, timing/API summaries, session/portfolio snapshots, BUY/SELL
  candidates, execution/sizing/risk payloads, rebalance previews, rate-limit
  flags, SELL watch cursors, BUY scan counters, throttle metrics, and warning
  counters. It also persists runtime state, cycle snapshots, candidate
  outcomes, cycle stats, performance reports, Slack status snapshots, and daily
  summaries.

Recommended Stage 4 order:

1. **4a:** docs/analysis only. No production code, tests, runtime module
   creation, live scans, `app.main`, or `run_session.sh`.
2. **4b:** scheduler helper extraction with wrappers and targeted tests.
3. **4c:** `main()` runtime loop/session extraction. Keep `main()` responsible
   for `get_settings()`, `acquire_app_main_lock()`, startup validation, and the
   call into the extracted session loop.
4. **4d:** `run_cycle()` context/finally dependency reduction only after 4b/4c
   pass. Defer full `run_cycle()` extraction if the final block still requires
   broad mutable local threading.

Stop conditions: any need to touch SELL/BUY execution flow, change `run_once`,
change duplicate process lock behavior, alter base tick/last due timestamp
semantics, change runtime state schema, run live broker/KIS paths, or change
the final persistence payload shape.

#### Stage 4 progress sync (2026-06-02)

Stage 4 is complete under the safe extraction boundary. Completed and pushed:

| Slice | Commit | Result |
| :--- | :--- | :--- |
| 4-0 feasibility analysis | `9abee45` | Mapped closure blockers, mutable locals, `finally` dependencies, validation gates, and stop conditions. |
| 4-1 scheduler timing helpers | `1743173` | Extracted `is_due`, `build_scheduler_tick_decision`, `parse_hhmm_window`, and `within_hhmm_window` to `app/runtime/session_loop.py`; kept `app.main` wrappers. |
| 4-2 runtime loop status helper | `ae4f303` | Extracted `print_engine_schedule_state`; kept the `app.main` wrapper and unchanged call sites. |
| 4-3 runtime rate control helper | `b0025f2`, `c1947b4` | Added characterization coverage and extracted `build_runtime_rate_control`; preserved API-budget state mutations and kept the `app.main` wrapper. |
| 4-4a main-loop characterization | `3c5b26a` | Added deterministic coverage for `run_once`, base tick calculation, transient/KIS backoff behavior, due timestamp updates, exception handling, and app-main lock ownership. |
| 4-4b repeated session loop | `252aa34` | Extracted only `run_session_loop()` to `app/runtime/session_loop.py`; retained `main()` ownership of app-main locking, startup validation, and `run_once`; passed `run_cycle` and loop dependencies as explicit callbacks. |

Completed Stage 4 scope:

- Scheduler timing helpers and HHMM window helpers.
- Scheduler status output helper.
- Runtime rate-control helper with preserved API-budget state mutation.
- Main-loop characterization tests.
- Repeated session-loop extraction behind the `app.main._run_session_loop()`
  compatibility wrapper.
- `main()` retention of settings loading, app-main lock ownership, startup
  validation, and the `run_once` path.

Explicitly deferred beyond the Stage 4 completion boundary:

- `_api_budget_note_rate_limit` because bottleneck recording ownership and the
  `_record_bottleneck()` dependency are not yet decoupled from `app.main`.
- `_wait_for_execution_request_budget` because it couples the `note()` closure,
  sleep/callback behavior, and order-execution pacing.
- Full `run_cycle()` internals because broad mutable-local, `finally`,
  persistence, and runtime-schema dependencies make movement high risk.
- The final persistence/reporting block inside `run_cycle()` because it writes
  runtime snapshots, cycle snapshots, Slack status, daily summaries, and other
  artifacts.
- `note()`, `resolve_buy_universe_symbols()`, and
  `log_buy_universe_source()` because they remain closure-heavy.
- Any context-object redesign for these items belongs to a future explicitly
  scoped effort, not Stage 5 cleanup.

### Extraction phase

The Stage 4 extraction phase is complete under the safe boundary. Do not extend
Stage 4 into `run_cycle()` internals as part of cleanup.

Target modules:
- `app/runtime/session_loop.py` — loop entry, scheduler tick, `_is_due`
- `app/runtime/session_context.py` — deferred future redesign only; not created

The inner function closure issues require signature changes (adding explicit
parameters). This is a behavior-preserving refactor but changes call sites
within `run_cycle`, so it remains deferred beyond Stage 4 and Stage 5.

### Validation

- `tests/test_session_lock.py` (11 tests, 173 lines)
- `tests/test_session_lock_runtime.py` (3 tests, 126 lines)
- `tests/test_runtime_rate_control.py` (17 tests, 390 lines)
- `tests/test_main_note_call_arity.py` (1 test, 46 lines)
- `tests/test_main_loop_characterization.py` (10 tests, 321 lines)

Validation remains test-only: do not run `app.main`, `run_session.sh`, live
scans, broker/KIS APIs, or order APIs.

## 9. Stage 5: Cleanup Plan

Status: ✅ Complete — `3894aea` (5-1 dead shim removal), `af91312` (5-2 test
patch migration), `2b3fd63` (5-3 migrated wrapper removal), and `eddfc7b`
(unused buy error-classifier import cleanup). The final docs/validation record is
in the "Stage 5 completion" subsection below.

### Scope

1. Remove temporary compatibility shims/wrappers from `app/main.py` where the
   only callers are `run_cycle` (update call sites to use new module directly).
2. Update test files to import from new modules instead of `app.main`.
3. Clean up unused imports in `app/main.py`.
4. Update `docs/main_split_plan.md` with final status.
5. Run full test suite.

Stage 5 must not absorb the deferred Stage 4 redesign items: do not move
`run_cycle()` internals, its final persistence/reporting block, closure-heavy
inner helpers, `_api_budget_note_rate_limit`, or
`_wait_for_execution_request_budget`.

### Rules

- Keep cleanup commits separate from extraction commits.
- Do not change runtime behavior.
- Run full test suite after each cleanup commit.
- Shims that external consumers depend on (Slack bot, dashboards, replay
  tooling) must remain or be redirected.

### Stage 5 completion (2026-06-02)

Stage 5 cleanup is complete under the safe boundary. Latest pushed baseline:
`eddfc7b`.

| Slice | Commit | Result |
| :--- | :--- | :--- |
| 5-1 dead shim removal | `3894aea` | Removed dead `app.main` compatibility shims with no remaining callers. |
| 5-2 test patch migration | `af91312` | Migrated split-helper test patch targets off `app.main` onto the owning modules. |
| 5-3 migrated wrapper removal | `2b3fd63` | Removed `app.main` compatibility wrappers whose tests had migrated. |
| unused import cleanup | `eddfc7b` | Removed the unused `_looks_like_buy_untradable_response` import alias from `app/main.py`. |

**`app/main.py` final role:**

- Orchestration entrypoint for the trading runtime.
- Owner of the app-main fcntl lock (`acquire_app_main_lock()`) and startup
  validation.
- `run_cycle()` orchestrator.
- Holder of the deferred closure-heavy runtime pieces that remain inside
  `run_cycle()`.

Final shape: 5,965 lines, 119 top-level functions (down from 6,164 lines / 133
functions at the Stage 4 boundary).

**Remaining intentional `app.main` shims/wrappers (kept on purpose):**

- `_run_session_loop` → `app/runtime/session_loop.py`
- `_run_sell_order_flow` → `app/execution/sell_flow.py`
- `_run_buy_order_flow` / BUY orchestration callback boundary →
  `app/execution/buy_flow.py`
- `_log_engine_event` → `app/reporting/runtime_snapshots.py`
- scheduler / runtime-budget callbacks consumed by the session loop
- Slack / bottleneck hooks

**Deferred / not moved (still owned inside `app/main.py`):**

- `_api_budget_note_rate_limit`
- `_wait_for_execution_request_budget`
- `run_cycle()` internals and its `finally` persistence/reporting block
- `note()`
- `resolve_buy_universe_symbols()`
- `log_buy_universe_source()`

**Reasons for the deferrals:**

- Closure dependencies — `note()` and the BUY-universe inner helpers capture
  nonlocal `run_cycle()` state.
- Bottleneck-recording ownership — `_api_budget_note_rate_limit` →
  `_record_bottleneck()` is not yet decoupled from `app.main`.
- Order-execution pacing — `_wait_for_execution_request_budget` couples the
  `note()` closure, sleep/callback behavior, and order pacing.
- Broad mutable-local, `finally` persistence, and runtime-schema coupling in
  the `run_cycle()` final block.

Any context-object redesign for these deferred items is a future explicitly
scoped effort, not part of the completed Stage 5 cleanup.

**Final test status:** `1367 passed, 22 subtests passed` (full suite).

## 10. Day-by-Day Accelerated Schedule

### Conservative schedule (~12 working days)

| Day | Goal | Commits | Push |
| :--- | :--- | :--- | :--- |
| 1 | Stage 2c-1c: 3 evaluation helpers | test + extraction | Yes |
| 2 | Stage 2c-1d: 5 candidate + display helpers | test + extraction | Yes |
| 3 | Stage 2e-0 characterization tests | tests | Yes |
| 4 | Stage 2e-1: pre-gating + normalizers | extraction | Yes |
| 5 | Stage 2e-2/2e-3: universe + scan-only diagnostics | extraction | Yes |
| 6 | Stage 2f-0/2f-1: characterization + candidate/funnel extraction | tests + extraction | Yes |
| 7 | Stage 2g: remaining budget helpers | extraction | Yes |
| 8 | Stage 3a: SELL flow | ✅ Complete — tests + extraction | Yes |
| 9 | Stage 3b: BUY flow | ✅ Complete — tests + extraction | Yes |
| 10 | Stage 4: runtime loop analysis | analysis only first; extraction follows only after analysis | Yes |
| 11 | Stage 4: remaining loop work | extraction | Yes |
| 12 | Stage 5: cleanup | cleanup | Yes |

### Balanced accelerated schedule (~8 working days, recommended)

| Day | Goal | Scope | Commits | Validation |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Finish Stage 2c** | 2c-1c (3 helpers) + 2c-1d (5 helpers) | 2 extraction commits | `test_rebalance_helpers`, `test_rebalance_candidate`, full suite |
| 2 | **Stage 2e characterization + start** | Characterization tests + 2e-1 extraction | 2 commits | `test_buy_scan_budget_cap`, `test_scan_only_diagnostic`, full suite |
| 3 | **Finish Stage 2e + start 2f** | ✅ Complete — 2e-3 runtime guard/display extraction (`8af57fa`) + 2f-0 characterization tests (`8198bf3`) | 2 commits | `test_runtime_scan_helpers`, `test_runtime_snapshot_helpers`, scan/budget targeted tests, full suite (`1314 passed, 22 subtests`) |
| 4 | **Finish Stage 2f + Stage 2g** | ✅ Complete — 2f-1 funnel helpers (`2f5a61b`), 2f-2 action helpers (`d03ca21`), 2g budget helpers (`a035781`) | 3 extraction commits | `test_cycle_snapshots`, `test_pid_logging`, `test_runtime_budget_helpers`, `test_runtime_snapshot_helpers`, full suite |
| 5 | **Stage 3a: SELL flow** | ✅ Complete — characterization tests + extraction | 2 commits | `test_order_guard`, `test_no_position_sell_detection`, `test_main_slack_order_hooks`, full suite |
| 6 | **Stage 3b: BUY flow** | ✅ Complete — characterization tests + extraction | 2 commits | `test_buy_orderable_market_session_order`, `test_buy_order_submit_budget`, full suite (`1338 passed, 22 subtests`) |
| 7 | **Stage 4: runtime loop analysis + safe extraction** | ✅ Complete — 4-0 analysis, 4-1/4-2/4-3 helpers, 4-4a characterization, and 4-4b repeated-loop extraction | Analysis, characterization, and helper extraction commits | `test_session_lock`, `test_runtime_rate_control`, `test_session_loop_helpers`, `test_main_loop_characterization`, full suite |
| 8 | **Stage 5: cleanup** | ✅ Complete — 5-1 dead shim removal, 5-2 test patch migration, 5-3 migrated wrapper removal, unused import cleanup | `3894aea`, `af91312`, `2b3fd63`, `eddfc7b` | Full suite (`1367 passed, 22 subtests`) |

### Aggressive but acceptable schedule (~6 working days)

| Day | Goal | Scope | Required safeguards |
| :--- | :--- | :--- | :--- |
| 1 | **Stage 2e characterization prep** | 2e-0 characterization tests | Full suite before extraction; push after test-only commit |
| 2 | **Stage 2e + 2f** | All scan + reporting helpers | Characterization tests first; full suite after each extraction commit; push after each |
| 3 | **Stage 2g + remaining display** | Budget helpers + display helpers | Full suite gate |
| 4 | **Stage 3a: SELL flow** | Characterization + extraction | Full suite; **do not continue to BUY** |
| 5 | **Stage 3b: BUY flow** | ✅ Complete — BUY extraction only | Full suite after BUY; no Stage 4 code bundled |
| 6 | **Stage 4 analysis, then extraction planning** | Runtime-loop feasibility analysis first | Analysis commit before any scheduler extraction |

**Aggressive schedule constraints:**
- Never skip full suite between extraction commits on the same day.
- Never push without full suite green.
- If any targeted test fails, stop and debug before continuing.
- Day 4 must end with SELL only. No BUY work on SELL day.
- Stage 4 must not be bundled with execution-flow work and must not start with
  extraction before runtime-loop analysis is complete.

## 11. Validation Matrix

### Per-stage targeted tests

| Stage | Primary test files | Test count |
| :--- | :--- | :--- |
| 2c | `test_rebalance_helpers.py`, `test_rebalance_candidate.py` | 58 |
| 2e | `test_buy_scan_budget_cap.py`, `test_scan_only_diagnostic.py`, `test_scanner_parse_error_skip.py`, `test_scanner_shallow_cache.py` | 9+ |
| 2f | `test_cycle_snapshots.py`, `test_pid_logging.py`, `test_main_benchmark_snapshot.py` | 12 |
| 2g | `test_runtime_budget_helpers.py`, `test_runtime_rate_control.py` | 17+ |
| 3a SELL | `test_order_guard.py`, `test_no_position_sell_detection.py`, `test_main_slack_order_hooks.py`, `test_pid_logging.py`, `test_sell_watch_budget.py` | 59 |
| 3b BUY | `test_buy_orderable_market_session_order.py`, `test_buy_order_submit_budget.py`, `test_order_guard.py` | 20 |
| 4 | `test_session_lock.py`, `test_session_lock_runtime.py`, `test_runtime_rate_control.py`, `test_main_note_call_arity.py` | 32 |

### Standard validation commands

```bash
# Compile check
python -m py_compile app/main.py

# Targeted stage tests (example for Stage 2c)
.venv/bin/python -m pytest tests/test_rebalance_helpers.py tests/test_rebalance_candidate.py -v

# Full suite gate (required before push)
.venv/bin/python -m pytest tests/ -v --tb=short
```

## 12. Stop Conditions

Stop extraction and investigate if any of these occur:

1. **Any targeted test failure** after an extraction commit.
2. **Any full suite failure** after an extraction commit.
3. **Any accidental `app.main` or `run_session.sh` execution.**
4. **Any broker/KIS API call** during extraction work.
5. **Any order execution behavior diff** outside the approved stage scope.
6. **Any import cycle** where a new module imports from `app.main`.
7. **Any unplanned change** to live scan config, ETF path, or order sequence.
8. **Any `py_compile` failure** on `app/main.py` or new modules.

**Recovery:** `git revert <extraction-commit>` restores the previous state.
Compatibility wrappers in `app/main.py` ensure downstream tests continue
working even if an extraction is reverted.

## 13. Explicit Do Not Do

- **Do NOT move `_build_rebalance_buy_preview` or `_print_rebalance_buy_preview`
  in Stage 2c.** These depend on `_build_math_sizing_context` and synthetic
  `ExecutionSnapshot` construction, and were correctly completed in Stage 3b.
- **Do NOT bundle SELL and BUY execution** in the same day or commit.
- **Do NOT bundle execution flow (Stage 3) with runtime loop (Stage 4).**
- **Do NOT run `app.main` or `run_session.sh`** for validation. Tests only.
- **Do NOT change order submission sequence** (guard → recheck → submit →
  state → notify).
- **Do NOT modify live scan config** or scan universe settings.
- **Do NOT connect ETF buy path** or expand scan universe.
- **Do NOT remove `acquire_app_main_lock()`** or bypass the fcntl lock.
- **Do NOT modify runtime state schema** or log payload shapes during extraction.
- **Do NOT push without full test suite green.**
- **Do NOT extract `_wait_for_execution_request_budget`** until the `note()`
  closure, callback behavior, and order-execution pacing are addressed in a
  future explicitly scoped redesign.
- **Do NOT extract `_api_budget_note_rate_limit`** until the bottleneck
  singleton is decoupled from `app.main`.
- **Do NOT move `run_cycle()` internals or its final persistence/reporting
  block** during Stage 5 cleanup.
