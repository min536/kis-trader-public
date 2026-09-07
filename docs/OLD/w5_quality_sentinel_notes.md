---
Purpose: W5 implementation/contract notes — pure market-data quality sentinel.
Read when: reviewing W5, wiring the sentinel in W6, or tuning its check thresholds.
Status: commit ①+② (pure checks + artifact/alert) DONE + adversarial-verified (2 passes). Wiring = W6.
Date: 2026-06-13
---

# W5 — Market-data quality sentinel (pure checks + artifact + alert)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §1 (Day5),
> §4 "W5+W6", §8 items 2/3/4. **Invariant: v1 is read-only / alert-only / fail-safe — it
> reports, it never blocks trading. Trade-reaction wiring is a later risk-gated step.**

## As-built

| file | role | mine? |
|------|------|-------|
| quality_sentinel.py (not included in this source snapshot) | pure checks → `MarketDataQualityReport`, `build_quality_alert_lines`, atomic `write_market_data_quality_artifact` | yes |
| test_quality_sentinel.py (not included in this source snapshot) | 28 tests / 2 subtests | yes |

One module (not three) — the plan named "순수 체크 + artifact"; it shipped as a single leaf module
since the checks, the report, the formatter, and the writer are tightly coupled around one dataclass.

## Contract (v1)

- **Input:** a sequence of per-symbol `MarketSnapshot` (not included in this source snapshot) records (or
  Mapping equivalents). The W6 caller passes `observed_market_snapshots.values()` from `run_cycle`.
- **Per-symbol checks** (category → meaning):
  - `missing_field` — required price (current/open/low) absent/uncoercible; **and `high_price`** when
    it is the `0` default (see honesty note below).
  - `non_positive_price` — a present price ≤ 0 (high < 0).
  - `price_out_of_range` — **one-sided** bounds fire independently: `current < low`, `current > high`,
    or an inconsistent band `low > high`. A missing `high` disables only the upper bound (and is
    itself flagged), so the lower bound still runs on the production `high_price=0` default.
  - `implausible_daily_move` — `abs(prev_day_change_pct) > max_daily_move_pct` (default `30.0` = KRX
    regular-session price limit). `nan` is dropped; `inf` flags with a JSON-safe string detail.
- **Freshness:** live-snapshot age vs an **interval-derived** threshold `max(refresh_interval*2, 360)`
  — an exact mirror of `live_snapshot_worker_health` (`live_snapshot.py:126`). This is the §8-item-3
  constraint: HT stages use *shorter* intervals (240→60s as r/s climbs 4→15), so the **360s floor**
  is the branch HT exercises — it keeps freshness from tightening below 360s under HT. A non-finite or
  unparseable age never claims "fresh" (reason `age_unavailable`/`updated_at_invalid`) and never
  serializes to `Infinity`.
- **Status:** `"ok"` / `"warn"` only. `warn` iff any issue or stale freshness.
- **Outputs:** `report.to_dict()` (JSON-safe), `build_quality_alert_lines(report)` (empty tuple when
  ok — alert-only), `write_market_data_quality_artifact(report, path=…)` → `data/runtime/market_data_quality.json`.

## Honesty decision — `high_price=0` (operator-tunable)

`build_market_snapshot` sets `high_price=0` when `stck_hgpr` is absent (`schema.py:59`), and the
dataclass defaults it to `0`. A zero high disables the upper-bound range check, so an over-high feed
glitch (e.g. 700,000 printed for a ~70,000 name) coinciding with `high=0` would otherwise report
`ok` — the same "not measured rendered as all-clear" overclaim trap caught in W2/W4. **Decision:**
flag a missing/zero `high_price` as `missing_field` so the skipped upper bound is *visible*. This is
safe because KIS `inquire-price` normally returns `stck_hgpr` (so `high=0` is rare). **If production
turns out to carry legitimate `high=0` frequently (e.g. pre-open), this is the one v1 knob to relax**
(drop high from the missing check, or downgrade to an informational note) — alert-only makes it cheap
to tune. Flagged to the operator as a noted choice.

## Deferred to W6 (not dropped)

- **Coverage alerting:** an empty scan reports `symbol_count=0` in the artifact but emits no alert
  (status `ok`). Distinguishing "checked nothing" from "all clear" in the operator-facing alert is the
  W6 worker-health concern (§4 W5 row scopes coverage to W6) — W6 wiring must alert on `symbol_count==0`.
- **Runtime wiring:** the `run_cycle` call site, the Slack alert emission, and the persistent
  pipeline/backtest heartbeat are W6 (`main_runtime_hooks.py`, a 1-line `app/main.py` call, import-first).
  `live_snapshot_worker_health()` / `summarize_api_budget_state()` are the W6 reuse signals — not
  imported by W5 (correct deferral, verified zero wiring in `app/main.py` / `app/notifications/`).
- **Not in v1:** bid/ask spread, cross-symbol consistency, per-symbol staleness.

## Adversarial verification (2026-06-13)

Two passes — both earned their keep on a pure module:
1. **3 refute-mandate skeptics** (check-logic / safety-honesty-completeness) found and fixed:
   `int(float('inf'))` OverflowError crash → robust `_coerce_price`; `age_seconds` inf/nan → invalid
   JSON / false "fresh" (the W3-class bug) → `math.isfinite` guard; writer `except OSError` too narrow
   + temp-file leak → `except Exception` + `allow_nan=False` + unlink; missing-field detection restored.
2. **5-lens verification workflow** (correctness / failsafe / json_safety / honesty / completeness):
   correctness + json_safety **confirmed clean**; fixed the **upper-bound `high=0` overclaim** (above),
   broadened all coercion/accessor guards to honor the absolute "never raises" contract against hostile
   records (malicious `__float__`, a `.symbol` property that raises, `issues=None` into the formatter),
   added the HT-floor test (short-interval clamp), and de-duped repeated symbols in alert category lines.

## Verification

- `app.market_data.quality_sentinel` imports pull in **no `app.research`** (leaf-invariant asserted).
- 28 tests + 2 subtests; **full suite 2,319 passed, 590 subtests** (exit 0).
- `app/main.py` untouched; no broker/order API touched; read-only, alert-only.
