## Runbook: open-trading-api Integration (read-only / shadow)

**Owner:** trading ops | **Frequency:** As needed (when feeding external backtests into the autotuner)
**Last Updated:** 2026-06-10 | **Last Run:** —

### Purpose

Operate the read-only bridge to a sibling `open-trading-api` checkout so external
backtests can become **shadow-only** autotuner candidate suggestions — without ever
blurring kis-trader's operational/security boundary. Nothing here applies anything
to the live cycle: external results are *proxy evidence*, every parameter move still
flows through `screen → suggest → propose → human approve → gated mock runtime`.

> All commands run from the repo root with the project venv: `.venv/bin/python`.
> **Never** start `app.main` / `run_session.sh`, the backtester service, or any
> broker/order API from an agent session — those are operator actions.

### Which engine answers which question (3-engine role table)

| Engine | What it actually runs | Use it for | Never use it for |
|--------|----------------------|------------|------------------|
| **open-trading-api Lean backtester** (`:8002`, DSL→codegen approximation strategies) | QuantConnect Lean, `Resolution.Daily`, approximation of the strategy family — **not** the live code | Parameter *direction* exploration, market/universe-level proxy research | Strategy-logic verification; autotuner screening input |
| **kis-trader native `backtester/engine_backtest`** (+ `parity`) | The real `evaluate_buy_decision` / `evaluate_sell_decision` / selection / sizing functions, replayed | Verifying a strategy-logic change behaves as intended; live-log parity | Market-wide universe research (it replays recorded sessions) |
| **Operational eval** (`autotuner_operational_backtest` on session summaries / EOD) | Recorded live/mock session outcomes | The **only** screening producer for autotuner cadence params; D4 `live_log` cross-validation | — |

Rule of thumb: 전략 로직 변경 → engine_backtest + parity, 파라미터 방향 탐색 →
open-trading-api, autotuner 승인 → operational eval + live_log.

### Known limitations of the external backtest (read before trusting a result)

External (`:8002`) eval results are **proxy evidence only** because:

- **Daily resolution** — intraday cycle logic (entry windows, reentry cooldowns,
  intra-session risk exits) cannot be reproduced at `Resolution.Daily`.
- **Survivorship bias** — the universe comes from the current `.master/` snapshot;
  delisted symbols are absent, which inflates long-only results.
- **Cost model is optimistic** — commission 0.015% + sell tax 0.2% are modeled, but
  **slippage = 0**; thin-liquidity fills are unrealistically good.
- **Approximation strategies** — the DSL→codegen strategy is *not* a 1:1 export of
  live logic (see `_KNOWN_APPROXIMATIONS` in `backtester/engine_backtest/parity.py`).

A "pass" here narrows the search space; it never substitutes for the operational
eval + `live_log` cross-validation that gates an actual parameter move.

### Safety boundary (what the integration may and may not do)

- **Filesystem / subprocess** go through the adapter (`app/integrations/open_trading_api`):
  repo located **only** via `OPEN_TRADING_API_ROOT` (no implicit sibling fallback),
  repo-marker validated; **allowlisted read-only git commands only** (`repo_head`,
  `repo_root`), `shell=False`, 5s timeout, sanitized env (no `KIS_*` secrets), bounded
  output; JSON/CSV artifact reads are relative-path-only, root-escape/protected-file
  rejected, size/row bounded, schema-checked.
- **Backtester REST** (`run_proposal_backtest → :8002`) is a **localhost-only** research
  service. The adapter owns the URL policy: a non-loopback `--bt-url` is **refused before
  any network access** (`is_loopback_url`). The operator starts the backtester; the agent
  never does.
- **Forbidden:** reading `.env` / `kis_devlp.yaml` / token cache; broker/order/session;
  copying the sibling repo's venv / Lean workspace / credentials into kis-trader.

### Prerequisites

- [ ] `.venv` present (`.venv/bin/python -m pytest tests/test_open_trading_api_integration.py -q` passes).
- [ ] Sibling repo checked out (default `$HOME/open-trading-api`).
- [ ] `OPEN_TRADING_API_ROOT` exported in the **process environment** (NOT `.env`):
      ```sh
      export OPEN_TRADING_API_ROOT=$HOME/open-trading-api
      ```
- [ ] `KIS_ENV=mock`. Live is a separate human gate — out of scope here.

### Operator one-command pipeline

For the normal research path, prefer the operator wrapper:

```sh
export OPEN_TRADING_API_ROOT=/path/to/open-trading-api
scripts/open_trading_api_pipeline.sh --account <ACCOUNT> --date <YYYYMMDD>
```

What it does, in order:

1. starts or verifies the localhost backtester (`/api/health` on `:8002`);
2. calls `POST /api/symbols/collect` to refresh the external `.master/` files;
3. runs `validate_symbol_master` against the refreshed `.master/` snapshot (report-only by default);
4. refreshes baseline summaries via `scripts/update_baselines.sh`;
5. runs `scripts/run_research_loop.sh` against the same `:8002` REST service.

Use `--dry-run` first when changing paths or options. Use `--strict-symbol-validation`
only when `config/symbol_tags.yaml` is already clean against the refreshed master;
otherwise stale symbol-tag findings are reported but do not block backtests.

### Slack-triggered pipeline

The Slack Socket Mode bot can start the same wrapper from the project backtester
channel. Configure the bot process environment, then restart `scripts/run_slack_bot.sh`:

