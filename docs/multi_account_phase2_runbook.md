# Multi-Account Phase 2 — Read-Only Sanity Runbook

> Operator runbook for C-4 Phase 2 (`multi_account_parallelization_plan.md` §9).
> The **configuration** steps (1–3) are agent-buildable and offline. The
> **API sanity** steps (4) are **operator-run against real KIS** — an agent must
> never execute them (no broker/quotations calls, no `app.main`, no live gate).

## 0. Preconditions

- Phase 1 path separation mechanism is in place: session lock auto-scopes by
  `account_signature`; `KIS_LIVE_SNAPSHOT_DIR` / `KIS_LIVE_SNAPSHOT_LOG_DIR` /
  `KIS_CREDENTIAL_CACHE_DIR` redirect snapshot/worker/token-cache per account.
- `KIS_ENV=mock` for every account. Live stays behind the Phase 5 gate.

## 1. Generate collision-free per-account env (offline)

```sh
.venv/bin/python -m app.tools.multi_account_env --label core --label extended
```

Emits a `# account: …` block plus `export KIS_LIVE_SNAPSHOT_DIR=… / …LOG_DIR=… /
KIS_CREDENTIAL_CACHE_DIR=…` per label (paths under `data/accounts/<slug>/`).
Distinct labels that collapse to the same slug are rejected. Append each block to
that account's session env file (alongside its real `CANO` / `APP_KEY` etc.).

## 2. Verify path isolation (offline, no secrets)

Write a secrets-free profiles JSON (labels + the three dirs only):

```json
[
  {"label": "core",     "snapshot_dir": "data/accounts/core/data",
   "snapshot_log_dir": "data/accounts/core/logs",
   "credential_cache_dir": "data/accounts/core/cache", "env": "mock"},
  {"label": "extended", "snapshot_dir": "data/accounts/extended/data",
   "snapshot_log_dir": "data/accounts/extended/logs",
   "credential_cache_dir": "data/accounts/extended/cache", "env": "mock"}
]
```

```sh
.venv/bin/python -m app.tools.multi_account_preflight --profiles profiles.json
```

Exit `0` + `OK` means the live-snapshot file, snapshot worker PID/heartbeat dir,
and token cache file are pairwise distinct. Exit `1` lists the shared resource and
the env var to fix. Paths are `.resolve()`-normalized, so `..`/symlink/relative
aliases of the same dir are caught.

## 3. Route the universe per account (offline)

```sh
.venv/bin/python -m app.strategy.universe_routing --tags universe:core
.venv/bin/python -m app.strategy.universe_routing --tags universe:extended --exclude risk:leveraged,risk:derivative
```

Paste each `SCAN_SYMBOLS="…"` into the matching account's env.

## 4. Read-only API sanity — **OPERATOR ONLY**

Run **manually**, one account at a time, with that account's env sourced. These
hit real KIS; keep call volume minimal (no order API, no load test — avoid
`EGW00201`). Per `multi_account_parallelization_plan.md` §9:

1. **Token issue** — each AppKey issues its own token; confirm the token cache
   lands under that account's `KIS_CREDENTIAL_CACHE_DIR` only.
2. **Single quote** — one `inquire_price` per account; confirm a normal response.
3. **Cache isolation** — confirm account A's token file is untouched by account B.
4. **Balance** — one balance query per account; confirm correct, separated data.
5. **Rate-limit** — observe with minimal calls; note any `EGW00201`.

`app/tools/kis_api_sanity_check.py` prints the resolved offline metadata to
cross-check what each env would use **without** calling the network.

## 5. Hand back to the agent

Report the step-4 results. The agent reviews them (no execution) and, on a clean
pass, proceeds to Phase 3 routing already shipped → Phase 4 mock-parallel
observation (operator-run) → Phase 5 live (separate gate).

## Never

- ❌ Agent executes step 4 (real KIS calls) or starts `app.main`.
- ❌ Order-API based bucket/limit testing; bulk quote load tests.
- ❌ Parallel `app.main` before steps 1–3 pass for every account.
