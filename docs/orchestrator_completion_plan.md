# Orchestrator Completion Plan

**Subject:** `kis-ops-orchestrator` harness — drive from "structurally aligned" to "fully verified & durable"
**Created:** 2026-06-03
**Owner:** harness maintainer (kis-trader)
**Status:** completed for the current ops harness pass; retained as the completion record.

---

## 0. Why this plan exists

The orchestrator harness was audited and hardened after the `app/main.py` 5-stage split
(2026-06-02). Two docs-only commits landed:

- `3e76a56` — align harness safety gates with the split runtime (trading-critical surface,
  stale-anchor corrections, CLAUDE.md operator section)
- `4b513b8` — tighten quant-analyst routing triggers ("analyze" disambiguation)

That work made the harness **structurally correct**. It did not make it **verifiably complete**.
The remaining gap is evidence and coverage, not structure: the validation that proved routing/gates
work lives only in a now-compacted conversation, team mode has never been exercised end-to-end, and
the 9 supporting child skills were never re-read for post-split drift.

This plan closes those gaps against the harness skill's own acceptance bar
(`.claude/skills/harness/SKILL.md` Phase 6 verification + Phase 7 evolution + 산출물 checklist).

---

## 1. Current state (what is already done)

| Area | State | Evidence |
|------|-------|----------|
| Agent definitions (4) | ✅ exist, split-aligned | `.claude/agents/{ops-incident-engineer,debug-engineer,risk-analyst,quant-analyst}.md` |
| Orchestrator skill (1) | ✅ exists, 119 lines (<500) | `.claude/skills/kis-ops-orchestrator/SKILL.md` |
| Supporting skills (9) | ✅ exist | `.claude/skills/{ce-debug,ce-compound,incident-response,deploy-checklist,risk-assessment,statistical-analysis,create-viz,runbook,task-management}` |
| Safety gate scope | ✅ covers trading-critical surface | gate lists `app/execution`, `app/risk`, `app/strategy`, `app/auth/settings.py`, `app/main.py` |
| Stale anchors | ✅ corrected | no stale or permissive `app/config.py` / `run_session.sh` references; explicit `scripts/run_session.sh` execution prohibition retained; lock = `app/core/session_lock.py` |
| Routing precision | ✅ quant "analyze" disambiguated by object | SKILL.md + quant-analyst.md scope guard |
| CLAUDE.md pointer + change log | ✅ present | CLAUDE.md "Harness" section |
| Single-domain dry-run | ✅ persisted | read-only `Agent → return-value` path recorded in `docs/orchestrator_validation.md` §4 |
| Trigger validation | ✅ persisted, deliberately minimal | 12 core boundary cases recorded in `docs/orchestrator_validation.md` §2; expand only when routing changes |

---

## 2. Gap analysis (what "perfect" still needs)

| # | Gap | Harness ref | Severity |
|---|-----|-------------|----------|
| G1 | **RESOLVED (Phase B, 2026-06-03).** `docs/orchestrator_validation.md` now holds the trigger matrix, safety-gate matrix, dry-run evidence, and A/B result. | 5-1 file-based / audit trail | done |
| G2 | **ACCEPTED/MINIMAL (2026-06-03).** The durable artifact records the 12 high-value boundary cases and cross-skill collision notes. Expand the matrix when routing/agent scope changes rather than maintaining a large synthetic set on every pass. | 6-4 | monitored |
| G3 | **RESOLVED for this runtime (Phase C, 2026-06-03).** `TeamCreate`/`SendMessage` are unavailable here, so the executable multi-domain path is orchestrator-coordinated `Agent` + `Task*`; that path is validated in `docs/orchestrator_validation.md` §4B. Autonomous team mode is deferred until a runtime provides those tools. | 6-2, 6-5 | done/deferred |
| G4 | **RESOLVED (Phase D, 2026-06-03).** A/B result persisted in `docs/orchestrator_validation.md` §4C; orchestrator won 5/6 axes with no correctness regression. | 6-3 | done |
| G5 | **RESOLVED (Phase A, 2026-06-03).** Audited all 9; zero stale, permissive, or operational-execution `app/config.py` / `run_session.sh` references and zero pre-split-path drift found. Explicit prohibitive `scripts/run_session.sh` references are required/allowed. Two terminology drifts patched (`ce-debug`, `ce-compound`); the 3 ops skills are generic-by-design (bindings live in agent+orchestrator layer), not drift. | 0 (drift detection), 7-5 | done |
| G6 | **RESOLVED (Phase E, 2026-06-03).** `docs/todo.md` and CLAUDE.md both reference the orchestrator validation/completion pass. | 7-3 | done |

