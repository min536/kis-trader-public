---
Purpose: Step 3 of the post-Phase-4 roadmap — bridge the pre-existing research
  pipeline's real outputs (backtest evaluations, session summaries) into autotuner
  evidence, and compose them into a validated DRAFT shadow proposal.
Read when: wiring real evidence into autotuner proposals, or extending the
  candidate/evidence collection loop.
Status: DONE (read-only / proposal-generation only; no runtime application).
Date: 2026-06-04
---

# Step 3 — Real-Evidence Bridge + Shadow-Eval Entrypoint

Builds on the Step 1 reconciliation decision (layered-by-domain, reuse not rewrite):
instead of a second backtest/generation stack, Step 3 *bridges* the existing
research pipeline's real artifacts into the autotuner's evidence entries, then
composes a candidate change + that evidence into a schema-valid `mode=shadow`
draft proposal. Everything here is read-only and generates proposals only — it
never approves and never applies anything to a running cycle.

## Modules

### `app/autotuner/evidence_sources.py` (pure mappers + bounded readers)
- `backtest_result_to_evidence(eval_result, *, source_id, source_path, generated_at)`
  — maps a `run_proposal_backtest` evaluation dict (`verdict`, `deltas{sharpe,
  max_drawdown,total_return}`) into a `backtest` proxy-evidence entry. `verdict=="pass"`
  → `confidence="medium"`, else `"low"`. Honest `limitations` (proxy) preserved (D4).
- `load_backtest_evidence(eval_path, *, source_id=None, generated_at=None)`
  — reads a `research/evaluations/*.json` (non-restricted path), derives `source_id`
  from `candidate_run_id`/`proposal_id`, `generated_at` from file mtime; rejects a
  non-object eval with `ValueError`.
- `live_log_summary_to_evidence(summary, *, ...)` — maps a session-summary dict
  (`session_date`, `cycles`, `buys`, `sells`) into a `live_log` evidence entry.
- `load_live_log_evidence(summary_path, *, ...)` — reads a small session-summary JSON
  and shapes the `live_log` entry. (Raw `logs/` are never slurped — only an
  already-summarized artifact is read, honoring the data/logs/results read limits.)

### `app/tools/autotuner_shadow_eval.py` (thin CLI)
Composes a proposed cadence change + one or more bridged evidence sources into a
validated DRAFT shadow proposal under `_workspace/autotuner/proposals/`:

```
python -m app.tools.autotuner_shadow_eval \
    --parameter buy_scan_shallow_top_k --from-value 10 --to-value 12 \
    --reason "shadow eval of shallow_top_k" \
    --backtest-eval research/evaluations/eval_atp_...json \
    --live-log-summary research/sessions/session_20260603.json
```

- Requires ≥1 evidence source (a shadow proposal with no evidence is refused).
- Self-validates via `build_shadow_proposal` → `validate_proposal`; an out-of-bounds
  or non-whitelist change is refused, nothing is written.
- Output is a **draft** (`mode=shadow`, `status=pending_review`). It must still pass
  the human approval gate (`app/tools/autotuner_approve.py`, Step 2) before any
  runtime reader could ever consume it, and even then only in `mock` with the
  default-OFF flag on.

## Pipeline position

```
real backtest eval (research/evaluations/)  ─┐
real session summary (research/sessions/)   ─┤→ evidence_sources bridge
                                              │
candidate cadence change (human/loop)  ──────┴→ autotuner_shadow_eval
        → DRAFT shadow proposal (_workspace/autotuner/proposals/)
        → autotuner_approve (human gate, Step 2)  → approved_low_risk
        → runtime_activation (mock, default-OFF, safer-of)  [Phase 3]
```

## Trust boundary / known limitation (honest)

- **Backtest is proxy evidence only (D4).** A `backtest` entry never alone qualifies a
  proposal for live; an `approved_*` proposal still requires ≥1 `live_log` cross-validation.
- **The candidate change is supplied explicitly** (by the loop/human), not derived from the
  backtest. The pre-existing `run_proposal_backtest` varies *strategy* params (RSI families),
  not autotuner *cadence* params — so for cadence params the directly-relevant evidence is the
  reconstructed `live_log`, with the backtest serving as supporting proxy. A cadence-aware
  backtest is a deeper follow-up (Step 4+), not required to land the bridge.
- No broker calls, no runtime application, no trading-critical surface touched.
