---
Purpose: W1 implementation/gate spec — walk-forward holdout eval + screening downgrade.
Read when: implementing or reviewing W1 (PR-1) in the autotuner operational-cadence path.
Status: SPEC + GATES. Direct (non-delegated) Core task per five_day_plan_replan_20260611 §3.
Date: 2026-06-13
---

# W1 — Walk-forward holdout eval + screening 강등 (PR-1)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 W1,
> the W1 runbook draft ([runbook_autotuner_w1_update_draft_20260611.md](runbook_autotuner_w1_update_draft_20260611.md)),
> and the CLI drift audit ([autotuner_cli_drift_audit_20260611.md](autotuner_cli_drift_audit_20260611.md)).
> Invariant: offline/read-only, proxy evidence only. No broker call, no session start, no
> runtime apply. Whitelist / Tier / bounds / approval TTL / mock-only activation / safer-of
> merge are **unchanged**. The downgrade is **conservative-only** — it can shrink the suggestion
> set, never promote a fail to pass.

## Why

The operational-cadence evaluator currently aggregates *all* session evidence into one
window and emits a single pass/fail verdict per candidate. A move that looks good on the
sessions it was tuned against can still degrade on unseen sessions. W1 adds an out-of-sample
(holdout) check: a candidate that passes in-sample but fails holdout is demoted so it cannot
reach the human-approval line as a clean `pass`.

## Structure map (surface — 4 files, all tracked / none NOT-mine)

| File | Change |
|------|--------|
| `app/autotuner/operational_backtest.py` | `build_operational_cadence_eval` gains optional `holdout_summaries` / `holdout_source_paths`. When holdout present, each evaluation gains additive `in_sample`, `out_of_sample`, `verdict_reconciliation`. Top-level `verdict` stays in-sample (downgrade is screening's job). |
| `app/autotuner/screening.py` | `evals_to_screening` reads `verdict_reconciliation`; when `downgrade_to` is set and the eval verdict is `pass`, the screening entry verdict becomes that value (`inconclusive`). Conservative-only; non-holdout evals (no reconciliation) are byte-identical to today. |
| `app/tools/autotuner_operational_backtest.py` | New `--holdout-summary` (repeatable) + `--holdout-dir` (directory) flags, mirroring the existing train `--session-summary` / `--sessions-dir`. Loaded via the existing bounded `load_session_summaries`. |
| docs (slice ④, separate commit) | Update canonical `runbook_autotuner_loop.md` + `live_autotuner_proposal_schema.md` from the draft. |

**Flag naming decision:** `--holdout-dir` (+ `--holdout-summary`), matching the runbook draft's
concrete examples and mirroring the existing train flags. The replan's tentative `--eval-sessions`
is superseded (ambiguous train-vs-eval); the drift audit defers the final name to the W1 tests.

**No new module:** the optional `app/autotuner/holdout.py` date-splitter is **not** built —
the operator pre-splits evidence into train/holdout directories (runbook design), so no
auto-split helper is needed. Less code, less risk.

## Reconciliation semantics (`verdict_reconciliation`)

Per candidate, evaluate the same change against the train window and the holdout window
(same whitelist, loaded once for the batch):

| in_sample | out_of_sample | status | agree | downgrade_to | screening result |
|-----------|---------------|--------|-------|--------------|------------------|
| pass | pass | `confirmed` | true | `null` | pass (kept) |
| pass | fail | `holdout_contradicts` | false | `inconclusive` | **inconclusive (dropped by suggest)** |
| fail | * | `in_sample_fail` | (out==fail) | `null` | fail (already dropped) |

`candidate_suggest.suggest_candidates` already drops any `verdict != "pass"`, so `inconclusive`
needs **no** downstream change.

## Slices (commits — code/docs separated)

1. **① holdout eval (code)** — `operational_backtest.py` + `tests/test_autotuner_operational_backtest.py`.
2. **② screening 강등 (code)** — `screening.py` + `tests/test_autotuner_screening.py`.
3. **③ CLI flags (code)** — `autotuner_operational_backtest.py` + CLI tests.
4. **④ docs** — canonical runbook + proposal schema (separate docs commit).

## Gates (deterministic)

- G1 **backward-compat**: every existing test in the two test files passes unchanged; a
  non-holdout `build_operational_cadence_eval` call produces no `in_sample`/`out_of_sample`/
  `verdict_reconciliation` keys; a non-holdout eval's screening output is byte-identical.
- G2 **conservative-only**: screening can only turn `pass`→`inconclusive`; never `fail`→`pass`
  nor `inconclusive`→`pass`. (adversarially verified)
- G3 **invariant preservation**: whitelist/Tier/bounds/step/safer-direction checks in
  `evaluate_candidate` unchanged; human gate (propose→approve→gated runtime) untouched.
- G4 **offline purity**: no new import of broker/runtime/network; `holdout` loaded via the same
  bounded reader; producer stays pure (git/clock injected, not read).
- G5 **holdout separation**: train and holdout observations are summarized from disjoint inputs;
  holdout never mutates the in-sample verdict/deltas.
- G6 **full suite green** (`.venv/bin/python -m pytest -q`).

## Adversarial verification outcome (2026-06-13)

A 6-agent adversarial pass (5 gate-skeptics each told to *refute* their gate + 1 completeness
critic) ran against commits `0f01c4c..e762d1c`. **All five gates HELD with high confidence
(none refuted; only nits)** — including byte-identity proof of the non-holdout path against the
pre-W1 module, an integration monotonicity sweep showing the W1 suggestion set is always a subset
of the pre-W1 set, and confirmation that a forged `verdict_reconciliation` with `downgrade_to=pass`
is inert (the screening guard short-circuits on `verdict == "pass"`).

Hardening applied from the critic's findings (commit after `e762d1c`):
- **[major] human-review trap** — `overall_verdict` is in-sample only, so a holdout-contradicted
  move could read `overall_verdict=pass`. Fixed by a top-level `walk_forward` summary + a CLI
  `walk-forward holdout: …` line + runbook caveat. (Machine path was already safe.)
- **[minor]** added coverage for the `in_sample_fail` branch and the `--holdout-summary` path.
- **[nit]** `in_sample` deltas/data_window are now copied, not aliased to the top-level evaluation.

### Follow-up (not wired now — guard for a future change)

`app/autotuner/evidence_sources.py::backtest_result_to_evidence` derives a verdict from
`result.get("verdict") or result.get("overall_verdict")` and is **holdout-blind**. It is **not**
currently in the operational-cadence eval→screening→suggest path, so this is latent, not a live
defect. If a future change ever routes operational-cadence evals through that mapper, it MUST
consult `verdict_reconciliation` / `walk_forward` (or the screening-downgraded verdict) so a
holdout-contradicted move is not re-promoted to `pass` there.
