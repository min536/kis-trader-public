---
Purpose: W3 implementation/contract notes — pure portfolio analytics layer.
Read when: implementing/reviewing W3, or building W7 (analytics → EOD/Slack/dashboard).
Status: commit ① (pure metrics) DONE + adversarial-verified. commit ② (reader) DEFERRED to W7.
Date: 2026-06-13
---

# W3 — `app/portfolio/analytics.py` 순수 메트릭 (PR-3)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 "W3+W7" / §7 PR-3.
> Pure & offline. No I/O, no broker calls. Not on the trading-critical surface →
> no `/risk-assessment` gate (cf. W2). Equity-curve/MDD/Sharpe stay in
> performance.py (not included in this source snapshot); analytics owns only the new
> synthetic-attribution metrics (no duplication).

## What W3 v1 ships (commit ① — pure metrics)

`compute_portfolio_analytics(records) -> PortfolioAnalytics`
(analytics.py (not included in this source snapshot)), re-exported from
`app.portfolio` (not included in this source snapshot). Every §4 W3 metric bullet is
delivered **and** tested (tests/test_portfolio_analytics.py (not included in this source snapshot),
14 tests / 90 subtests):

| §4 spec bullet | output | 
|---|---|
| per-symbol realized PnL | `by_symbol[sym].net_pnl_krw` (+`gross_pnl_krw`) |
| per-trigger realized PnL | `by_trigger[trig].net_pnl_krw` |
| per-symbol win rate | `by_symbol[sym].win_rate_pct` |
| average holding time | `GroupMetrics.avg_hold_days` (every group) |
| per-symbol turnover | `by_symbol[sym].turnover_krw` (= buy+sell notional) |
| holding-time distribution (percentile) | `min/max_hold_days`, `hold_days_p50`, `hold_days_p90` (linear-interp, numpy 'linear') |
| symbol×trigger matrix | `by_symbol_trigger[sym][trig] -> GroupMetrics` |

`PortfolioAnalytics.to_dict()` renders a JSON-serializable nested dict — the
intended W7 EOD/Slack/dashboard surface.

### Input record contract (what a normalized closed trade looks like)

```
{ "symbol": "005930", "sell_trigger": "take_profit", "hold_days": 1.0,
  "buy_notional_krw": 700_000, "sell_notional_krw": 730_000,
  "gross_pnl_krw": 30_000, "net_pnl_krw": 25_000 }
```

Contract is pinned by the synthetic fixture
tests/fixtures/portfolio_trade_records.py (not included in this source snapshot)
+ hand-calculated table
[portfolio_analytics_fixture_expected_values_20260611.md](portfolio_analytics_fixture_expected_values_20260611.md).

### Semantics

- **Win/loss on `net_pnl_krw`** (cost-aware): `>0` win, `<0` loss, `==0` breakeven.
  `win_rate_pct` uses `trade_count` as denominator (a breakeven lowers win rate
  without counting as a loss).
- **Attribution sums back**: per-symbol net, per-trigger net, and every matrix cell
  all sum to total net (tested invariants).
- **Defensive/total**: non-`Mapping` records skipped; numeric fields coerced to
  finite numbers (exact ints preserved, non-finite/bool→0); a derived average is
  re-coerced so an overflowing sum can't leak `inf` into the JSON artifact; missing
  symbol/trigger → `UNKNOWN`/`unknown` bucket.

## ⚠️ commit ② (order-log reader) is DEFERRED to W7 — read before wiring real data

> **SUPERSEDED by W7 (2026-06-13, [w7_analytics_wiring_notes.md](w7_analytics_wiring_notes.md)).**
> Two claims below were **wrong** and W7 disproved them by inspecting real records:
> (1) the structured sell **trigger IS in the log** — `raw_response.trigger` is written by
> `app/main.py` (`sell_raw_response["trigger"] = sell_position_sizing.sell_trigger`), so
> per-trigger analytics is real, not an "unknown" bucket; (2) **no FIFO pairing is needed** —
> each `sell_order_succeeded` is self-contained (cost basis in
> `sell_strategy_details.details.average_cost`, sell price in `sell_plan.current_price_krw`),
> so PnL is computed exactly as `performance.py`'s realized summary, record-for-record. The only
> real gap was `hold_days` (no per-position entry timestamp) → set 0.0 and omitted from output.
> The original (now-incorrect) reasoning is kept below for history.

