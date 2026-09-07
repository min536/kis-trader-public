---
Purpose: Draft runbook update for W1 walk-forward/holdout autotuner screening.
Read when: implementing or reviewing W1 before editing `docs/runbook_autotuner_loop.md`.
Status: DRAFT — do not paste future flags into the canonical runbook until W1 code lands.
Date: 2026-06-11
---

# Draft — W1 Autotuner Runbook Update

This is a safe draft lane for the future W1 runbook update. It is intentionally
separate from the canonical operator runbook because the W1 holdout flags do not
exist yet in the current CLI.

Grounding:
- Current CLI drift audit: `docs/autotuner_cli_drift_audit_20260611.md`.
- Replan spec: `docs/five_day_plan_replan_20260611.md` W1.
- Current invariant: operational-cadence eval remains offline/read-only and only
  produces proxy evidence; approval and runtime apply remain separate human gates.

## Safe Text That Can Be Added Now

The current `autotuner_operational_backtest` CLI already supports repeated
single-file inputs and directory input:

```sh
.venv/bin/python -m app.tools.autotuner_operational_backtest \
    --candidates candidates.json \
    --baseline baseline.json \
    --sessions-dir research/sessions \
    --repo-head "$(git rev-parse HEAD)" \
    --out research/evaluations/eval_operational_YYYYMMDD.json
```

Notes for the canonical runbook:
- `--session-summary` can be repeated for explicit files.
- `--sessions-dir` loads `*.json` session summaries from a directory.
- `--repo-head` is optional provenance parity; it is injected by the caller and the
  evaluator does not call git itself.

## Future Text After W1 Lands

Use this section only after the W1 implementation adds holdout inputs and the tests
prove the exact flag names. The replan currently names `--eval-sessions` and
`--holdout-dir` as the intended operator surface.

### Step 0: Produce train/holdout operational-cadence evals

Prepare cadence candidates as before:

```json
[{"parameter":"buy_scan_shallow_top_k","to_value":8}]
```

Then split session evidence into an in-sample/train window and an out-of-sample
holdout window:

```sh
.venv/bin/python -m app.tools.autotuner_operational_backtest \
    --candidates candidates.json \
    --baseline baseline.json \
    --sessions-dir research/sessions/train \
    --holdout-dir research/sessions/holdout \
    --repo-head "$(git rev-parse HEAD)" \
    --out research/evaluations/eval_operational_holdout_YYYYMMDD.json
```

Expected output additions after W1:
- `evaluations[].in_sample` records the train-window verdict and deltas.
- `evaluations[].out_of_sample` records the holdout-window verdict and deltas.
- `evaluations[].verdict_reconciliation` explains whether train and holdout agree.
- Top-level provenance still includes `proxy_evidence=true`, `input_sessions`, and
  optional `repo_head`.

Screen and suggest as before:

```sh
.venv/bin/python -m app.tools.autotuner_screen --evals-dir research/evaluations --out screening.json
.venv/bin/python -m app.tools.autotuner_suggest \
    --screening screening.json --baseline baseline.json --date YYYYMMDD --out plan.json
```

Expected screening behavior after W1:
- If the train verdict passes but the holdout verdict fails, `autotuner_screen`
  must downgrade the candidate to a non-pass state such as `inconclusive`.
- The downgrade must be conservative only: it may reduce the set of suggestions,
  never promote a failing candidate to pass.
- Whitelist, Tier, bounds, approval TTL, mock-only runtime activation, and safer-of
  merge remain unchanged.

## Reviewer Checklist

- [ ] W1 CLI help/tests prove the final flag names before this draft is pasted.
- [ ] Canonical runbook keeps the no-broker/no-session-start/no-runtime-apply wording.
- [ ] Examples do not instruct the operator to enable a flag, approve a proposal, or
      start a session inside the eval/screen/suggest section.
- [ ] `autotuner_screen` downgrade wording is conservative and does not imply automatic approval.
- [ ] The docs commit is separate from the W1 code commit.