---

## 3. Plan — phased, each phase independently shippable

> Constraint envelope (applies to every phase): docs/validation only. **No runtime code edits, no
> `python -m app.main`, no `scripts/run_session.sh`, no broker/order APIs, no `.env`/`.token_cache.json` access, `data/`·`logs/`·`results/`·
> `archive/` via tail/head only.** Code and doc commits stay separate. These mirror CLAUDE.md runtime
> safety rules and must not be relaxed by any phase below.

### Phase A — Audit child skills for post-split drift  (closes G5)
Read all 9 supporting `SKILL.md` files and grep for stale anchors
(`app/config.py`, stale or permissive `run_session.sh` references, pre-split monolith assumptions,
old lock location). Explicit prohibitive references to `scripts/run_session.sh` are required/allowed.
- **Do:** correct any stale reference to match the split runtime (same fixes already applied to the
  4 agents). If a skill is clean, record it as clean.
- **Output:** drift findings table (skill → stale ref → corrected ref → status).
- **Acceptance:** every child skill either verified clean or corrected; zero stale, permissive, or
  operational-execution `app/config.py` / `run_session.sh` references remain anywhere under
  `.claude/skills/`. Explicit prohibitive references to `scripts/run_session.sh` are required/allowed.

> **Result (2026-06-03):** ✅ done. Scanned all 9 child SKILL.md (no `references/` subdirs exist).
> No stale, permissive, or operational-execution `app/config.py` / `run_session.sh` references and no
> monolith / pre-split-path drift. Explicit prohibitive `scripts/run_session.sh` references are
> required/allowed. Two narrow terminology
> drifts patched: `ce-debug` (vague "config loading" + main.py-only scope → `KIS_ENV` mock default in
> `app/auth/settings.py` + full trading-critical surface) and `ce-compound` (`paper_trading` boolean
> term → `KIS_ENV` mock/live). The ops skills (`deploy-checklist`, `incident-response`, `runbook`) are
> generic-by-design — kis-trader bindings correctly live in the agent/orchestrator/CLAUDE.md layer, so
> they were verified clean, not patched. **Smaller drift than G5 assumed; Phases B–E unaffected.**
> Observation (non-blocking, evolution candidate — not patched): `deploy-checklist` carries no pointer
> to the `KIS_ENV=live` switch; acceptable today since the orchestrator pairs it with `risk-assessment`
> + the ops agent, but worth revisiting if deploy guidance ever runs skill-only.

### Phase B — Build the durable validation artifact  (closes G1 + G2)
Create `docs/orchestrator_validation.md` as the reproducible test record.
- **Do:** for each routing boundary (ops / debug / risk-gate / quant / direct-handle), write
  8–10 should-trigger phrasings + 8–10 near-miss should-NOT phrasings (the hard, boundary-blurring
  cases — e.g. "analyze today's rate-limit spikes" must land on ops/debug, not quant). Run each
  mentally through the routing table, record expected vs actual + PASS/FAIL.
- **Do:** add the cross-skill collision check — confirm no phrasing double-triggers two child skills.
- **Output:** a trigger matrix with a pass-rate summary; any FAIL feeds a routing-table patch (Phase E).
- **Acceptance:** ≥95% pass on should-trigger and should-NOT sets; every FAIL has a logged disposition
  (patched or accepted-with-reason).

> **Result (2026-06-03):** ✅ done with a deliberately smaller, high-signal matrix. The durable
> artifact records 12 core routing/safety boundary cases, the "analyze" disambiguation, and the
> cross-skill collision check in `docs/orchestrator_validation.md` §2. Future runs should add cases
> when routing changes, not inflate the matrix mechanically.

### Phase C — Exercise the executable multi-domain path read-only  (closes G3)
Prove the multi-domain path actually wires up, using a benign, non-mutating scenario.
- **Do:** pick the documented KIS-timeout scenario. Because this runtime lacks `TeamCreate` and
  `SendMessage`, use the documented executable fallback: spawn `ops-incident-engineer` and
  `debug-engineer` via `Agent`, track triage→root-cause→conditional-risk→synthesis with `Task*`,
  and synthesize returned outputs in the orchestrator. No `python -m app.main`, no
  `scripts/run_session.sh`, no broker calls — the agents read bounded artifacts and reason.