W3 v1 is the **pure metrics layer only**. The optional "(선택)" record reader
`app/portfolio/trade_records.py` (order log → normalized trade records) is **not
built**. This is a deliberate, evidence-based deferral, not an omission:

The `sell_order_succeeded` order-log record **cannot** be turned into the input
contract above on its own:

- **Sell trigger is NOT in the log.** The structured exit trigger
  (`exit_reason`/`triggered_rule_name`/`net_pnl_pct`) is routed to in-memory state
  via `record_symbol_exit(...)` (sell_flow.py:492 (not included in this source snapshot)),
  **not** written to the order log. The only trigger-ish field in the log record is
  `reason` — a free-text summary string.
- **No entry timestamp / no realized PnL in the sell event.** So `hold_days`,
  `sell_trigger`, and PnL all require **FIFO buy↔sell pairing**.
- **Only existing FIFO pairer** is the dashboard's `_build_trade_journal`
  (normalizers.py:760 (not included in this source snapshot)) — and it yields only
  `sell_reason` (free text) + `hold_mins`, **not** PnL or notional.

So a real-data reader is a *wiring* task that must reuse the dashboard journal
(rather than duplicate FIFO pairing) and reconcile PnL from the sell record's
`sell_strategy_details`/`sell_plan` — which is exactly W7's scope
(analytics → EOD/Slack/dashboard). The plan marks the reader optional and §7 PR-3
scopes W3 to "pure metrics only, W7 integration excluded".

> Note: todo.md (not included in this source snapshot) §A still reads as if PR-3 covers commits ①~②. After this
> deferral, PR-3 = commit ① only; ② moves into the W7 work item. (todo.md is owned
> elsewhere in the working tree — not edited here.)

### W7 field-source map (so W7 doesn't re-derive this)

| analytics input | real source |
|---|---|
| `symbol`, `buy/sell_notional_krw`, `gross/net_pnl_krw` | sell record `sell_plan` + `sell_strategy_details.details` (cost basis `average_cost`, embedded `gross_pnl_krw`/`net_pnl_krw`); cf. performance.py:908 (not included in this source snapshot) `build_today_realized_summary` |
| `hold_days` | FIFO buy↔sell pairing of order-log timestamps (à la `_build_trade_journal`) |
| `sell_trigger` (structured) | `record_symbol_exit` → runtime state; free-text fallback = sell event `reason` |

## Adversarial verification (2026-06-13)

3 parallel refute-mandate skeptics (math / robustness / completeness):

- **Math: UPHELD** — output matches the hand-calculated fixture exactly; `_percentile`
  is bit-for-bit equal to `numpy.percentile` (linear) at all boundaries; all
  sums-back invariants hold.
- **Robustness: 3 real gaps found + fixed** — (1) a non-`Mapping` element
  (`[None]`) crashed the whole call → now skipped; (2) a finite-but-huge `hold_days`
  let `avg = sum/n` overflow to `inf` past the input `isfinite` guard, emitting
  invalid JSON (`Infinity`) → derived average re-coerced; (3) `_to_int` routed ints
  through `float()`, losing precision >2^53 and zeroing `10**400` → now preserves
  exact ints. Each fix landed test-first.
- **Completeness: UPHELD with one doc gap** — every metric built + tested; reader
  deferral judged defensible; the missing piece was *this hand-off note*.

## Verification

- Full suite green: `.venv/bin/python -m pytest -q` → **2277 passed, 543 subtests** (exit 0).
- New: tests/test_portfolio_analytics.py (not included in this source snapshot)
  (14 tests / 90 subtests: total, by_symbol, by_trigger, hold-days distribution,
  symbol×trigger matrix, package re-export, + edge cases empty/breakeven/coercion/
  non-Mapping/non-finite-JSON/large-int/to_dict).
- `app/main.py` untouched; no broker/order API touched.
- Test depends on the aux-authored fixture `tests/fixtures/portfolio_trade_records.py`
  (the shared W3 contract artifact) — that fixture must land in the same PR.
