# Operations Node Plan (Mac mini)

## Status

| Field | Value |
|-------|-------|
| Type | Architecture / planning document (Stage 0 deliverable) |
| Implementation | Stage 1 repo-side prep added — plist examples only, no jobs loaded |
| Drives | Near-term always-on operations-node work |
| Source | `agent_workflows/codex_macmini_operations_review.md` (not included in this source snapshot) |
| Related | `agent_workflows/agent_workflow_operationalization_plan.md` (not included in this source snapshot) (deferred to Stage 4+) |

> This is the **Stage 0** document the Codex review called for. It defines the
> process layout, scheduling, reports, notifications, safety boundaries, and
> rollback for treating a Mac mini as a **stable, deterministic operations node**
> — not a multi-agent host, not an LLM orchestration platform, not a scheduled
> slash-command runner. Build order: reliable sessions → deterministic postrun →
> lifecycle notifications → (only later, human-triggered) LLM report summaries.

---

## 1. Goal & non-goals

**Goal:** a dedicated machine that (a) starts/stops the mock trading session on a
weekday schedule, (b) keeps the Slack bot always-on, (c) runs the EOD postrun
pipeline deterministically and captures its output, and (d) emits Slack
lifecycle notifications — all with **zero autonomous decision-making**.

**Non-goals (now and foreseeable future):**
- No autonomous multi-agent runtime on the node.
- No scheduled LLM shell execution (cron + Claude Code = no).
- No agent-triggered session start/stop/restart.
- No automatic config/order/strategy changes by any agent or scheduled script.
- No broker API calls from agents or scheduled scripts (except `run_session.sh`'s
  normal mock-session operation).
- No automatic deployment or `git pull` in Stages 0–1.
- No Telegram until Slack coverage is complete and proven.

---

## 2. Process layout

Three independent long-lived/triggered processes, each isolated so one failing
does not take down the others:

| Process | Entry point | Lifetime | Notes |
|---------|-------------|----------|-------|
| Trading session | `scripts/run_session.sh` → `python -m app.main` | Weekday market window | Mock by default (`KIS_ENV=mock`). Protected by the per-account `fcntl` lock (`acquire_app_main_lock()`, `app/core/session_lock.py`) — launchd must never bypass it, so a double-trigger fails fast instead of double-running. |
| Slack bot | `scripts/run_slack_bot.sh` → `app/notifications/slack_bot.py` | Always-on (`KeepAlive`) | Must run **standalone**, not as a child of `run_session.sh`, so it answers 24/7 independent of session state. `run_session.sh` still has a legacy optional supervisor path; the ops-node session job must set `SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE=false` and let the slack-bot plist own the bot. |
| EOD postrun pipeline | `scripts/eod_wrapper.sh` → `scripts/check_eod.sh <ACCOUNT> [DATE]` | Triggered after close | `check_eod.sh` chains the EOD diagnostics (incl. ML data accumulation). The wrapper captures output to `reports/` and sends a best-effort deterministic Slack summary from `app.tools.eod_report_summary`. |

**Account/env:** each process sources operator-managed environment (never the
committed, protected `.env`/`.token_cache.json`). `KIS_ENV` stays `mock` on this
node until a separate live gate (`/deploy-checklist` + human approval) is cleared.

---

## 3. Scheduling model (launchd)

Specifications plus repo examples — the operator still copies, reviews, and
`launchctl load`s these manually (Stage 2). The node's timezone must be set to
**Asia/Seoul (KST)** so calendar intervals are unambiguous.

| Plist (proposed) | Triggers | Schedule | Key keys |
|------------------|----------|----------|----------|
| `launchd/com.kis-trader.session.plist.example` | `run_session.sh` | 08:55 KST, weekdays | `StartCalendarInterval` per weekday; `RunAtLoad = false`; sets `SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE=false` |
| `launchd/com.kis-trader.slack-bot.plist.example` | `run_slack_bot.sh` | On boot, always-on | `KeepAlive = true`, `RunAtLoad = true` |
| `launchd/com.kis-trader.eod-pipeline.plist.example` | `eod_wrapper.sh mock_12345678_01` | 16:00 KST, weekdays | `StartCalendarInterval` per weekday; resolves latest date automatically |

**launchd safety constraints:**
- Plists must invoke **only** the three entry points above — never a tool with
  write access to `config/`, `app/`, or `scripts/`.
- The session plist must set `SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE=false` so the
  dedicated slack-bot plist owns bot lifetime.
- No `git pull`, no `restart_session.sh`, no `app.main` tool other than the normal
  session, in any plist.