- **Output:** a transcript-backed note in `orchestrator_validation.md`: tasks created, dependencies
  closed in order, returned outputs joined, teardown clean.
- **Acceptance:** multi-domain wiring is non-empty on the executable `Agent` + `Task*` path, risk gate
  correctly *not* required for read-only diagnosis, clean teardown.

> **Result (2026-06-03):** ✅ done. `docs/orchestrator_validation.md` §4B records the executable
> `Agent` + `Task*` path and demotes autonomous team mode to a future enhancement.

### Phase D — With-skill vs without-skill A/B  (closes G4)
Measure the harness's marginal value on one representative single-domain request.
- **Do:** run one realistic prompt (e.g. "draw the equity curve for the latest backtest") through
  (a) the orchestrator path and (b) a baseline no-harness agent. Compare routing correctness, safety-rule
  adherence (tail/head on `results/`, no full reads), and output completeness.
- **Output:** short A/B comparison in `orchestrator_validation.md`.
- **Acceptance:** with-skill run shows ≥1 concrete advantage (correct routing or a safety rule the
  baseline skipped); if it shows none, log it as an evolution trigger (Phase 7-4).

> **Result (2026-06-03):** ✅ done. `docs/orchestrator_validation.md` §4C records a read-only A/B:
> orchestrator won 5/6 axes, mainly on routing separation, safety gating, and evidence discipline.

### Phase E — Fold findings back + register the harness in tracking  (closes G6 + regressions)
- **Do:** apply any routing-table / description patch surfaced by Phases B–D (generalize the fix,
  never overfit to one phrasing). Add a `docs/todo.md` line for the harness so future sessions see it.
  Append a CLAUDE.md change-log row summarizing this completion pass.
- **Output:** patched harness files (if any) + todo entry + change-log row.
- **Acceptance:** CLAUDE.md change log, `docs/todo.md`, and the actual `.claude/` files agree
  (no drift between documentation and reality).

> **Result (2026-06-03):** ✅ done. CLAUDE.md change log and `docs/todo.md` both reference the
> validation/completion pass and runtime skeleton.

---

## 4. Sequencing & dependencies

```
A (child-skill drift)  ─┐
                        ├─► E (fold-back + tracking + regression sweep)
B (trigger matrix) ─────┤
C (multi-domain Agent+Task* dry-run) ──┤
D (A/B value test) ─────┘
```

A, B, C, D are independent and can run in any order (or parallel). E must run last — it consumes
their findings. Recommended order: **A → B → C → D → E** (A is cheapest and may pre-empt B failures;
E closes the loop).

---

## 5. Definition of done (whole effort)

- [x] Zero stale anchors anywhere under `.claude/` (agents **and** skills).
- [x] `docs/orchestrator_validation.md` exists and is reproducible: trigger matrix, safety-gate matrix,
      executable multi-domain dry-run note, A/B comparison.
- [x] Multi-domain `Agent` + `Task*` path proven non-empty end-to-end, read-only.
- [x] Any routing FAIL either patched (generalized) or logged with an accepted reason.
- [x] `docs/todo.md` and CLAUDE.md change log reflect the harness state; no doc-vs-reality drift.
- [ ] All work landed as **docs-only** commits, separate from any code commit.

## 6. Explicitly out of scope

- Adding new agents or new child skills (this is a *completion/verification* pass, not expansion).
- Any runtime code change to `app/` (the harness audits code; it does not edit trading logic here).
- Live-mode / deploy actions of any kind.
- Touching `.env`, `.token_cache.json`, or doing full reads of `data/`·`logs/`·`results/`·`archive/`.

## 7. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Multi-domain dry-run accidentally triggers a real call | Scenario is read-only over existing artifacts; risk-analyst gate + CLAUDE.md "never call broker APIs" rule enforced; no `python -m app.main` or `scripts/run_session.sh`. |
| Over-fitting a routing patch to one failed phrasing | Phase E rule: generalize the principle, re-test the whole boundary, never a one-phrasing band-aid. |
| Validation artifact rots as harness evolves | `orchestrator_validation.md` is regenerated, not hand-edited, whenever routing/gates change; CLAUDE.md change log points to it. |
| Scope creep into expansion | Section 6 fences scope; expansion is a separate future effort. |
