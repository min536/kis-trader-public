## Runbook: Live-Autotuner Human-in-the-Loop (mock)

**Owner:** trading ops | **Frequency:** As needed (per market session / when tuning)
**Last Updated:** 2026-06-07 | **Last Run:** —

### Purpose

Operate the autotuner loop in **mock**: turn curated candidate parameter moves +
real evidence into reviewed, human-approved runtime overrides that the live cycle
consumes — **safely**. Use this when you want to adjust Tier A scan/throttle cadence
(or Tier B buy-frequency) and have the bot pick it up *only* after your explicit
approval. Nothing here applies automatically; every apply is gated behind your
keystrokes plus a default-OFF flag, mock-only, and a safer-of merge.

> All commands run from the repo root with the project venv: `.venv/bin/python`.
> **Never** start `app.main` / `run_session.sh` from an agent session — that is an
> operator action done through your normal session launcher.

### Prerequisites

- [ ] Repo root, `.venv` present (`.venv/bin/python -m pytest -q` passes).
- [ ] `KIS_ENV=mock` (paper). Live is a **separate** human gate — out of scope here.
- [ ] Evidence on disk: a backtest evaluation JSON (e.g. `research/evaluations/eval_*.json`)
      and/or a session summary JSON. Backtest alone is proxy evidence (D4) — an
      *approved* proposal needs ≥1 `live_log` evidence.
- [ ] A local session summary JSON for operational-cadence screening
      (for example `research/sessions/session_YYYYMMDD.json`). This is read-only proxy
      evidence for candidate generation, not approval.
- [ ] A candidate **plan** file (`plan.json`), e.g.:
  ```json
  [{"proposal_id":"atp_20260605_shadow_0001","parameter":"buy_scan_shallow_top_k",
    "from_value":10,"to_value":12,"reason":"reduce shallow fan-out",
    "backtest_eval":"research/evaluations/eval_x.json",
    "live_log_summary":"research/sessions/session_20260604.json"}]
  ```
  Allowed Tier A params + bounds are in `config/autotuner_whitelist.yaml`. The tool
  refuses anything out of bounds / off-whitelist.

### Procedure

#### Step 0: Produce operational-cadence evals (offline/read-only)
Prepare `candidates.json` as a JSON list of whitelisted cadence moves:
```json
[{"parameter":"buy_scan_shallow_top_k","to_value":8}]
```

Then run the offline evaluator:
```
.venv/bin/python -m app.tools.autotuner_operational_backtest \
    --candidates candidates.json \
    --baseline baseline.json \
    --session-summary research/sessions/session_YYYYMMDD.json \
    --out research/evaluations/eval_operational_YYYYMMDD.json
```
**Expected result:** writes an autotuner-domain eval artifact with
`provenance.source=kis-trader-operational-cadence`, `proxy_evidence=true`, and
`evaluations[].changes[].parameter`. It is conservative: in-bounds Tier A/B only,
with pressure-reducing directions (interval/cooldown ↑, caps/K ↓). **No approval,
no apply, no broker call.**

> **Optional provenance parity:** add `--repo-head "$(git rev-parse HEAD)"` to stamp
> the producing commit into `provenance.repo_head`. `autotuner_screen` carries it into
> the screening audit link, so the approved bundle is traceable back to the exact code
> that produced the eval. Offline-pure — the wrapper injects the value, it does not call git.

Aggregate the evals and suggest a plan:
```
.venv/bin/python -m app.tools.autotuner_screen --evals-dir research/evaluations --out screening.json
.venv/bin/python -m app.tools.autotuner_suggest \
    --screening screening.json --baseline baseline.json --date YYYYMMDD --out plan.json
```