- Each plist writes `StandardOutPath`/`StandardErrorPath` into `reports/launchd/`
  (gitignored) for post-hoc debugging.

---

## 4. Reports directory & retention

- **Path:** `reports/` at repo root (operational output, **append-only mindset**).
- **Gitignore:** `/reports/` is ignored at repo root. Git history must not
  accumulate operational noise.
- **Layout:**
  - `reports/eod_YYYYMMDD.txt` — captured `check_eod.sh` output.
  - `reports/launchd/{job}.out|err` — launchd stdio.
- **Report summaries:** `python -m app.tools.eod_report_summary reports/eod_YYYYMMDD.txt`
  prints the deterministic postrun summary used by the Slack notification.
- **Retention:** operator-pruned (e.g., keep 90 days). Curated findings are
  **manually promoted** into `docs/` — never auto-committed.

---

## 5. Notifications

Reuse the existing Slack layer (`app/notifications/slack.py`) — channels are
env-configured, so no secrets in code:

| Env var | Channel role | Lifecycle use |
|---------|--------------|---------------|
| `SLACK_CHANNEL_PROJECT_OPERATOR` | operator/lifecycle | session start/stop; default fallback for any channel left unset |
| `SLACK_CHANNEL_PROJECT_ORDERS` | order events | (existing session output) |
| `SLACK_CHANNEL_PROJECT_BACK_TESTER` | EOD postrun | `postrun.complete` / `postrun.failed` |
| `SLACK_CHANNEL_PROJECT_ACTIVATOR` | KIS rate-limit | `kis_rate_limit` pressure alerts |
| `SLACK_CHANNEL_PROJECT_BOTTLENECKS` | bottleneck/health | `repeated_bottleneck`, `stale_snapshot`, `fallback_spike`, `cash_budget_shortage` |
| `SLACK_CHANNEL_PROJECT_SUMMARY` | daily summary | (existing) |

> **Routing note:** each event resolves to its primary channel env, falling back to
> `SLACK_CHANNEL_PROJECT_OPERATOR` when the primary is unset (`_resolve_channel`). So
> EOD/postrun (→ back-tester) and rate-limit (→ activator) land in the operator channel
> until the operator creates those channels and sets `SLACK_CHANNEL_PROJECT_BACK_TESTER`
> / `SLACK_CHANNEL_PROJECT_ACTIVATOR`.

**Lifecycle event list (Stage 3):**
1. `session.started` — emitted by `run_session.sh` after the lock is acquired.
2. `session.stopped` — clean vs error status.
3. `postrun.complete` — deterministic summary from `eod_wrapper.sh`.
4. `postrun.failed` — non-zero `check_eod.sh` exit → deterministic failure summary.
5. *(optional)* `health.degraded` — from a lightweight market-hours health check.

Errors continue to route to `#trading-alerts` per the project convention.

---

## 6. Session lifecycle safety checks

- **Single-active invariant:** rely on `acquire_app_main_lock()` (per-account
  `fcntl` lock). launchd never removes/bypasses it; a missed-cleanup lock is
  released by the OS on process exit.
- **Mock assertion at start:** `run_session.sh` must confirm `_resolve_kis_env()`
  resolves to `mock` (via `app/auth/settings.py`) before trading; abort + alert if
  not, until the live gate is explicitly cleared.
- **No auto-restart:** a crashed session is **not** auto-restarted (Stage 0–2).
  The operator inspects and restarts manually (`restart_session.sh` is never
  scheduled).
- **No auto-pull:** code on the node changes only by an observed, manual operator
  action — never a pre-session `git pull`.

---

## 7. Manual installation procedure (Stage 2)

1. Set node timezone: `sudo systemsetup -settimezone Asia/Seoul`.
2. Provision operator environment (KIS mock credentials, `SLACK_*` channel ids)
   via a sourced profile outside the repo — never edit the committed `.env`.
3. Create launchd output directory:
   `mkdir -p $KIS_TRADER_ROOT/reports/launchd`.
4. Review the repo examples under `launchd/*.plist.example`, then copy them
   without the `.example` suffix into `~/Library/LaunchAgents/`.
5. `plutil -lint ~/Library/LaunchAgents/com.kis-trader.*.plist`.
6. `launchctl load` each plist; verify with `launchctl list | grep kis-trader`.
7. Dry-run each entry point once by hand before relying on the schedule.

---

## 8. Rollback / disable (per job)

