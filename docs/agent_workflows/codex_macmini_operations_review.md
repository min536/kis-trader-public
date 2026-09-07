# Codex Review: Mac Mini Operations Node First

## Status

| Field | Value |
|-------|-------|
| Type | Reference document — planning only |
| Implementation | None performed |
| Supersedes | Nothing by itself |
| Purpose | Sequence future work |
| Related | [`agent_workflow_operationalization_plan.md`](agent_workflow_operationalization_plan.md) |

---

## Executive Summary

A Mac mini should first be treated as a **stable, deterministic operations node** — not a multi-agent host, not an LLM orchestration platform, and not a slash-command runner.

The correct build order is:

1. Reliable session hosting with launchd scheduling.
2. Deterministic read-only postrun scripts with output capture.
3. Slack/Telegram notification summaries from script output.
4. Only then: optional LLM-assisted interpretation of generated reports, human-triggered.
5. Only much later, if justified by real operational need: limited offline agent review via slash commands.

Concretely: **`check_eod.sh` running reliably at 16:00 KST on a dedicated Mac mini is worth more than six slash commands on a shared laptop.**

---

## Priority Decision

**Operational stability first. Agent workflow operationalization later.**

The existing [`agent_workflow_operationalization_plan.md`](agent_workflow_operationalization_plan.md) is well-reasoned and remains useful. Its recommended permission scopes (`ops-readonly`, `ops-readonly-with-diff`) and output contracts are sound design. But it is premature until the Mac mini operations layer is proven.

Why:
- LLM-assisted reviews produce no value if the underlying scheduled jobs are unreliable.
- Establishing the Mac mini ops node takes priority because it solves real, current problems: missed sessions, interrupted sessions, missed postrun analysis, and Slack bot downtime.
- Agent workflows solve a different problem — interpretation quality — which is a later concern.

**The agent workflow plan should be revisited after 2–3 months of stable Mac mini operations. Not before.**

---

## Recommended Sequence

### Stage 0: Mac mini operations architecture document

Create `docs/operations_node_plan.md` (or `docs/agent_workflows/operations_node_architecture.md`).

Define:
- Process layout (session, Slack bot, postrun pipeline).
- Scheduling model (launchd plists, weekday constraints).
- Notification path (Slack channels, event triggers).
- Report storage (`reports/` gitignored directory).
- Safety boundaries (what the node must never do automatically).
- Rollback/disable procedure.

**Do not create this file now. It is the minimal next planning step (see Section 9).**

### Stage 1: Deterministic offline/read-only ops scripts and safety verification

- Audit `check_eod.sh` for any side effects (network calls, writes outside `data/` and `reports/`).
- Audit `scripts/run_slack_bot.sh` for standalone operation (currently a child of `run_session.sh`).
- Create `scripts/eod_wrapper.sh` — captures `check_eod.sh` output to `reports/eod_YYYYMMDD.txt`, sends a short Slack summary on completion or failure.
- Verify all postrun tools respect data access tiers (no Black/Red tier full reads).
- No `git pull` automation. No auto-restart. No config changes.

**Gate:** All scripts run successfully on the Mac mini in dry-run mode before enabling scheduling.

### Stage 2: launchd templates and manual installation procedure

- Create `launchd/com.kis-trader.session.plist` — starts `run_session.sh` at 08:55 KST on weekdays.
- Create `launchd/com.kis-trader.slack-bot.plist` — runs `run_slack_bot.sh` on boot, `KeepAlive = true`, independent of session.
- Create `launchd/com.kis-trader.eod-pipeline.plist` — runs `eod_wrapper.sh` at 16:00 KST on weekdays.
- Document manual installation steps in `docs/operations_node_plan.md`.
- Manual `launchctl load` by operator. No automated deployment.

**Gate:** 5 consecutive trading days with all sessions starting and stopping as scheduled, Slack bot responding 24/7, postrun reports appearing in `reports/`.

### Stage 3: Slack/Telegram notification summaries

- Add "session started" Slack notification (via `notify_session_event.sh` or an addition to `run_session.sh`).
- Add "session stopped" notification with clean/error status.
- Add "postrun complete" notification with key metrics (from `eod_wrapper.sh`).
- Add "postrun failed" alert.
- Optional: "health degradation detected" alert from a lightweight market-hours health check.

**Gate:** Operator receives correct notifications for all session lifecycle events for 2 weeks.

### Stage 4: Optional LLM summaries from generated reports

- Operator SSHes into Mac mini and invokes Claude Code manually.
- Claude Code reads `reports/eod_YYYYMMDD.txt` and produces a structured interpretation.
- Result is either reviewed in terminal or manually posted to Slack/docs.
- **Never scheduled. Never automated. Always human-triggered.**

**Gate:** Demonstrate that LLM interpretation adds value beyond what the deterministic reports already show.

### Stage 5: Optional slash commands / limited offline multi-agent review

- Revisit [`agent_workflow_operationalization_plan.md`](agent_workflow_operationalization_plan.md).
- Consider implementing `/ops-review` and `/config-risk` slash commands if Stage 4 demonstrates recurring LLM value.
- Permission scopes (`ops-readonly`, `ops-readonly-with-diff`) from the existing plan remain the right design.
- Multi-agent orchestration only if: multiple accounts, multiple operators, or analysis load that single-agent can't handle. None of these conditions currently exist.