> **Optional walk-forward holdout (W1):** split the session evidence into an
> in-sample/train window and a disjoint out-of-sample/holdout window so a move that only
> looks good on the sessions it was tuned against cannot reach the approval line as a clean
> `pass`. Keep the train sessions on `--session-summary`/`--sessions-dir` and add the
> holdout window with `--holdout-summary` (repeatable) or `--holdout-dir`:
> ```
> .venv/bin/python -m app.tools.autotuner_operational_backtest \
>     --candidates candidates.json \
>     --baseline baseline.json \
>     --sessions-dir research/sessions/train \
>     --holdout-dir research/sessions/holdout \
>     --repo-head "$(git rev-parse HEAD)" \
>     --out research/evaluations/eval_operational_holdout_YYYYMMDD.json
> ```
> **Expected additions:** each `evaluations[]` gains `in_sample` (train verdict + deltas),
> `out_of_sample` (holdout verdict + deltas), and `verdict_reconciliation`
> (`status`: `confirmed` | `holdout_contradicts` | `in_sample_fail`); `provenance.holdout_sessions`
> records the holdout inputs, and a top-level `walk_forward` block counts confirmed /
> holdout_contradicted / in_sample_fail. **Read `walk_forward` and `verdict_reconciliation`, not
> just `overall_verdict`** — `overall_verdict` reflects the in-sample window only, so a
> holdout-contradicted move can still show `overall_verdict=pass` in the artifact (the CLI also
> prints a `walk-forward holdout: …` line that flags contradictions at run time). **Screening
> downgrade:** when the train verdict passes but the
> holdout verdict fails (`status=holdout_contradicts`), `autotuner_screen` downgrades that
> candidate's verdict to `inconclusive`, so `autotuner_suggest` drops it. The downgrade is
> **conservative-only** — it can shrink the suggestion set, never promote a non-pass to pass.
> Whitelist, Tier, bounds, approval TTL, mock-only runtime activation, and safer-of merge are
> unchanged. Omitting the holdout flags reproduces the pre-W1 single-window behaviour exactly.

#### Step 1: Generate DRAFT proposals (dry-run first)
```
.venv/bin/python -m app.tools.autotuner_propose --plan plan.json --project-root .
```
**Expected result:** prints a Markdown report listing drafts "awaiting human approval"
and any refused candidates (with reasons). **Writes nothing** (dry-run default).
**If it fails:** a refused candidate means out-of-bounds / off-whitelist / bad evidence —
fix `plan.json` or the evidence path and re-run.

Then persist the drafts:
```
.venv/bin/python -m app.tools.autotuner_propose --plan plan.json --allow-write
```
**Expected result:** writes `_workspace/autotuner/proposals/<proposal_id>.json` (mode=shadow,
status=pending_review). Still **no approval, no apply.**

> **Optional Slack notification:** add `--notify-slack` to post the cycle summary to the
> operator channel. It is double-gated — nothing is sent unless `SLACK_ALERTS_ENABLED=true`
> and the operator channel (`SLACK_CHANNEL_PROJECT_OPERATOR`) is configured (delivery routes
> through the existing `SlackNotifier`, event type `autotuner_proposal`).

#### Step 2: Review the draft + evidence
```
.venv/bin/python -m app.tools.autotuner_approve \
    --proposal-file _workspace/autotuner/proposals/atp_20260605_shadow_0001.json \
    --approved-by human:<name>
```
**Expected result:** prints a review summary (id, mode/status, evidence count incl.
live_log, the change). **Writes nothing** (no `--approve`). Read it and decide.
**If it fails / looks wrong:** do not approve; delete or fix the draft and return to Step 1.

#### Step 3: Approve (the human gate)
```
.venv/bin/python -m app.tools.autotuner_approve \
    --proposal-file _workspace/autotuner/proposals/atp_20260605_shadow_0001.json \
    --approved-by human:<name> --ttl-hours 24 --approve
```
**Expected result:** re-validates, stamps approval + TTL, sets `mode=approved_low_risk`,
writes the approved artifact into the proposals dir. `REFUSED: ...` means it failed
re-validation (out of bounds, not Tier A, missing live_log evidence) — nothing written.
**If it fails:** read the REFUSED reason; the artifact is unchanged.

#### Step 4: Enable in mock (default-OFF gate)
Set in the **operator** environment that launches the bot (not in `.env`, not in an
agent session):
```
export KIS_ENV=mock
export AUTOTUNER_RUNTIME_OVERRIDES_ENABLED=true     # Tier A
# export AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED=true # Tier B (extra gates; see below)
```
**Expected result:** with these set, the next cycle's `_build_runtime_rate_control` /
`_build_regime_state` consult the approved bundle. Without them, behavior is byte-identical.

