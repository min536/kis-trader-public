---
Purpose: Reconcile the pre-existing research-proposal pipeline (`app/tools/proposal_*`,
  `research/`) with the new live-autotuner pipeline (`app/autotuner/`, `_workspace/autotuner/`).
  Records the layered-by-domain decision so the two systems do not drift into duplicates.
Read when: before building any new proposal/approval/generation/backtest tooling for the autotuner,
  or when deciding where a new capability belongs.
Status: DECISION RECORD — Step 1 of the post-Phase-4 roadmap.
Date: 2026-06-04
---

# Autotuner ↔ Research-Proposal Pipeline Reconciliation

## TL;DR

The two pipelines **tune disjoint parameter domains** and serve different purposes.
The only real overlap is the *machinery and vocabulary* ("proposal → human approval →
maybe apply"), not the parameters they touch. **Decision: keep both, layered by domain.
The new `app/autotuner/` stays the single runtime-application gate; the old research
pipeline stays the strategy-research surface. Do not merge their schemas/storage.**

## The two systems

| Axis | OLD: research-proposal pipeline | NEW: live-autotuner |
|------|----------------------------------|----------------------|
| Code | `app/tools/proposal_generator.py`, `proposal_constraints.py`, `proposal_registry.py`, `run_proposal_backtest.py`, `shadow_watch.py`, `overnight_tuning_report.py` | `app/autotuner/{validator,generator,persist,evidence,shadow,runtime_override,runtime_activation}.py` |
| Parameter domain | **Strategy / signal entry** params: core & continuation RSI bounds (`proposal_constraints.CORE_PARAMS` / `CONTINUATION_PARAMS`), `family/param_id` keyed | **Operational cadence / throttle** params: scan & sell intervals, `scan_symbols_max_per_cycle`, deep/shallow eval limits (Tier A); `rebuy_cooldown_minutes`, `same_symbol_max_buys_per_day` (Tier B). Order sizing = Tier C (blocked); creds/env = Tier D (forbidden) |
| Whitelist source | `proposal_constraints.ParamSpec` (`MAX_PARAMS_PER_PROPOSAL=3`) | `config/autotuner_whitelist.yaml` (Tier A/B/C/D + bounds + max_step) |
| Artifact schema | `{changes, status: proposed/invalid/exhausted, ...}` | schema-versioned: `proposal_id=atp_*`, `baseline`, `evidence`, `ttl`, `approval`, `risk_review`, `audit` |
| Storage | `research/proposals/`, `research/evaluations/`, `research/proposal_registry.json` | `_workspace/autotuner/{proposals,baselines}/` |
| Lifecycle / approval | `proposal_registry`: `proposed → research_only → shadow_candidate → live_candidate` (live needs explicit `--promote-live`). "Single source of truth for proposal state." | `status: draft → approved` + `approval{}` + `ttl{}`, re-validated by `validate_proposal` at read time |
| Backtest / eval producer | `run_proposal_backtest` → `research/evaluations/` (strategy/research domain) | `autotuner_operational_backtest` → autotuner-domain evals for `autotuner_screen` |
| **Wired to live runtime?** | **No** — research artifacts for human review; promotion does not auto-apply to a running cycle | **Yes** — `runtime_activation.apply_runtime_overrides` is called from `app/main.py` (Tier A, default-OFF, mock-only) |

## Key finding

There is **no parameter-domain collision**. The old pipeline proposes changes to
strategy entry logic (RSI thresholds); the new autotuner proposes changes to operational
cadence/throttle. A proposal in one system can never be a duplicate of one in the other
because they cannot name the same parameter (different whitelists, different keyspaces).

The duplication the roadmap worried about is **structural vocabulary** — both have
"changes", "status", "shadow", "promote/approve", a registry/persist layer, and a
backtest step. That is shared *pattern*, not shared *state*.

## Decision

1. **Layered by domain; two registries stay separate.** Merging the schemas/storage would
   force the safety-hardened autotuner artifact (ttl, evidence, re-validation, safer-of)
   onto strategy research that does not need it, and would risk weakening the autotuner's
   hard gates. Net complexity up, safety down. Rejected.
2. **`app/autotuner/` is the *only* runtime-application gate.** Nothing from the old
   pipeline reaches a running cycle except by being converted into an autotuner artifact
   that independently passes `validate_proposal` at read time. This preserves the invariant:
   *the old research pipeline cannot accidentally change live behavior.*
3. **Reuse the old registry's safety *principle*, not its code:** explicit human promotion +
   a separate, non-skippable gate for the live step. The autotuner already does this via
   `status=approved` + `KIS_ENV=mock` + default-OFF flags; live is a distinct human gate.
4. **No new strategy-param generator/registry.** The old `proposal_*` tools remain the
   surface for strategy/RSI research. New autotuner work must not reimplement generation,
   constraints, registry, or backtest for that domain.

## Consequences for the roadmap

- **Step 2 (approval tool) is NOT duplication.** The autotuner artifact in
  `_workspace/autotuner/proposals/` has no tool to perform its `draft → approved` transition
  (stamp `approval{}`/`ttl`, re-validate). That gate is the missing piece for the autotuner's
  own distinct artifact — build it under `app/autotuner/approval.py` + a thin
  `app/tools/autotuner_approve.py` CLI. It does not touch the old `research/` registry.
- **Step 3 (real backtest + evidence) reuses old infrastructure via a bridge, not a rewrite.**
  `run_proposal_backtest` output and reconstructed live logs can still feed the
  autotuner's `backtest_evidence` / `live_log_evidence` constructors as proxy evidence.
  Screening is separate: autotuner-domain cadence evals are produced by
  `app.tools.autotuner_operational_backtest`.
- **Strategy-RSI proposals stay manual-apply for now.** Promoting a `live_candidate` in the
  old registry still implies a reviewed config/strategy change by a human; it is out of scope
  for the autotuner runtime path (Tier C/strategy is blocked/forbidden there by design).

## Safety invariant (unchanged)

Only an autotuner artifact that (a) lives in `_workspace/autotuner/proposals/`, (b) is
`status=approved` with valid `approval`/`ttl`, and (c) passes `validate_proposal` **at read
time** can affect a running cycle — and only in `mock` with the default-OFF flag on, merged
safer-of so it can never beat a protective clamp. The old pipeline has no path to that.

## Screening producer is autotuner-domain (E1 finding, 2026-06-07)

The **evidence** bridge (Step 3) is change-shape-agnostic: it reads a backtest eval's
`verdict`/`deltas`/`provenance`, so `run_proposal_backtest` output can attach as proxy
evidence regardless of domain. The **screening** producer is not: `autotuner_screen` /
`evals_to_screening` read each change's `parameter`/`to_value` to build candidates, and
`run_proposal_backtest` writes research-domain changes (`family`/`param_id`/`new_value` —
RSI etc.). So a *real* `run_proposal_backtest` eval yields an **empty** screening (no
`parameter` key) — by design, not a bug. Consequence:

- `autotuner_screen` consumes only **autotuner-domain** evals (`{evaluations:[{changes:
  [{parameter,to_value}], verdict}]}`). The proper producer now exists:
  `python -m app.tools.autotuner_operational_backtest`, which evaluates scan/throttle
  candidates offline from local session summaries and emits autotuner-shape changes.
- Do **not** force-map research `param_id` → autotuner `parameter` (RSI is not an operational
  cadence param; the mapping would be meaningless). The disjoint-domain principle holds.

## Deferred / open

- Improve the operational-cadence evaluator's metric model as more session-summary fields
  become stable. It is intentionally proxy evidence: conservative pass/fail screening only,
  never approval or runtime application.
- Whether to eventually unify the two whitelists behind one schema (low priority; only worth
  it if the domains ever converge — they currently do not).
- Whether strategy-RSI changes should ever gain a gated runtime path (would require unblocking
  Tier C-equivalent strategy params — a separate, high-bar human decision; see decisions D1).
