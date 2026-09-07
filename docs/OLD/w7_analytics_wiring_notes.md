---
Purpose: W7 implementation/contract notes — closed-trade reader + portfolio-analytics report layer,
  and the (dormant) EOD/Slack wiring hook.
Read when: reviewing W7, activating the analytics section in the EOD report / daily summary, or
  consuming the W3 analytics against real order-log data.
Status: reader + loader + renderer DONE + adversarial-verified. EOD `main()` call site + daily_summary
  integration DEFERRED (dormant hook) — operator-gated activation, see below.
Date: 2026-06-13
---

# W7 — Portfolio analytics → operator surface

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §4 "W3+W7", §7 PR-3.
> Depends on W3 ([w3_portfolio_analytics_notes.md](w3_portfolio_analytics_notes.md)); the W3 notes'
> "reader deferred — trigger not in log / FIFO needed" reasoning is **superseded here** (it was wrong).

## As-built

| file | role | mine? |
|------|------|-------|
| trade_records.py (not included in this source snapshot) | `build_closed_trade_records(records, *, settings)` — order log → W3-contract records | yes |
| portfolio_analytics_report.py (not included in this source snapshot) | `load_portfolio_analytics(settings)` (bounded read → analytics) + `render_portfolio_analytics_lines` | yes |
| eod_report_summary.py (not included in this source snapshot) | additive `analytics_lines` param on `format_eod_report_summary` | yes (1 additive param) |

## Key finding — each sell record is self-contained (no FIFO)

A `sell_order_succeeded` order-log event carries everything a closed trade needs:
`raw_response.trigger` (structured exit trigger, written by `app/main.py`),
`raw_response.sell_strategy_details.details.average_cost` (cost basis at sell), `sell_plan.current_price_krw`
(sell price), and qty. So PnL is `calc_net_pnl(average_cost, sell_price, qty, settings)` — **the exact
same computation as `performance.py`'s `build_today_realized_summary`**, on the same field, so the two
agree **record-for-record** (both inherit the broker's integer avg-cost rounding; neither is penny-exact
vs. share-level ground truth — an inherited limitation, not a divergence). Partial / multi-lot / re-bought
positions all map correctly (each partial sell is its own closed trade at the then-current avg cost).

## Scope / limitations (v1)

- **Mock (paper) records only** — live-scoped records skipped.
- **All-time, not today** — every closed trade in the log is included (no date gate); a cumulative
  scorecard that is a *superset* of the today-filtered realized summary. The renderer labels it
  **"realized to date"** so it can't read as "today's" if appended to a daily report.
- **`hold_days = 0.0`** — the sell record carries the *average* cost basis, not a single entry
  timestamp, so holding period isn't derivable per sell. Per-symbol/per-trigger PnL, win rate, and
  turnover are exact; the holding-time distribution is degraded → the renderer **omits** it (rather
  than print a misleading "0 days"). The W3 `to_dict()` still has zeroed `hold_days_*` keys; gate/annotate
  them before any dashboard consumes the JSON artifact.
- **Fail-safe loader** — an oversize (>250MB) / over-long-line / unreadable log degrades to empty
  analytics, never crashes the reporting surface.

## DORMANT hook — EOD/daily_summary activation is deferred (operator-gated)

§4 named `eod_report_summary.py` **and** `daily_summary.py` as wiring targets. What landed: the reader,
the loader, the renderer, and the additive `format_eod_report_summary(analytics_lines=…)` param — but
the eod CLI `main()` **does not yet feed** `analytics_lines`, and `app/notifications/daily_summary.py`
is **untouched**. This matches the conservative posture chosen for the W6 sentinel call (no live-path
activation without operator sign-off). To activate (no trading-critical surface):

```python
# app/tools/eod_report_summary.py main(), non-JSON branch:
from app.auth.settings import get_settings
from app.reporting.portfolio_analytics_report import load_portfolio_analytics, render_portfolio_analytics_lines
analytics_lines = render_portfolio_analytics_lines(load_portfolio_analytics(get_settings()))
print(format_eod_report_summary(summary, analytics_lines=analytics_lines))
```

Note: this reads the **whole** order log (the agent rule forbids that for me; the production tool may).
Decide date-filtering (today vs to-date) at activation — the daily summary already reports today's
realized PnL, so the "to date" scorecard is the complementary view.

## Adversarial verification (2026-06-13)

2 refute-mandate skeptics (reader correctness / honesty-completeness):
- **Math UPHELD** — byte-identical gross/net vs `performance.py` across partial-sell, multi-lot,
  re-buy, and filter scenarios; field contract matches `compute_portfolio_analytics` exactly; loader
  junk-handling safe. Fixed: **loader fail-safe** on oversize log, **"realized to date"** relabel,
  and the docstring's "exact consistency" overclaim.
- **Honesty UPHELD** — `hold_days=0` is omitted from output (not a "0 days" lie); the dormant-hook
  deferral and the corrected W3 trigger claim are now documented (this file + the W3 superseded note).

## Verification

- 12 tests (test_trade_records.py (not included in this source snapshot) ×3,
  test_portfolio_analytics_report.py (not included in this source snapshot) ×4 +
  test_eod_report_summary.py (not included in this source snapshot) analytics param).
- Full suite **2,337 passed, 590 subtests** (exit 0). `app/main.py` untouched; no broker/order API touched.
