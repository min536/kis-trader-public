# runtime pressure / API budget split plan

## Purpose

This document inventories the runtime pressure and API budget logic currently living in `app/main.py`.
The next implementation step should not move these helpers yet.  The goal is to make the inputs,
outputs, state keys, call order, timing-sensitive behavior, and test bundles explicit before any
behavior-preserving refactor.

No code move, import change, runtime behavior change, or trading behavior change is part of this
document-only step.

## Current Responsibility Summary

`app/main.py` currently owns the full runtime pressure loop:

- Initializes and carries the mutable `api_budget_state` dict across scheduler ticks.
- Tracks one-second request windows and per-tick quote usage.
- Detects EGW00201/KIS rate-limit responses and applies exponential backoff.
- Detects transient API/network failures and applies transient backoff.
- Switches runtime pressure mode between normal, midday, and degraded modes.
- Decides whether a scheduler tick should run sell watch, buy scan, both, or wait.
- Preserves BUY scan request/quote reserve while SELL watch is running.
- Reserves request capacity before balance, orderable lookup, and order submit.
- Performs actual `time.sleep` waits for request-window stabilization and backoff drain.
- Writes budget summaries into runtime state and cycle snapshots.

Because this logic directly affects API call order and wait timing, only pure calculation helpers
should be considered for the first extraction.

## Related Constants

| Name | Current role | Split caution |
| --- | --- | --- |
| `_API_TRANSIENT_BACKOFF_WINDOW_SECONDS` | rolling window for transient error hits | keep with transient backoff helpers if moved |
| `_API_TRANSIENT_BASE_BACKOFF_SECONDS` | base transient backoff seconds | must preserve exponential calculation |
| `_API_TRANSIENT_MAX_BACKOFF_SECONDS` | transient backoff cap | must preserve cap |
| `_TRANSIENT_API_ERROR_MARKERS` | text markers for transient API/network failures | parser behavior must remain identical |

## Current Function Inventory

| Function | Responsibility | Side effect |
| --- | --- | --- |
| `_build_runtime_rate_control` | computes normal/midday/degraded effective intervals and caps | mutates `consecutive_backoff_cycles`, `degraded_mode_until`, `degraded_mode_reason`; prints when degraded activates |
| `_build_api_budget_state` | creates initial mutable budget dict from settings | none beyond returning a new dict |
| `_summarize_api_budget_state` | returns runtime budget summary for display/snapshot | prunes `recent_requests`, `recent_rate_limit_hit_times`, `recent_transient_error_times` |
| `_api_budget_backoff_active` | checks active EGW00201 backoff | none |
| `_api_budget_transient_backoff_active` | checks active transient backoff | none |
| `_api_budget_backoff_remaining_seconds` | computes remaining EGW00201 backoff seconds | none |
| `_api_budget_transient_backoff_remaining_seconds` | computes remaining transient backoff seconds | none |
| `_api_budget_remaining_requests` | computes remaining request slots | prunes `recent_requests` |
| `_api_budget_request_window_size` | returns current request window size | prunes `recent_requests` |
| `_api_budget_note_rate_limit` | records EGW00201 hit and schedules exponential backoff | mutates hit/source/backoff keys; prints; calls `note_rate_limit`; records bottleneck |
| `_api_budget_note_transient_api_error` | records transient API/network hit and schedules transient backoff | mutates transient hit/source/backoff keys; prints |
| `_api_budget_update_rate_limit_recovery_state` | preserves or resets EGW00201 hit/source state after cycle | mutates `last_rate_limit_source`, `rate_limit_hits` |
| `_api_budget_update_transient_recovery_state` | preserves or resets transient hit/source state after cycle | mutates `last_transient_error_source`, `transient_error_hits` |
| `_wait_for_execution_request_budget` | waits before orderable/order submit phases to preserve request slots | prints; calls `note`; calls `time.sleep` |
| `_build_scheduler_tick_decision` | determines sell/buy due flags and skip-cycle decision | none, but changes main-loop cadence |
| `_looks_like_rate_limit_error` | detects EGW00201/rate-limit exceptions | none |
| `_rate_limit_source_from_response_body` | maps KIS response body rate-limit to source | none |
| `_rate_limit_source_from_exception` | infers rate-limit source from exception text | none |
| `_should_downgrade_empty_buy_scan_to_backoff` | decides whether an empty BUY scan should be reported as backoff | none |
| `_build_buy_scan_rate_limit_degraded_reason` | formats BUY scan backoff/degraded reason | none |