| Action | Command |
|--------|---------|
| Disable one scheduled job | `launchctl unload ~/Library/LaunchAgents/com.kis-trader.<job>.plist` |
| Stop the session immediately | `scripts/stop_session.sh` (manual) |
| Stop the Slack bot | `scripts/stop_slack_bot.sh` |
| Full node disable | `launchctl unload` all three plists |
| Emergency mock revert | ensure `KIS_ENV=mock` + unset live-scoped vars in the operator profile, then restart (see CLAUDE.md Emergency) |

Every scheduled job must be independently disable-able without touching the
others.

---

## 9. Safety boundaries

| Category | Actions |
|----------|---------|
| **Allowed now** | scheduled `run_session.sh` (weekdays, mock), scheduled `check_eod.sh` (after close), always-on `run_slack_bot.sh`, manual `stop_session.sh`, `tail`/`head` log reads, Slack lifecycle notifications |
| **Allowed later, human-approved** | `git pull --ff-only` before session (Stage 3+), weekly ML retraining, market-hours health-check, LLM report interpretation (Stage 4, human-triggered) |
| **Forbidden now** | automatic `git pull`, auto-restart on crash, LLM-triggered actions, config/strategy edits, any broker API call outside the session, scheduled `restart_session.sh` |
| **Forbidden (likely permanent)** | LLM-scheduled execution, autonomous config/strategy changes, multi-agent orchestration on the live ops node, broker calls from agents, agent-triggered session control, automatic deployment from CI |

---

## 10. Staged sequence & gates

| Stage | Deliverable | Gate to advance |
|-------|-------------|-----------------|
| **0** | **This document** | — |
| 1 | Repo-side prep complete: `check_eod.sh` / `run_slack_bot.sh` side-effect audit, `scripts/eod_wrapper.sh`, deterministic EOD summary parser, launchd plist examples, `/reports/` gitignore | Operator dry-runs all scripts on the node |
| 2 | launchd plists + manual install procedure | 5 consecutive trading days: sessions start/stop on schedule, bot up 24/7, reports appear |
| 3 | Slack lifecycle notifications | 2 weeks of correct notifications for all lifecycle events |
| 4 | *Optional* human-triggered LLM summaries of `reports/eod_*.txt` | LLM interpretation demonstrably adds value over the deterministic report |
| 5 | *Optional* `/ops-review` `/config-risk` slash commands (revisit the agent-workflow plan) | Stage 4 shows recurring LLM value |

**Revisit the agent-workflow operationalization plan only after 2–3 months of
stable Mac mini operations — not before.**

---

## 11. Open follow-ups (Stage 1 entry items)

- [x] Audit `check_eod.sh` scheduled-shell side effects: it invokes deterministic
      postrun tooling and writes expected generated outputs under `logs/`; the
      wrapper captures operator reports under `reports/`.
- [x] Confirm `run_slack_bot.sh` can run standalone: it is already a
      single-instance supervisor with pid/log files and restart backoff.
      Ops-node launchd must disable the legacy `run_session.sh` child-supervisor
      path via `SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE=false`.
- [x] Add `/reports/` to `.gitignore`.
- [x] Author `scripts/eod_wrapper.sh` (output capture + best-effort Slack summary).
- [x] Add deterministic EOD report summarizer (`app.tools.eod_report_summary`).
- [x] Add launchd plist examples under `launchd/` (examples only; not loaded).
- [ ] Operator dry-run on the Mac mini with real node environment.
- [x] Confirm all postrun tools respect data-access tiers (static audit, 2026-06-08):
      every `check_eod.sh` step (`eod_health_check`, `export_signal_dataset`,
      `analyze_signal_dataset`, `export_signal_outcome_dataset`,
      `analyze_signal_outcome_dataset`, `postrun_diagnostics`, `analyze_core_bucket`,
      `export_ml_candidate_dataset`, `build_ml_labels`) reads only **account/date-scoped
      local artifacts** (`candidate_outcomes_{account}_{date}`, `cycle_snapshots_{account}`,
      `signal_*_{account}_{date}`) via **line-streaming** JSONL helpers — no whole-file
      slurp, no recursive/broad `logs/`+`data/` reads, no reads outside intended
      artifacts. `detect_latest_market_date` globs filenames only (no content read).
      **Node-scale note (efficiency, not a tier violation):** `cycle_snapshots_{account}.jsonl`
      is a single non-rotated growing file iterated in full each EOD; bounded operationally
      by the `log_data_retention_plan.md` retention policy (operator-pruned). Revisit a
      date-bounded snapshot read only if multi-month retention makes the full iteration
      costly on the node.