```sh
SLACK_CHANNEL_PROJECT_BACK_TESTER=<CHANNEL_ID>
SLACK_BOT_ALLOWED_USER_IDS=<USER_ID[,USER_ID…]>   # REQUIRED for backtest
SLACK_BACKTEST_ACCOUNT=<ACCOUNT>
OPEN_TRADING_API_ROOT=/path/to/open-trading-api
SLACK_BACKTEST_MAX_RUNTIME_SEC=3600                # optional; watchdog default
SLACK_BACKTEST_STOP_AFTER=true                     # recommended: no orphan :8002
```

Then mention the bot in that channel:

```text
@kis-trader backtest
@kis-trader backtest <YYYYMMDD>
@kis-trader backtest <YYYYMMDD> --dry-run
@kis-trader backtest status     # running pid + last log lines
@kis-trader backtest kill       # SIGTERM the pipeline process group
```

The bot replies immediately with the background pid and log path. It refuses the
command outside `SLACK_CHANNEL_PROJECT_BACK_TESTER`; because the command starts a
host process, `SLACK_BOT_ALLOWED_USER_IDS` is **mandatory** (unset ⇒ every
backtest mention is refused). Optional `SLACK_BOT_ALLOWED_TEAM_IDS` still applies.
Runs longer than `SLACK_BACKTEST_MAX_RUNTIME_SEC` are terminated (process-group
SIGTERM) and reported to the thread as timed out.

Operational hardening behavior (automatic, no configuration needed):

- **Bot restart while a pipeline runs:** completion monitoring lives in the bot
  process, so a restart loses it. On startup the bot posts a notice to the
  backtester channel (pid + "monitoring lost — use `backtest status` / `backtest
  kill`"). The run itself keeps going; only the automatic completion message is lost.
- **PID-reuse protection:** `slack_backtest.pid` records the process start time
  alongside the pid; a recycled OS pid no longer blocks new launches or receives
  a mistaken `backtest kill`.
- **Master collect retry:** the pipeline retries `POST /api/symbols/collect` up to
  `MASTER_COLLECT_RETRIES` times (default 3) with
  `MASTER_COLLECT_RETRY_DELAY_SECONDS` (default 10) between attempts before failing.

### 1. Confirm the integration is available (with audit trail)

```sh
python -m app.tools.open_trading_api_status --json
# opt-in observability — record which command/commit/path was probed:
python -m app.tools.open_trading_api_status --json --audit-log _workspace/otapi_audit.jsonl
```
- `available: true` + a `repo_head` commit → ready.
- `available: false` → check `reason` (env unset / markers missing). This is a clean
  "external evidence unavailable", never a trading block.

### 2. Operator runs the backtests (localhost only)

Start the backtester service yourself (operator action, outside any agent session),
then for each candidate proposal:
```sh
python -m app.tools.run_proposal_backtest --proposal <proposal.json> \
    --output-dir research/evaluations            # --bt-url defaults to http://localhost:8002
```
- A non-loopback `--bt-url` is refused up front (SSRF guard).
- Each eval JSON is **self-describing**: it carries the proposal `changes` and its own
  `verdict` (from `pass_criteria`).

### 3. Attach research evals as proxy evidence, but screen cadence separately

`run_proposal_backtest` evals are strategy/research-domain artifacts (`family` /
`param_id` / `new_value`). They can be attached as proxy evidence in the human-gated
autotuner loop, but they are **not** the screening producer for operational cadence
params. Produce cadence evals locally:

```sh
.venv/bin/python -m app.tools.autotuner_operational_backtest \
    --candidates candidates.json \
    --baseline baseline.json \
    --session-summary research/sessions/session_YYYYMMDD.json \
    --out research/evaluations/eval_operational_YYYYMMDD.json
python -m app.tools.autotuner_screen  --evals-dir research/evaluations --out screening.json
python -m app.tools.autotuner_suggest --screening screening.json --baseline baseline.json \
    --date $(date +%Y%m%d) --out plan.json
```
- The screening `verdict` is the eval's own — you cannot hand-set a pass the backtest
  did not produce.
- `autotuner_screen` consumes only autotuner-domain evals with `{parameter,to_value}`;
  strategy/research evals intentionally yield no screening entries.
- `autotuner_suggest` drops anything not whitelisted / in-bounds / Tier A·B.

### 4. Hand off to the human-gated autotuner loop

From here it is the unchanged autotuner runbook (`docs/runbook_autotuner_loop.md`):
`autotuner_propose` (dry-run) → review → `autotuner_approve --approve` → mock runtime
(default-OFF flag, safer-of). External backtest results remain **proxy evidence**: an
*approved* proposal still needs ≥1 `live_log` cross-validation (D4).

### Failure / recovery

| Symptom | Cause | Action |
|---------|-------|--------|
| `available:false`, reason mentions env | `OPEN_TRADING_API_ROOT` unset | export it in the process env (not `.env`) |
| `available:false`, reason mentions marker | wrong path / not the repo | point env at the real checkout (`.git`,`README.md`,`backtester/`) |
| `refusing non-loopback backtester URL` | `--bt-url` not localhost | use `http://localhost:8002`; never a remote host |
| empty `screening.json` | no autotuner-domain evals / all malformed / only research-domain evals | run `autotuner_operational_backtest` and confirm its eval has `changes[].parameter` |
| `autotuner_suggest` drops everything | out-of-bounds / non-whitelist moves | check `config/autotuner_whitelist.yaml` bounds |

### Verification (before the next regular session)

- [ ] `.venv/bin/python -m pytest tests/test_open_trading_api_integration.py tests/test_autotuner_screening.py tests/test_autotuner_operational_backtest.py -q` passes.
- [ ] No runtime override was applied without your explicit `autotuner_approve --approve`.
- [ ] The integration touched no protected files and started no service from an agent session.
