# regular session observation checklist

## Purpose

This checklist is for the next regular session after the recent inventory documents and small helper
extractions.  Do not continue refactoring during this observation pass.  The goal is to confirm that
the current `app.main` behavior remains stable in a real regular session.

Do not push, commit, move helpers, change API budget mutation logic, change wait/sleep timing, change
scheduler behavior, or edit buy/sell cycle flow as part of this observation step.

## Current Change Set To Observe

The local `main` branch currently includes these recent commits:

| Commit | Summary | Observation focus |
| --- | --- | --- |
| `ae6f4c1` | Document workspace inventory | documentation only |
| `a77a91d` | Document app tools inventory | documentation only |
| `2baac7c` | Document app main split plan | documentation only |
| `cfc3491` | Extract main runtime Slack helpers | Slack event type/session status/market session helper shims |
| `721279e` | Document runtime budget split plan | documentation only |
| `7b9905a` | Extract runtime budget remaining helpers | backoff remaining-seconds helper shims |

Current push status at the time this checklist was written:

- Branch: `main`
- Upstream status: `main...origin/main [ahead 6]`
- Push was not performed.

## Before Regular Session

- Confirm the working tree is clean with `git status --short`.
- Confirm the branch with `git branch --show-current`.
- Confirm the app imports without running the trading loop:

```bash
python3 -c 'import app.main; print("app.main import OK")'
```

- Confirm no new refactor is mixed into the observation run.
- Confirm `.env` and token/cache files are not modified.
- Confirm this is a mock-investment operating context only.

## During Regular Session Observation

Watch the live console, runtime state, snapshots, Slack messages, and Slackbot responses for the
following items.

### Startup And Heartbeat

- `app.main` starts normally.
- Startup sanity output is present and does not block unexpectedly.
- Runtime mode, applied settings, and market session lines print normally.
- Snapshot heartbeat refreshes on schedule.
- Runtime state continues to update `last_budget_status`.
- No stale snapshot or fallback messages appear repeatedly.

### Slack And Slackbot

- Slack order submitted/accepted/rejected notifications still appear with the same observable payload shape.
- Slack event type mapping still matches order action:
  - submitted -> `order_submitted`
  - succeeded -> `order_accepted`
  - failed -> `order_rejected`
- Slack runtime status snapshot still includes session status text correctly.
- Market session status payload still includes session, order_allowed, reason, buy_block_action, and sell_block_action.
- Slackbot `status` works.
- Slackbot `health` works.
- Slackbot `orders today` works.
- Slackbot `bottlenecks` works.
- `orders today` reflects the local order log and does not unexpectedly omit today's submitted orders.

### Runtime Budget And Backoff

- `backoff_remaining_seconds` appears in runtime summaries and snapshots when EGW00201 backoff is active.
- `transient_backoff_remaining_seconds` appears when transient API/network backoff is active.
- Remaining-seconds values count down rather than staying stuck or becoming negative.
- Runtime budget summary still includes:
  - `recent_request_count`
  - `quotes_used_this_tick`
  - `rate_limit_hits`
  - `last_rate_limit_source`
  - `recent_rate_limit_hits_10m`
  - `consecutive_backoff_cycles`
  - `transient_error_hits`
  - `last_transient_error_source`
  - `recent_transient_error_hits_10m`
- EGW00201 rate-limit backoff is not excessive compared with previous regular sessions.
- EGW00201 hit count resets only after clean recovery.
- Transient API backoff does not block cycles longer than expected.
- SELL watch due plus `API_BACKOFF_WAIT` still drains backoff and permits the SELL watch exception path.
- Order submit preflight reserve still runs before the broker order call.
- Existing wait/sleep timing is not visibly changed.

### Trading Cycle Behavior

- Buy scan cadence and sell watch cadence look normal.
- SELL watch does not starve BUY scan reserve.
- BUY scan candidate count and deep-eval count are within expected ranges for the session.
- Order submitted/accepted/failed counts look plausible.
- Order log events preserve submitted, succeeded, and failed ordering.
- No unexpected HARD_STOP is triggered.
- `DATA_INSUFFICIENT` does not appear repeatedly without a real data-quality reason.
- T+2 PnL guard does not over-produce `DATA_INSUFFICIENT`.
- `indicator_quality` shadow payloads continue to appear in logs/snapshots.
- Score guard and indicator shadow payload collection flow remains visible.

### Warning Pressure

- Bottleneck warnings are not excessive.
- Rate-limit bottleneck warnings correspond to actual EGW00201 or budget pressure.
- Transient API warnings correspond to actual temporary API/network failures.
- No repeated import, attribute, or missing-helper errors appear after the helper extractions.

## Postrun Audit Sequence

After the regular session ends, run audit tools against the produced logs/snapshots.  Prefer read-only
analysis commands.  Do not change runtime code during the audit.

### Suggested Tool Order

1. `postrun_diagnostics`
2. `eod_health_check`
3. `compare_runtime_quality`
4. `recommend_score_guards`

Use the repository's current documented command forms or script entry points for these tools.  If an
entry point is ambiguous, inspect docs/scripts first rather than guessing and editing code.

### Postrun Items To Check

- HARD_STOP count and reason.
- `DATA_INSUFFICIENT` count and reason distribution.
- stale snapshot count.
- fallback count.
- EGW00201 rate-limit count.
- rate-limit source distribution.
- transient API/network error count.
- buy scan candidate count.
- buy scan deep-eval count.
- order submitted count.
- order accepted count.
- order failed count.
- Slackbot `orders today` count versus local order log count.
- `indicator_quality` shadow payload collection count.
- score guard recommended value changes.
- Runtime budget/backoff summary persisted in cycle snapshots.
- `backoff_remaining_seconds` and `transient_backoff_remaining_seconds` values look consistent with rate-limit/transient events.
- Bottleneck warning volume compared with previous regular sessions.

## Regression Signals From Recent Helper Extractions

### Slack Helper Regression Signals

- Slack order notifications disappear or change event type unexpectedly.
- Slackbot status reports a missing or malformed session.
- Market session payload is missing expected keys.
- Slack runtime snapshot stops updating even while app runtime state updates.

### Runtime Budget Helper Regression Signals

- `backoff_remaining_seconds` is missing from `last_budget_status`.
- `transient_backoff_remaining_seconds` is missing from `last_budget_status`.
- Remaining seconds are negative.
- Backoff drain sleeps for a visibly wrong duration.
- EGW00201 backoff is detected but snapshots show zero remaining seconds during active backoff.
- Transient backoff is detected but scheduler does not skip as expected.

## Do Not Do During Observation

- Do not split additional helpers.
- Do not move API budget mutation helpers.
- Do not move wait/sleep helpers.
- Do not move scheduler helpers.
- Do not edit `run_cycle`.
- Do not edit buy/sell cycle logic.
- Do not edit Slack notification payload code.
- Do not run live API calls just for testing outside the planned regular session.
- Do not push until explicitly instructed.
- Do not commit this observation checklist unless explicitly instructed.
