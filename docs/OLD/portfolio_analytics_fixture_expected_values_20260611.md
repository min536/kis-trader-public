---
Purpose: Hand-calculated expected values for the W3 portfolio analytics fixture.
Read when: implementing `app/portfolio/analytics.py` or reviewing its tests.
Status: COMPLETE — fixture arithmetic table.
Date: 2026-06-11
---

# W3 Portfolio Analytics Fixture — Expected Values

Fixture source: `tests/fixtures/portfolio_trade_records.py`.

The fixture is synthetic and local-only. It exists so W3 portfolio analytics can
test symbol attribution, trigger attribution, win rate, and holding-period math
without reading order logs or calling broker APIs.

## Trade Records

| trade_id | symbol | trigger | qty | entry | exit | buy notional | sell notional | gross PnL | net PnL | hold days |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| w3_001 | 005930 | take_profit | 10 | 70,000 | 73,000 | 700,000 | 730,000 | 30,000 | 25,000 | 1.0 |
| w3_002 | 005930 | stop_loss | 10 | 72,000 | 70,500 | 720,000 | 705,000 | -15,000 | -18,000 | 1.0 |
| w3_003 | 000660 | take_profit | 5 | 130,000 | 136,000 | 650,000 | 680,000 | 30,000 | 24,000 | 3.0 |
| w3_004 | 035420 | time_exit | 4 | 180,000 | 179,000 | 720,000 | 716,000 | -4,000 | -7,000 | 2.0 |

## Total Expected Values

| metric | value | calculation |
|---|---:|---|
| trade_count | 4 | four closed trades |
| win_count | 2 | `net_pnl_krw > 0`: w3_001, w3_003 |
| loss_count | 2 | w3_002, w3_004 |
| win_rate_pct | 50.0 | `2 / 4 * 100` |
| buy_notional_krw | 2,790,000 | `700,000 + 720,000 + 650,000 + 720,000` |
| sell_notional_krw | 2,831,000 | `730,000 + 705,000 + 680,000 + 716,000` |
| gross_pnl_krw | 41,000 | `30,000 - 15,000 + 30,000 - 4,000` |
| net_pnl_krw | 24,000 | `25,000 - 18,000 + 24,000 - 7,000` |
| avg_hold_days | 1.75 | `(1.0 + 1.0 + 3.0 + 2.0) / 4` |

## By Symbol

| symbol | trade_count | win_rate_pct | net_pnl_krw | avg_hold_days |
|---:|---:|---:|---:|---:|
| 000660 | 1 | 100.0 | 24,000 | 3.0 |
| 005930 | 2 | 50.0 | 7,000 | 1.0 |
| 035420 | 1 | 0.0 | -7,000 | 2.0 |

## By Trigger

| trigger | trade_count | win_rate_pct | net_pnl_krw | avg_hold_days |
|---|---:|---:|---:|---:|
| stop_loss | 1 | 0.0 | -18,000 | 1.0 |
| take_profit | 2 | 100.0 | 49,000 | 2.0 |
| time_exit | 1 | 0.0 | -7,000 | 2.0 |

## W3 Test Expectations

- Analytics should prefer `net_pnl_krw` for win/loss attribution.
- `gross_pnl_krw` remains available for cost-model comparisons.
- Symbol attribution and trigger attribution should both sum back to total net PnL:
  `24,000`.
- The fixture intentionally includes two trades for one symbol (`005930`) and two
  trades for one trigger (`take_profit`) to catch grouping mistakes.
