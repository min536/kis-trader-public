---
Purpose: Read-only drift audit between autotuner operator docs and current CLI flags.
Read when: editing W1 holdout/walk-forward docs or changing autotuner CLI flags.
Status: COMPLETE — no blocking operator-command flag drift found.
Date: 2026-06-11
---

# Autotuner CLI Drift Audit — 2026-06-11

Scope: compare current `app/tools/autotuner_*.py` command flags with the operator-facing
autotuner docs. This was a read-only audit of command surfaces: no runtime flags were
enabled, no proposals were approved, no broker calls were made, and no session was started.

## Current CLI Surface

| CLI | Current flags |
|---|---|
| `autotuner_operational_backtest` | `--candidates`, `--baseline`, `--session-summary`, `--sessions-dir`, `--out`, `--repo-head` |
| `autotuner_screen` | `--evals-dir`, `--out` |
| `autotuner_suggest` | `--screening`, `--baseline`, `--date`, `--mode`, `--out` |
| `autotuner_propose` | `--plan`, `--project-root`, `--out-dir`, `--allow-write`, `--notify-slack` |
| `autotuner_approve` | `--proposal-file`, `--approved-by`, `--out-dir`, `--ttl-hours`, `--expires-at`, `--high-risk`, `--approve` |
| `autotuner_status` | `--project-root`, `--env`, `--enabled`, `--high-risk`, `--enabled-high-risk`, `--live-shadow` |
| `autotuner_shadow_eval` | `--parameter`, `--to-value`, `--from-value`, `--reason`, `--proposal-id`, `--backtest-eval`, `--live-log-summary`, `--out-dir` |

## Operator Docs Checked

| Document | Result |
|---|---|
| `docs/runbook_autotuner_loop.md` | Current commands match CLI flags. Optional `--repo-head`, Tier B `--high-risk`, and live-shadow `--live-shadow` are documented. |
| `docs/autotuner_rollout_plan.md` | Current Stage 2/3/4a operator commands match CLI flags. `autotuner_status --enabled` relies on the CLI default `--env mock`, which is valid. |
| `docs/live_autotuner_phase5_candidate_suggest.md` | Current screen/suggest/operational-backtest examples match required flags. It omits optional `--repo-head`, but that is not an operator-blocking drift. |
| `docs/runbook_open_trading_api.md` | Cadence-screening handoff commands match current flags. The research-domain backtest commands are outside the autotuner CLI surface. |
| `docs/live_autotuner_step3_evidence_bridge.md` | `autotuner_shadow_eval` example matches current flags. |
| `docs/live_autotuner_step4_propose_cycle.md` | `autotuner_propose --plan/--project-root/--allow-write` examples match current flags. |
| `docs/live_autotuner_step6_e2e_observability.md` | `autotuner_status --enabled`, `--high-risk --enabled-high-risk`, and `--live-shadow` examples match current flags. |

## Findings

1. **No blocking drift in current operator commands.** The canonical runbook and rollout plan use flags that exist in the current CLIs.
2. **W1 holdout flags are planned, not current.** `docs/five_day_plan_replan_20260611.md` names future flags `--eval-sessions` / `--holdout-dir` for W1. Those flags do not exist yet in `app/tools/autotuner_operational_backtest.py`. Treat them as the W1 implementation target, not as current operator instructions.
3. **Optional provenance is only fully canonical in the main runbook.** `--repo-head` exists and is documented in `docs/runbook_autotuner_loop.md`; older phase docs that omit it are still valid but less complete.
4. **`--sessions-dir` is implemented but not emphasized.** The operational backtest CLI supports directory input in addition to repeated `--session-summary`. Current examples use the single-file form, which is valid.

## Follow-up Draft Inputs

- W1 docs should introduce `--eval-sessions` / `--holdout-dir` only in the same commit that implements those flags.
- When updating the W1 runbook, preserve the current invariant wording: offline/read-only, no approval, no runtime apply, no broker call.
- If a concise runbook refresh is desired before W1 lands, add optional notes for `--sessions-dir` and `--repo-head`; do not mention holdout flags as available yet.