Related helpers that share the same state and should be considered during later implementation:

- `_prune_recent_rate_limit_hits`
- `_prune_recent_transient_api_errors`
- `_prune_api_budget_requests`
- `_api_budget_can_request`
- `_api_budget_register_request`
- `_api_budget_register_requests`
- `_api_budget_register_measured_extra_requests`
- `_api_budget_can_quote`
- `_api_budget_remaining_quotes`
- `_buy_scan_reserve_active`
- `_api_budget_preserves_buy_scan_reserve`
- `_api_budget_min_wait_for_request_slot`
- `_cap_buy_scan_deep_eval_symbols_for_api_budget`

## API Budget State Keys

Initial keys from `_build_api_budget_state`:

| Key | Meaning | Mutation points |
| --- | --- | --- |
| `recent_requests` | one-second rolling request timestamps | prune/register/request-window helpers |
| `quotes_used_this_tick` | quote calls consumed in current scheduler tick | reset in main loop; increment on quote registration |
| `backoff_until` | EGW00201 backoff deadline | `_api_budget_note_rate_limit` |
| `rate_limit_hits` | consecutive EGW00201 hit count used for exponential backoff | note/update recovery helpers |
| `last_rate_limit_source` | last known rate-limit source | note/source parsing/recovery helpers |
| `recent_rate_limit_hit_times` | 10-minute rate-limit hit window | prune/note/runtime rate control |
| `transient_error_until` | transient API/network backoff deadline | `_api_budget_note_transient_api_error` |
| `transient_error_hits` | recent transient hit count | transient note/update recovery helpers |
| `last_transient_error_source` | last known transient error source | transient note/source/recovery helpers |
| `recent_transient_error_times` | 10-minute transient hit window | prune/transient note/summary |
| `consecutive_backoff_cycles` | count of consecutive cycles while EGW00201 backoff active | `_build_runtime_rate_control` |
| `degraded_mode_until` | degraded mode deadline | `_build_runtime_rate_control` |
| `degraded_mode_reason` | degraded mode activation reason | `_build_runtime_rate_control` |
| `soft_max_requests_per_second` | request-window limit from settings | read by can/remaining/wait helpers |
| `soft_max_quotes_per_tick` | quote-per-tick limit from settings | read by quote helpers |
| `backoff_seconds_on_rate_limit` | EGW00201 base backoff seconds | read by `_api_budget_note_rate_limit` |

Additional runtime keys written later:

| Key | Meaning |
| --- | --- |
| `last_sell_watch_total_holdings` | latest holdings count used by runtime pressure and sell interval calculation |
| `last_sell_watch_partial` | whether latest observed sell watch was partial |
| `consecutive_sell_watch_partial_cycles` | consecutive sell watch partial streak |
| `last_sell_watch_budget_plan_pressure_level` | latest sell watch budget pressure level |

Summary keys written into state/snapshots via `_summarize_api_budget_state`:

- `recent_request_count`
- `quotes_used_this_tick`
- `backoff_remaining_seconds`
- `transient_backoff_remaining_seconds`
- `rate_limit_hits`
- `last_rate_limit_source`
- `recent_rate_limit_hits_10m`
- `consecutive_backoff_cycles`
- `transient_error_hits`
- `last_transient_error_source`
- `recent_transient_error_hits_10m`

## Call Sites

### Main Loop

The main loop currently:

