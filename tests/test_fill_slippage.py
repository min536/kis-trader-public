"""Fill-side slippage pure-calc tests (BP-10 / E7 S1).

Mirrors the submit-side convention in ``app/reporting/slippage_measure.py`` but
reads the actual EOD-reconciled fill price (``fill_price_krw``) instead of
``quote_at_submit``. Pure + offline: no I/O, no broker calls. See
docs/fill_slippage_plan_20260705.md.
"""

from __future__ import annotations

import pytest

from app.reporting.fill_slippage import build_fill_slippage_summary


def test_buy_adverse_fill_is_positive_bps() -> None:
    # buy filled above reference is adverse -> positive bps.
    # (100.5 - 100)/100 * 10000 = 50 bps.
    records = [
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 100.0, "fill_price_krw": 100.5},
        }
    ]
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.buy.count == 1
    assert summary.buy.mean_bps == 50.0
    assert summary.sell.count == 0
    assert summary.sample_count == 1


def test_sell_adverse_fill_is_positive_bps() -> None:
    # sell filled below reference is adverse -> positive bps.
    # -(199 - 200)/200 * 10000 = +50 bps.
    records = [
        {
            "action": "sell_order_succeeded",
            "raw_response": {"reference_price_krw": 200.0, "fill_price_krw": 199.0},
        }
    ]
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.sell.count == 1
    assert summary.sell.mean_bps == 50.0
    assert summary.buy.count == 0


def test_favorable_fill_is_negative_bps() -> None:
    # buy filled below reference is favorable -> negative bps.
    records = [
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 100.0, "fill_price_krw": 99.5},
        }
    ]
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.buy.mean_bps == -50.0


def test_missing_or_bad_fill_price_counts_as_missing_not_raise() -> None:
    records = [
        {"action": "order_succeeded", "raw_response": {"reference_price_krw": 100.0}},
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 100.0, "fill_price_krw": float("inf")},
        },
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 100.0, "fill_price_krw": -5.0},
        },
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 0.0, "fill_price_krw": 100.0},
        },
    ]
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.buy.count == 0
    assert summary.buy.missing_reference_count == 4


def test_hostile_records_never_raise() -> None:
    records = [
        None,
        "not a mapping",
        42,
        {"action": "unrelated_event"},
        {"action": "order_succeeded", "raw_response": None},
        {"action": "order_succeeded", "raw_response": "not a mapping"},
    ]
    # Must not raise on any hostile entry; all degrade to empty/missing.
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.sample_count == 0
    assert summary.buy.count == 0
    assert summary.sell.count == 0


def test_percentiles_match_hand_computation() -> None:
    # buys at +10, +20, +30, +40 bps (fills 100.1, 100.2, 100.3, 100.4 vs ref 100).
    records = [
        {
            "action": "order_succeeded",
            "raw_response": {"reference_price_krw": 100.0, "fill_price_krw": p},
        }
        for p in (100.1, 100.2, 100.3, 100.4)
    ]
    summary = build_fill_slippage_summary(
        records, report_date="2026-07-05", policy_buy_bps=30.0, policy_sell_bps=30.0
    )
    assert summary.buy.count == 4
    assert summary.buy.mean_bps == pytest.approx(25.0)
    # linear-interp p50 over [10,20,30,40] = 25; p75 = 32.5; max = 40.
    assert summary.buy.p50_bps == pytest.approx(25.0)
    assert summary.buy.p75_bps == pytest.approx(32.5)
    assert summary.buy.max_bps == pytest.approx(40.0)