---

## Key Codex Conclusions

1. **Full multi-agent orchestration should not be first.** Single operator, single account, single strategy — no scaling problem exists.

2. **Slash commands should not be first** if the goal is Mac mini always-on operations. Slash commands assist LLM-triggered analysis; they do not solve the session-reliability or postrun-automation problem.

3. **Deterministic scheduled jobs are the right foundation.** `check_eod.sh` already chains 11 tools reliably. Wrap it, schedule it, capture its output. This is the correct first step.

4. **Notification-only workflows are high value.** The existing Slack bot covers order events, bottlenecks, and daily summary. Adding lifecycle notifications (session start/stop, postrun complete) closes the main gap.

5. **LLMs should initially summarize generated reports only.** Not run tools directly. Not be scheduled. Not make decisions autonomously.

6. **Agents must not change config, strategy, orders, or live behavior.** `trading_guard.py` enforces this for Claude Code and Codex. launchd plists must not invoke any tool with write access to `config/`, `app/`, or `scripts/`.

7. **`check_eod.sh` must be audited before scheduling.** Confirm no live API calls, no writes to protected paths. The current implementation appears safe, but verify before automating.

8. **Automatic `git pull` before session should be delayed.** A failed or unexpected pull could change behavior before an operator-unobserved session. First-day-on-Mac-mini rule: no auto-pull. Consider only in Stage 3+.

9. **Reports should be written to append-only / gitignored operational paths.** Curated findings are manually promoted to `docs/`. Git history should not contain operational noise.

---

## Safety Boundaries

| Category | Actions |
|----------|---------|
| **Allowed now** | `run_session.sh` (scheduled, weekdays), `check_eod.sh` (scheduled, after close), `run_slack_bot.sh` (always-on), `stop_session.sh` (manual), `tail`/`head` reads of log files, Slack notifications for lifecycle events |
| **Allowed later with human approval** | `git pull --ff-only` before session (Stage 3+), preopen brief offline mode (Stage 2), ML retraining weekly (Stage 2), health-check cron during market hours (Stage 2), LLM report interpretation (Stage 4, human-triggered) |
| **Forbidden now** | Automatic `git pull`, automatic session restart on crash, LLM-triggered actions, config file modifications, strategy code edits, any broker API call outside session, running `restart_session.sh` on a schedule |
| **Probably forbidden permanently** | LLM-scheduled execution, autonomous config/strategy changes, multi-agent orchestration on the live ops node, broker API calls from agents, agent-triggered session start/stop, automatic deployment from CI |

---

## Relationship to the Saved Claude Code Plan

| Aspect | [`agent_workflow_operationalization_plan.md`](agent_workflow_operationalization_plan.md) | This document |
|--------|----------------------------------------------------------------------------------------|---------------|
| Primary concern | LLM-assisted operational review quality | Reliable always-on operations node |
| First deliverable | `/ops-review` and `/config-risk` slash commands | launchd session + postrun pipeline |
| LLM role | Central (but read-only) | Deferred to Stage 4 |
| Scheduling | Explicitly deferred | Core design concern |
| Infrastructure | Assumed present | The main problem to solve |
| Time horizon | After infrastructure is stable | Months 1–3 |

**The Claude plan is still useful for future LLM-assisted reviews.** Its permission scopes and output contracts are sound architecture that should be preserved. Nothing in this document rejects that work.

**The Codex plan (this document) should drive near-term Mac mini operations-node work.** The infrastructure must exist and be proven reliable before LLM-assisted workflows add value.

**The two plans should not be merged.** They solve different problems at different stages. Keep them separate and sequence them: infrastructure first (this document), then tooling on top (agent_workflow_operationalization_plan.md).

---

## Minimal Next Planning Step

Create:

```
docs/operations_node_plan.md
```

or

```
docs/agent_workflows/operations_node_architecture.md
```

This document should specify:
- Exact process layout on the Mac mini.
- launchd plist specifications (scripts, schedules, environment variables).
- `reports/` directory structure and retention policy.
- Manual installation procedure.
- Notification event list and Slack channel routing.
- Session lifecycle safety checks.
- Rollback and disable procedure for each scheduled job.

**This file does not exist yet. Creating it is the recommended Stage 0 action.**

---

## Non-Goals

The following are explicitly out of scope, now and for the foreseeable future:

- No live ops via oh-my-claudecode, tmux, or similar interactive agent terminals during market hours.
- No autonomous multi-agent runtime on the Mac mini.
- No scheduled LLM shell execution (cron + Claude Code = no).
- No agent-triggered trading session control (start, stop, restart).
- No automatic config, order, or strategy changes by any agent.
- No broker API calls from agents or scheduled scripts (except `run_session.sh`'s normal session operation).
- No automatic deployment or `git pull` in Stages 0–1.
- No Telegram integration until Slack coverage is complete and proven.
- No agent-to-agent communication or shared agent state.
