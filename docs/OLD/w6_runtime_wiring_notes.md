---
Purpose: W6 implementation/contract notes — backtest heartbeat + sentinel runtime hook + the
  deferred (operator-gated) run_cycle activation.
Read when: reviewing W6, activating the market-data quality sentinel in app/main.py, or operating
  the Slack backtest command across a bot restart.
Status: heartbeat + sentinel runtime hook DONE (off-main.py). run_cycle wiring DEFERRED by operator
  decision (2026-06-13) — main.py intentionally untouched.
Date: 2026-06-13
---

# W6 — Runtime wiring (heartbeat persistence + quality-sentinel hook)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 "W5+W6", §5
> (review gate), §8 items 1/5. **Invariant: fail-safe / alert-only — nothing here blocks trading.**

## ③ Backtest completion-monitor heartbeat persistence (DONE)

The Slack backtest completion monitor (`_start_backtest_completion_monitor`,
backtest_control.py (not included in this source snapshot)) is an in-memory daemon thread that
dies on bot restart — previously the restart path could only say "monitoring was lost". W6 persists
the monitor's state so the restart notice is richer:

- `build_backtest_heartbeat_record` / `write_backtest_heartbeat` / `read_backtest_heartbeat`
  (fail-safe, bounded read) / `clear_backtest_heartbeat` → `slack_backtest.heartbeat.json` beside the
  pid file (`{pid, started_at_epoch, max_runtime_sec, channel, thread_ts}`).
- The monitor writes the heartbeat at start (wall-clock `started_at`, so a *different* process can
  compute elapsed) and clears it in the thread's `finally` next to the pid cleanup; the write is
  fail-safe and never blocks the monitor.
- `render_backtest_orphan_monitor_notice(*, env, now_epoch=None)` enriches the restart message with
  `running for Ns` + `timeout in ~Ns` when a heartbeat is present; corrupt/missing heartbeat degrades
  silently. The slack_bot startup call site is unchanged (new param defaults).

Deferred: re-attaching a live monitor after restart (a non-child process yields no exit code via
`process.wait`, so only liveness-poll + "presumed complete" is possible) — a later enhancement.

## ④ Quality-sentinel runtime hook (DONE, off-main.py)

main_runtime_hooks.py (not included in this source snapshot) consumes the W5 sentinel:

- `build_market_data_quality_alert_text(report) -> str | None` — alert-only Slack text (None when ok).
- `run_market_data_quality_sentinel(snapshots, *, now_epoch, refresh_interval_seconds,
  snapshot_updated_at, checked_at, env, artifact_path, alert_sender)` — the run_cycle hook:
  evaluates quality, **always writes** the `data/runtime/market_data_quality.json` artifact
  (read-only observability), and emits an operator alert **only when** the env flag
  `MARKET_DATA_QUALITY_ALERTS_ENABLED` is truthy (default off → no surprise alerts). The alert
  sender is **injected** (no `app.reporting.telegram` coupling in this module). **Fully fail-safe:**
  a sentinel / artifact / alert-sender failure degrades to `None`, never disturbing the cycle.

## run_cycle activation — DEFERRED (operator-gated, 2026-06-13)

The literal `app/main.py` `run_cycle` call was **intentionally not landed** (operator decision: keep
the trading-critical surface untouched in this pass). The hook is committed and tested but **dormant**
— nothing calls it yet. To activate later (one import + one call, boundary-hook safe — no net-new
`def` in `main.py`):

1. `from app.notifications.main_runtime_hooks import run_market_data_quality_sentinel` and
   `from app.reporting.telegram import send_telegram_alert` (already imported in main.py).
2. Near the end of `run_cycle`, after `observed_market_snapshots` is populated and using the existing
   `live_snapshot_status()` for freshness:
   ```python
   run_market_data_quality_sentinel(
       observed_market_snapshots.values(),
       now_epoch=time.time(),
       refresh_interval_seconds=snapshot_info.get("refresh_interval_seconds"),
       snapshot_updated_at=snapshot_info.get("updated_at"),
       checked_at=get_korean_now().isoformat(),
       env=os.environ,
       alert_sender=send_telegram_alert,
   )
   ```
3. Alerts stay **off** until the operator sets `MARKET_DATA_QUALITY_ALERTS_ENABLED=1`. Recommend a
   `/risk-assessment` + the §5 thread-lifecycle review before merging the main.py edit.

## Verification

- 5 sentinel-hook tests (test_main_runtime_hooks.py (not included in this source snapshot)) + 5
  heartbeat tests (test_backtest_control.py (not included in this source snapshot)).
- Full suite **2,329 passed, 590 subtests** (exit 0). `app/main.py` untouched; no broker/order API touched.
