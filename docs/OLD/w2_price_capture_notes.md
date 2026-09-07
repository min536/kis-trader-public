---
Purpose: W2 implementation/contract notes — order-record price capture for slippage prep.
Read when: implementing or reviewing W2 (PR-2), or building the W4 slippage report/measure tool.
Status: DONE (code commit) + adversarial-verified. Direct (non-delegated) Core+gated task.
Date: 2026-06-13
---

# W2 — 주문 레코드 가격 캡처 필드 (PR-2)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 W2 / §5
> trading-critical 접점. Invariant: additive-only, try/except-isolated, no broker call, no
> order-flow behavior change. `/risk-assessment` gate passed (residual **LOW**).

## What W2 adds

Two additive fields on the order **submit** and **succeed** records of both flows
(`build_price_capture` in app/core/order_log.py (not included in this source snapshot); call sites in
app/execution/buy_flow.py (not included in this source snapshot) and
app/execution/sell_flow.py (not included in this source snapshot)):

| field | meaning | source today |
|-------|---------|--------------|
| `reference_price_krw` | decision/reference price the order was sized against | buy: `planned_execution_snapshot.current_price`; sell: `analysis.market_snapshot.current_price` |
| `quote_at_submit` | best quote available at submit time | buy: `execution_snapshot.current_price`; sell: same snapshot |

Events stamped: `order_submitted` + `order_succeeded` (buy), `sell_order_submitted` +
`sell_order_succeeded` (sell). Blocked/failed records are **not** stamped and stay
byte-identical to pre-W2.

## Design / isolation

- `build_price_capture` is **pure and total**: coerces each price to a finite `float`,
  **omits** `None`/`bool`/non-finite/unparseable/overflowing values (e.g. `float(10**400)`
  → `OverflowError` is swallowed), and **never raises** (`except Exception`). An omitted
  field ⇒ the slippage consumer counts a missing reference.
- Each call site **also** wraps the snapshot attribute extraction in `try/except → {}` —
  defence-in-depth so a recording failure can never abort, alter, delay, or reorder the
  order path. (Adversarially confirmed: the call-site guard is the load-bearing one.)
- Additive merge via `**capture` (last); grep-verified the two names exist nowhere else,
  so no `raw_response` key collision.

## ⚠️ Scope is the SUBMIT side only — fill price is NOT captured (read before building W4)

`quote_at_submit == reference_price_krw` **today** for both sides: there is no re-quote
fetched between decision and the `buy_market`/`sell_market` call, and sell uses one
snapshot. Both fields are stamped for forward-compat (the day a submit-time re-quote is
added they diverge).

**Real slippage `= f(fill, reference)` is not computable from these records yet.** The KIS
order-cash ack (`order_response`) carries the order id (`output.odno`) and time, but **no
executed/fill price**, and the repo has **no executed-orders inquiry** (체결조회 / TTTC0081R)
anywhere. The existing realized-PnL path uses the decision snapshot price as the execution
proxy (app/reporting/performance.py (not included in this source snapshot) ~`947`), i.e. there is
no real fill price in the system.

So W2 deliberately captures only the **submit-side reference**, durably + joinable by
`odno`. Closing the loop is **W4 / operator**:

- A **fill source** is required — either a post-fill executed-orders inquiry (a **broker
  call → operator/risk gate**, not an agent action) or EOD/balance reconciliation that
  recovers the executed price.
- Mock fills are immediate market fills, so live-shadow data is needed for real
  calibration ([five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 W4).
- The W4 reader must **not** assume `order_response` contains a fill price.

## Adversarial verification (2026-06-13)

3 parallel skeptics (refute-mandate) against commit `fb08c40`:

- **Backward-compat: UPHELD** — purely additive; only 6 W2-owned files reference the
  names; every order-record reader uses targeted `.get()` (no whitelist/closed-schema);
  blocked/failed records byte-identical.
- **Order-path safety: UPHELD** — no exception can reach the order flow; no unbound-var
  path; broker objects not mutated. Found + fixed a contract bug: `_coerce_price` caught
  only `(TypeError, ValueError)` so `OverflowError` escaped the helper (still caught by the
  call-site wrapper). Hardened to `except Exception` + regression test.
- **Consumer correctness: refuted an overclaim** — slippage is not computable now (no fill
  source). Captured above as the explicit W4 follow-up; the capture itself is the correct
  submit-side half.

## Verification

- Full suite green: `.venv/bin/python -m pytest -q` → **2263 passed, 453 subtests** (exit 0).
- New tests: `tests/test_order_log_price_capture.py` (5: happy/non-numeric/non-finite/bool/
  overflow), + 1 buy + 1 sell characterization assertion.
- `app/main.py` untouched; no broker/order API touched; no NOT-mine working-tree file touched.
