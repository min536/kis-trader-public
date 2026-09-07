---
Purpose: capstone validation that the full autotuner loop composes in mock, plus
  operator observability for why a runtime override is/isn't applied.
Read when: confirming the loop works before enabling any flag, or debugging "why
  isn't my approved proposal being applied".
Status: DONE (read-only / tests + diagnostics; no trading-critical surface).
Date: 2026-06-05
---

# Step 6 — End-to-End Mock Validation + Observability

The pipeline was built as independently unit-tested components. This step proves
they actually **compose** into a working human-in-the-loop loop, and gives
operators visibility into the gate (the deferred half of review Finding 6).

## End-to-end validation (`tests/test_autotuner_e2e_mock.py`)

Composes the real modules, entirely in mock, no broker calls:

```
run_propose_cycle (DRAFT shadow, Tier A, +live_log)
  → approve_proposal (approved_low_risk, stamped + re-validated)
  → resolve_runtime_overrides (mock, enabled)  → {buy_scan_shallow_top_k: 12}
  → merge_safer_of  → effective applied (min = fewer, safer)
```

- **Positive:** the four stages chain and the override lands.
- **Negative:** the same draft left **unapproved** (`mode=shadow`) is never consumed
  by the runtime reader (`resolve_runtime_overrides` returns `{}`). The approval gate
  is load-bearing.

## Observability (`app/autotuner/diagnostics.py` + `app/tools/autotuner_status.py`)

The hot-path resolvers fail safe to `{}` silently. `diagnose_runtime_overrides(*,
project_root, now, env, enabled)` re-runs the same gate **read-only** and returns a
structured reason:

- flag off → `"flag disabled (AUTOTUNER_RUNTIME_OVERRIDES_ENABLED off)"`
- env ≠ mock → `"env is '<env>', not mock"`
- single approved bundle → `would_apply={...}`, `"N parameter(s) from 1 eligible approved bundle"`
- bundle present but not approved/valid/unexpired → `would_apply={}`, `"no eligible … bundle"`

Operator CLI:
```
python -m app.tools.autotuner_status --env mock --enabled
# -> flag_enabled / env / reason / would_apply
```

Tier B has a separate high-risk view:
```
python -m app.tools.autotuner_status --env mock --high-risk --enabled-high-risk
# -> flag_enabled / env / reason / would_apply
```

Stage 4a live-shadow diagnostics are read-only and never activate runtime overrides:
```
python -m app.tools.autotuner_status --env live --live-shadow
# -> shadow_only / runtime_apply=False / reason / would_apply
```

## Safety

- Read-only: the diagnostics never apply anything and never raise; the e2e test
  composes real modules but touches only a temp `_workspace`.
- No broker calls, no trading-critical surface touched, no live path.
- Tier B diagnostics are implemented as a separate default-OFF high-risk view.
- Live-shadow diagnostics compute `would_apply` only; they do not bypass the mock-only
  activation resolver.

## Where this leaves the roadmap

The loop is now **proven to work end-to-end in mock** and is **observable**. The
remaining items are deliberate human gates (enable flags, `KIS_ENV=live`, Tier C
unblock) and ergonomics (operator runbook, Slack delivery) — not correctness.
