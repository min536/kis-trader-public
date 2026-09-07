---
Purpose: Phase 5 (optional) — MCP/backtester-assisted candidate *suggestion*. Turn
  external proxy-backtest screening into a whitelist-validated curated plan that
  still flows through the existing human-gated pipeline.
Read when: wiring the open-trading-api backtester/MCP as a candidate screener.
Status: DONE (suggestions only; no live write, no auto-apply; trust boundary held).
Date: 2026-06-05
---

# Phase 5 — MCP-Assisted Candidate Suggestion (proposals only)

The external `open-trading-api` backtester (REST `:8002`) / MCP (`:3846`) can screen
parameter moves and emit proxy-backtest results. Phase 5 converts those into
**candidate suggestions** — entries of the same curated *plan* the Step 4
propose-cycle already consumes. It is the safe core of "MCP-assisted candidate
generation": it suggests, it never decides.

## What it does NOT do (plan §8 non-goals)
- No live write; the MCP/backtester never touches live settings.
- No auto-apply from proxy backtest PnL. A suggestion is just a plan entry.
- No bypass of the human gate: every suggestion still goes
  `propose → human approve → gated mock runtime` (and live is a separate gate).

## `app/autotuner/candidate_suggest.py`
`suggest_candidates(screening, *, baseline_values, date, mode="shadow") -> list[plan_entry]`
— pure. For each screened item it **drops** anything that is not suggestible:
- screener `verdict` present and ≠ `pass`;
- parameter not whitelisted, `blocked`, or Tier C/D (only Tier A/B suggested);
- `to_value` non-numeric or outside `[bound_min, bound_max]`.
Surviving items become plan entries (`proposal_id=atp_<date>_<mode>_NNNN`,
`from_value` from the baseline, `backtest_eval` carried through as evidence).

Proven to compose: a suggested entry feeds `run_propose_cycle` and builds a valid
DRAFT (see `tests/test_autotuner_candidate_suggest.py`).

## `app/tools/autotuner_suggest.py` (CLI)
```
python -m app.tools.autotuner_suggest \
    --screening screening.json --baseline baseline.json \
    --date 20260605 --out plan.json
# then: python -m app.tools.autotuner_propose --plan plan.json   (dry-run)
```
`screening.json` is produced by `autotuner_screen` (Phase A, below) from
**autotuner-domain** evals, normally emitted by `autotuner_operational_backtest`.
The CLI is **decoupled from the live service**: it consumes a screening file rather
than calling the network, so it runs offline/deterministically.

## `app/tools/autotuner_operational_backtest.py` (CLI) — eval producer
```
python -m app.tools.autotuner_operational_backtest \
    --candidates candidates.json \
    --baseline baseline.json \
    --session-summary research/sessions/session_20260607.json \
    --out research/evaluations/eval_operational_20260607.json
```
Creates the autotuner-domain eval shape consumed by `autotuner_screen`:
`{evaluations:[{changes:[{parameter,from_value,to_value}], verdict, deltas}], provenance}`.
It is offline/read-only, bounded, whitelist-aware, and conservative: it passes only
in-bounds Tier A/B moves that reduce operational pressure (interval/cooldown ↑,
caps/K ↓) and refuses source sessions with runtime/broker/order-error signals.

## `app/tools/autotuner_screen.py` (CLI) — screening producer (Phase A)
```
python -m app.tools.autotuner_screen \
    --evals-dir <autotuner-domain evals dir> --out screening.json
```
Aggregates autotuner-domain backtest evals (aggregate shape
`{proposal_id, evaluations:[{changes, verdict, deltas}, …], overall_verdict}`) — so
`app/autotuner/screening.py:evals_to_screening` **flattens `evaluations[]`**, emitting
one screening entry per change (`{parameter, to_value}`) tagged with that evaluation's
own `verdict` + E1 provenance. The **verdict is the eval's own — source of truth, not
operator-set**, so a suggestion can never claim a pass the backtest did not produce.
Bounded/offline: it never runs the backtester (`:8002`).

> **Domain boundary (E1 finding, 2026-06-07).** The screener reads each change's
> `parameter`/`to_value`. `run_proposal_backtest` writes **research-domain** changes
> (`family`/`param_id`/`new_value` — RSI etc.), a domain **disjoint** from the autotuner;
> a real research eval therefore yields an **empty** screening (no `parameter` key) — by
> design. The producer of autotuner-domain evals is now
> `app.tools.autotuner_operational_backtest` (scan/throttle params), not the research backtester. See
> `live_autotuner_pipeline_reconciliation.md`. (The **evidence** bridge is shape-agnostic and
> still attaches `run_proposal_backtest` results as proxy evidence regardless of domain.)

## Pipeline position
```
autotuner_operational_backtest evals → autotuner_screen → screening.json
  → autotuner_suggest                → plan.json  (whitelist-validated suggestions)
  → autotuner_propose        → DRAFT shadow proposals (_workspace)
  → autotuner_approve (human)→ approved_low_risk
  → runtime_activation (mock, default-OFF, safer-of)
```
Only the screen/suggest hops are "MCP/backtest-assisted"; everything after the plan
is the unchanged, human-gated loop. The trust boundary (§3) is preserved end to
end — backtest verdicts are proxy evidence and never auto-apply.