1. Builds `api_budget_state` once before the loop.
2. Resets `api_budget_state["quotes_used_this_tick"] = 0` on each tick.
3. Calls `_build_runtime_rate_control` to compute effective intervals and degraded mode.
4. Computes `sell_check_due` and `buy_scan_due`.
5. Calls `_build_scheduler_tick_decision`.
6. If the scheduler says to wait, logs remaining transient or EGW00201 backoff and sleeps `base_tick_seconds`.
7. Calls `run_cycle` with `scheduler_state`, `runtime_rate_control`, and the same mutable `api_budget_state`.

`run_once` uses the same state builder and runtime rate control, then passes the state into one full
cycle with a `RUN_ONCE_FULL_CYCLE` scheduler decision.

### run_cycle

`run_cycle` currently:

- Builds a budget state only if none was provided.
- Prints `_summarize_api_budget_state` in the engine schedule block.
- Writes `state["last_budget_status"]`.
- Checks transient backoff before heavy API calls and returns early on active transient backoff.
- Allows a due SELL watch to drain `API_BACKOFF_WAIT` before continuing.
- Returns early for active EGW00201 backoff when sell watch is not allowed to continue.
- Checks request budget before balance.
- Registers token, balance, quote, orderable, and order submit request usage.
- Preserves BUY scan request/quote reserve while SELL watch is evaluating holdings.
- Applies rate-limit note/backoff from 200-OK bodies and exceptions.
- Drains backoff before BUY execution tail orderable lookup if BUY scan hit a rate limit.
- Waits before orderable lookup and order submit to reserve follow-up request capacity.
- Updates rate-limit and transient recovery state in the final snapshot path.

## Timing-Sensitive Functions

These functions directly affect real wait/sleep/backoff timing and must not be first-move targets:

- `_wait_for_execution_request_budget`
- `_api_budget_min_wait_for_request_slot`
- `_build_scheduler_tick_decision`
- `_api_budget_note_rate_limit`
- `_api_budget_note_transient_api_error`
- `_api_budget_update_rate_limit_recovery_state`
- `_api_budget_update_transient_recovery_state`

Timing-sensitive call-site blocks:

- main loop `API_TRANSIENT_BACKOFF_WAIT` / `API_BACKOFF_WAIT` skip-cycle sleep
- run_cycle transient backoff early return
- run_cycle SELL watch due + `API_BACKOFF_WAIT` drain exception
- balance inquiry reserve wait
- SELL watch per-symbol pre-quote wait
- BUY scan pre-scan wait
- BUY execution tail backoff drain before orderable lookup
- orderable lookup request reserve wait
- order submit request reserve wait and existing `time.sleep(1.0)`

## Behavior That Must Be Preserved

- EGW00201 hit count accumulation and clean recovery reset conditions.
- EGW00201 `backoff_until` calculation, including exponential backoff and 600-second cap.
- Transient backoff hit counting, deadline calculation, and max cap.
- BUY scan request/quote reserve preservation during SELL watch.
- SELL watch due + `API_BACKOFF_WAIT` exception flow: drain remaining backoff, then permit SELL watch.
- Order submit preflight request reserve before the broker order call.
- Existing wait/sleep timing and placement.
- API call order across token, balance, quotes, orderable, and order submit.
- Scheduler tick decision semantics and returned keys.
- Runtime state keys and cycle snapshot fields that downstream tools read.

## Recommended Candidate Files

Future extraction should use existing project structure:

- `app/core/runtime_budget.py`
- `app/core/runtime_scheduler.py`

Do not introduce top-level modules for this split.

## Recommended Split Order

### 1. Pure summary/format helper

Move only a small pure calculation used by summary formatting.  Keep `app.main._function_name`
compatibility shims while tests still import helpers from `app.main`.

Candidate:

- a pure helper for computing summary backoff remaining seconds from a deadline

Risk:

- Low, if the helper accepts explicit `deadline` and `now` and does not mutate `api_budget_state`.

Tests:

- `PYTHONPATH=. pytest tests/test_runtime_rate_control.py`
- `python3 -c 'import app.main; print("app.main import OK")'`

### 2. Remaining/backoff seconds calculation helper

Move:

- `_api_budget_backoff_remaining_seconds`
- `_api_budget_transient_backoff_remaining_seconds`

