---
Purpose: Step 4 of the post-Phase-4 roadmap — the periodic propose-cycle tick that
  turns curated candidates + real evidence into DRAFT shadow proposals and a human
  notification, on a schedule, without ever approving or applying anything.
Read when: setting up periodic autotuner proposal generation, or extending the tick.
Status: DONE (read-only by default; write-gated; no runtime application).
Date: 2026-06-04
---

# Step 4 — Periodic Propose-Cycle

One **tick** of the human-in-the-loop tuning loop, built to be invoked every N
minutes during market hours by an external scheduler. It composes Step 3's evidence
bridge + shadow runner into a repeatable, safe cycle.

## `app/autotuner/propose_cycle.py`

`run_propose_cycle(plan, *, project_root, now, read_only=True, out_dir=None)`:

1. For each candidate in `plan`, collect bridged evidence (`backtest_eval` and/or
   `live_log_summary` via `evidence_sources`).
2. Shadow-evaluate it into a DRAFT `mode=shadow` proposal (`build_shadow_proposal`,
   which self-validates).
3. If `read_only=False`, persist the draft under
   `_workspace/autotuner/proposals/` (`write_proposal`). **Default is read-only
   (dry-run) — nothing is written.**
4. Return `{outcomes, written_paths, report}`. A candidate that fails (bad evidence,
   out-of-bounds, non-whitelist) is recorded as `refused` with a reason and **never
   aborts the tick** (collector-style fail-safe).

The `report` is a Markdown human-notification that lists the built drafts under
"awaiting human approval", lists refused candidates, and explicitly states that
nothing is approved or applied automatically.

### Hard boundaries
- **Never approves, never applies.** The tick only produces drafts + a notification.
- **Candidates come from a curated plan.** Auto-generating parameter moves is a
  deliberate non-goal — a human decides what to evaluate (keeps the loop honest).
- No broker calls; `_workspace/` writes only; no trading-critical surface touched.

## `app/tools/autotuner_propose.py` (periodic-tick CLI)

```
# dry-run (default): prints the notification report, writes nothing
python -m app.tools.autotuner_propose --plan plan.json --project-root .

# persist drafts under _workspace (still no approval / no apply)
python -m app.tools.autotuner_propose --plan plan.json --allow-write
```

`plan.json` is a JSON list of candidates:
```json
[{"proposal_id":"atp_20260604_shadow_0001","parameter":"buy_scan_shallow_top_k",
  "from_value":10,"to_value":12,"reason":"periodic shadow eval",
  "backtest_eval":"research/evaluations/eval_...json",
  "live_log_summary":"research/sessions/session_20260603.json"}]
```

## Scheduling (operator-owned)

The "every N minutes" trigger is **external** — an OS scheduler/cron or the operator
invokes the CLI. The agent never starts a long-running loop or `app.main`; this tool
is a single, deterministic, read-only-by-default tick. Notification delivery (Slack)
is deferred (D5); for now the report prints to stdout / can be logged.

## Pipeline position

```
curated plan + real evidence
   → autotuner_propose (tick, dry-run default)
   → DRAFT shadow proposals (_workspace, only with --allow-write)
   → autotuner_approve (human gate, Step 2)
   → runtime_activation (mock, default-OFF, safer-of, Phase 3)
```
Every arrow after the draft requires an explicit human action; none is automated.
