---
date: 2026-06-29
category: config
tags: [live, kis-env, credential-profile, buy-quote-lane, lane-pipeline, cycle-error]
symptom: Bot process healthy/heartbeat success, but every trading cycle aborts with CYCLE_ERROR and places zero orders
root_cause: Live read-only BUY quote lane builds a live credential profile, but resolve_kis_credential_profile had no base-URL fallback and KIS_BASE_LIVE_URL was unset
---

# Live BUY quote lane aborts every cycle when KIS_BASE_LIVE_URL is unset

## Symptom

On 2026-06-29 the bot looked healthy at the process/heartbeat level but did
nothing all session:

- `logs/kis_trader_live_snapshot.heartbeat` → `status=success`,
  `consecutive_failures=0` (worker loop alive).
- But **9/9 cycles** in `logs/app_stdout_20260629.log` ended in:

  ```
  [ERROR] cycle critical error: KIS live credential profile is incomplete: KIS_BASE_LIVE_URL
  cycle result | session=REGULAR | action=CYCLE_ERROR | buy=not_run | error=1
  ```

  Every cycle: `action=CYCLE_ERROR`, `executed_order_count=0`.

The heartbeat reads `success` only because the **worker loop** stays alive — it
does not reflect that each *trading cycle* inside it is crashing. Always check
`cycle_stats_*.jsonl` / `action=` in stdout, not just the heartbeat.

## Root Cause

Full chain:

1. Commit `ab3e366` enabled the in-process **live read-only BUY quote lane** in
   the regular-session profile (`config/regular_session.env`:
   `BUY_SCAN_QUOTE_KIS_ENV=live`). "No credentials added."
2. With the quote env = `live` (≠ the mock execution env), `run_cycle()` →
   `build_buy_scan_quote_context` (`app/scanner/quote_account.py:161`) calls
   `resolve_kis_credential_profile("live", allow_generic_fallback=False)` to
   mint a read-only live quote token.
3. `resolve_kis_credential_profile` (`app/auth/settings.py`) required all three
   of live app key / app secret / **base URL**. The environment had the live
   app key + secret but **no live base URL** → it raised
   `KIS live credential profile is incomplete: KIS_BASE_LIVE_URL`, which
   bubbled up as the cycle-level `CYCLE_ERROR` (before any SELL/BUY executed).
4. Why the base URL was simply never set: until this lane moved in-process, the
   only consumer of the live host was the snapshot worker
   `scripts/live_snapshot.py`, which resolves it with a **default fallback**
   (`LIVE_BASE_URL_DEFAULT = "https://openapi.koreainvestment.com:9443"`,
   `_first_env(..., default=...)`). So the worker silently succeeded without the
   env var, the operator never needed to set it, and the two code paths had
   **diverged**: worker defaulted, main app hard-failed.

Note this is paper/live-relevant: the lane is **read-only / quote-only** with
live-scoped credentials; orders remain on the mock execution account
(`get_settings().base_url`), a separate path that never calls
`resolve_kis_credential_profile`. The mock-default safety guarantee was not
affected — env resolution (`_resolve_kis_env`) is independent of this fix.

## Fix

Commit `b19fec6` (code-only):

- `app/auth/settings.py`: added module constant
  `LIVE_BASE_URL_DEFAULT = "https://openapi.koreainvestment.com:9443"` and a
  **live-only** base-URL fallback inside `resolve_kis_credential_profile`,
  placed after both fallback branches and before the missing-field check:

  ```python
  if normalized_env == "live" and not base_url:
      base_url = LIVE_BASE_URL_DEFAULT
  ```

  `app_key` / `app_secret` are **never** defaulted — live still requires
  explicit live credentials. Mock still fails loud without `KIS_BASE_MOCK_URL`.
- `scripts/live_snapshot.py`: imports `LIVE_BASE_URL_DEFAULT` from
  `app.auth.settings` instead of redefining the literal — single source of
  truth so the two paths can no longer drift (the drift is what masked this).

Confined to `resolve_kis_credential_profile` + the constant;
`_resolve_kis_env`, `get_settings`, the `Settings` dataclass, and all
order/SELL paths untouched.

## Verification

- New integration test reproduces the exact failing path:
  `tests/test_buy_scan_quote_account.py::BuyScanQuoteAccountTests::test_live_quote_env_defaults_base_url_when_unset`
  — with `KIS_BASE_LIVE_URL` unset, `build_buy_scan_quote_context` now returns a
  `read_only_quote_account` context instead of raising.
- Existing guard still holds: `test_live_quote_env_requires_live_scoped_credentials`
  still raises when live **keys** are missing.
- Run:
  ```bash
  .venv/bin/python -m pytest tests/test_auth_settings.py tests/test_buy_scan_quote_account.py -q
  ```
  Full suite at fix time: **2708 passed, 596 subtests passed**.
- Operationally: the fix only takes effect after a **session restart** (the
  running process loaded the old module). After restart, no env var is needed —
  the default fills in; cycles should show `action=BUY/SELL/NO_ACTION`, not
  `CYCLE_ERROR`.

## Prevention

- Single source of truth for the live host (this fix) — enforced by
  `test_live_base_url_default_single_source` (identity check between
  `app.auth.settings` and `scripts.live_snapshot`).
- Health checks must look at **cycle outcome** (`action=` / `cycle_stats` /
  `executed_order_count`), not just the heartbeat `status=success`. A green
  heartbeat with 100% `CYCLE_ERROR` is the trap here.
- When enabling a live lane via config (`BUY_SCAN_QUOTE_KIS_ENV=live`), confirm
  the full live credential profile resolves (key + secret + base URL) — or rely
  on the now-consistent base-URL default.
- Consider a startup-sanity check that resolves the configured quote-lane
  credential profile once and fails fast at launch rather than per-cycle.