Risk:

- Low to medium.  Many call sites use float seconds for actual sleeps, so rounding and type must stay identical.

Tests:

- `PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_buy_order_submit_budget.py`
- `PYTHONPATH=. pytest tests/test_sell_watch_rate_limit_body.py tests/test_rate_limit_200ok.py`

### 3. Rate-limit source parsing helper

Move:

- `_looks_like_rate_limit_error`
- `_rate_limit_source_from_response_body`
- `_rate_limit_source_from_exception`

Risk:

- Medium.  Source strings drive logs, snapshots, and backoff source attribution.

Tests:

- `PYTHONPATH=. pytest tests/test_main_rate_limit_body_sources.py tests/test_rate_limit_200ok.py tests/test_auth_token_rate_limit.py`

### 4. API budget state builder

Move:

- `_build_api_budget_state`

Risk:

- Medium.  Every default key must remain present, and settings attributes must be read with the same names.

Tests:

- `PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_buy_scan_budget_cap.py tests/test_buy_order_submit_budget.py`

### 5. Scheduler tick decision

Move later only after earlier helper shims are stable.

Candidate:

- `_build_scheduler_tick_decision`

Risk:

- High.  It changes whether `run_cycle` is called, whether BUY scan is deferred, and whether SELL watch may run during EGW00201 backoff.

Tests:

- `PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_sell_watch_budget.py tests/test_sell_watch_rate_limit_body.py`

### 6. wait/sleep 포함 helper

Move much later, after explicit timing tests are added or existing source-order tests are updated intentionally.

Candidate:

- `_wait_for_execution_request_budget`
- `_api_budget_min_wait_for_request_slot`

Risk:

- High.  These helpers print, call `note`, and call `time.sleep`; any movement must preserve wait placement and order-submit reserve semantics.

Tests:

- `PYTHONPATH=. pytest tests/test_buy_order_submit_budget.py tests/test_runtime_rate_control.py`
- Add/keep smoke import: `python3 -c 'import app.main; print("app.main import OK")'`

### 7. Mutation 포함 note/update helper

Move last.

Candidates:

- `_api_budget_note_rate_limit`
- `_api_budget_note_transient_api_error`
- `_api_budget_update_rate_limit_recovery_state`
- `_api_budget_update_transient_recovery_state`
- pruning helpers for hit windows

Risk:

- Highest.  These helpers mutate shared state, record bottlenecks, print diagnostics, and control whether EGW00201 hit counts reset or continue escalating.

Tests:

- `PYTHONPATH=. pytest tests/test_runtime_rate_control.py tests/test_main_bottleneck_hooks.py`
- `PYTHONPATH=. pytest tests/test_buy_scan_budget_cap.py tests/test_buy_order_submit_budget.py tests/test_sell_watch_rate_limit_body.py tests/test_main_rate_limit_body_sources.py tests/test_rate_limit_200ok.py tests/test_auth_token_rate_limit.py`

## Do Not Move Yet

The following should stay in `app/main.py` until a later, separate implementation plan:

- `_build_runtime_rate_control`
- `_build_scheduler_tick_decision`
- `_wait_for_execution_request_budget`
- `_api_budget_note_rate_limit`
- `_api_budget_note_transient_api_error`
- `_api_budget_update_rate_limit_recovery_state`
- `_api_budget_update_transient_recovery_state`
- any run_cycle block that calls `time.sleep`
- any orderable/order-submit reserve block
- any buy/sell cycle decision block

## First Implementation Scope Recommendation

The next code step, if approved, should be smaller than a module split:

- Add `app/core/runtime_budget.py`.
- Move one pure backoff/remaining-seconds helper or a tiny summary calculation helper.
- Keep `app.main._function_name` compatibility shims.
- Do not change scheduler decisions, wait/sleep placement, or mutation helpers.
- Run the targeted runtime budget tests before considering a second helper.

This keeps the first implementation aligned with the project rule: reduce `app/main.py` only through
behavior-preserving, test-backed seams, not by moving timing-sensitive trading flow all at once.