#### Step 5: Verify what WILL apply (before/while running)
```
.venv/bin/python -m app.tools.autotuner_status --env mock --enabled
```
**Expected result:**
```
# Autotuner runtime-override status (Tier A)
- flag_enabled : True
- env          : mock
- reason       : 1 parameter(s) from 1 eligible approved bundle
- would_apply  : {"buy_scan_shallow_top_k": 12}
```
**If `would_apply` is `{}`:** see Troubleshooting — the gate is intentionally refusing.

#### Step 6: Start/restart the session (operator) and monitor
Launch the bot through your **normal** session procedure (not via an agent). Confirm the
override took effect via your usual cycle logs and re-run Step 5 anytime.

> **Tier B (high-risk)** is the same flow, but approval is a separate opt-in:
> ```
> .venv/bin/python -m app.tools.autotuner_approve \
>     --proposal-file _workspace/autotuner/proposals/<draft>.json \
>     --approved-by human:<name> --ttl-hours 4 --high-risk --approve
> ```
> The proposal must contain **≥2 live_log evidence + a non-empty `risk_review`** block,
> and mock application still requires `AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED=true`.
> Safer-of means it can only **lengthen** `rebuy_cooldown_minutes` / **shrink**
> `same_symbol_max_buys_per_day` — never loosen.
> Verify the Tier B gate separately:
> ```
> .venv/bin/python -m app.tools.autotuner_status --env mock --high-risk --enabled-high-risk
> ```

#### Optional: live read-only shadow diagnostic (Stage 4a)
This is diagnostics only: it computes what the Tier A reader would select in live,
without enabling runtime application and without writing state.
```
.venv/bin/python -m app.tools.autotuner_status --env live --live-shadow
```
**Expected result:** `runtime_apply: False` and a `would_apply` map (or an explained
empty result). Collect **N=3 comparable shadow sessions** before any live activation
discussion.

### Verification
- [ ] `autotuner_status` shows `flag_enabled: True`, `env: mock`, and the expected `would_apply`.
- [ ] Exactly **one** approved, unexpired bundle for the parameter exists in
      `_workspace/autotuner/proposals/` (two+ ⇒ ambiguous ⇒ the gate refuses all).
- [ ] The merged effective value is never *more aggressive* than the protective clamp
      (safer-of: intervals/cooldown take the larger, caps take the smaller).

### Troubleshooting
| Symptom (`would_apply: {}`) | Likely cause | Fix |
|---|---|---|
| `reason: flag disabled` | `AUTOTUNER_RUNTIME_OVERRIDES_ENABLED` not set/true | export the flag in the launch env (Step 4) |
| `reason: env is 'live', not mock` | running live | Tier A/B activation is mock-only; live is a separate gate — stop |
| `reason: no eligible … bundle` (draft present) | bundle still `mode=shadow` / not approved | run Step 3 (`--approve`) |
| `no eligible` after approve | TTL expired, or re-validation now fails | re-approve with a fresh `--ttl-hours`; check bounds |
| `would_apply` empty with 2+ bundles | ambiguous (more than one active) | keep exactly one; supersede/remove the others |
| approved but value unchanged at runtime | safer-of kept the protective clamp (override was looser) | expected — override only tightens; nothing to do |

### Rollback
- **Immediate disable:** unset the flag in the launch env and restart the session —
  `unset AUTOTUNER_RUNTIME_OVERRIDES_ENABLED` (and the high-risk one). Behavior returns
  to byte-identical baseline.
- **Retire a bundle:** move/delete its file from `_workspace/autotuner/proposals/`, or
  let its TTL expire. The reader ignores expired/absent bundles automatically.
- **Confirm rollback:** `autotuner_status … --enabled` should report `flag disabled` (or
  `no eligible … bundle`) and `would_apply: {}`.

### Escalation
| Situation | Contact | Method |
|---|---|---|
| Override seems to apply in **live** | trading ops lead | immediate — unset flags, halt session |
| Unexpected order frequency/concentration change after enabling Tier B | risk owner | review `risk_review` + safer-of; disable flag |
| Repeated `REFUSED`/validation failures | autotuner maintainer | share the `REFUSED` reason + the artifact path |

### History
| Date | Run By | Notes |
|------|--------|-------|
| 2026-06-07 | — | Added Tier B `--high-risk` approval command and Stage 4a live-shadow diagnostics. |
| 2026-06-05 | — | Runbook authored; loop e2e-validated in mock (see live_autotuner_step6_e2e_observability.md). |
