"""Behavior tests for the weekly trade postmortem builder (plan §3 S3).

Pure-function contract: parsed lists/dicts in, report dict out; no real
``data/`` access, never raises.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 17, 15, 30, tzinfo=KST)


def _buy(symbol: str, ts: str, qty: int, reference_price: float) -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "qty": qty,
        "action": "order_succeeded",
        "account_signature": "mock_acct_test",
        "raw_response": {"reference_price_krw": reference_price},
    }


def _sell(
    symbol: str,
    ts: str,
    qty: int,
    reference_price: float,
    *,
    trigger: str | None = None,
) -> dict:
    raw: dict = {"reference_price_krw": reference_price}
    if trigger is not None:
        raw["trigger"] = trigger
        raw["sell_strategy_details"] = {"triggered_rule_name": trigger}
    return {
        "timestamp": ts,
        "symbol": symbol,
        "qty": qty,
        "action": "sell_order_succeeded",
        "account_signature": "mock_acct_test",
        "raw_response": raw,
    }


def test_postmortem_pairs_closed_trades_within_window():
    from app.reporting.trade_postmortem import build_weekly_postmortem

    orders = [
        # Closed 9 days ago — outside the 7-day window.
        _buy("000660", "2026-07-08T10:00:00+09:00", 5, 100.0),
        _sell("000660", "2026-07-08T14:00:00+09:00", 5, 120.0),
        # Closed inside the window.
        _buy("005930", "2026-07-16T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-16T13:30:00+09:00", 10, 110.0),
        # Still open — never a closed trade.
        _buy("014680", "2026-07-17T10:00:00+09:00", 3, 200.0),
    ]

    report = build_weekly_postmortem(
        orders=orders,
        exit_state={},
        now=NOW,
        window_days=7,
    )

    assert report["window"]["days"] == 7
    assert report["window"]["end"] == NOW.isoformat()

    assert len(report["closed_trades"]) == 1
    trade = report["closed_trades"][0]
    assert trade["symbol"] == "005930"
    assert trade["entry_at"] == "2026-07-16T10:00:00+09:00"
    assert trade["exit_at"] == "2026-07-16T13:30:00+09:00"
    assert trade["hold_minutes"] == 210
    assert trade["pnl_krw"] == 100.0
    assert trade["pnl_pct"] == 10.0


def test_postmortem_exit_reason_distribution_with_fallback():
    from app.reporting.trade_postmortem import build_weekly_postmortem

    orders = [
        # Reason recorded in the order log itself.
        _buy("005930", "2026-07-16T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-16T13:00:00+09:00", 10, 90.0, trigger="stop_loss"),
        # No order-log reason; 014680's LATEST exit → exit_state fallback.
        _buy("014680", "2026-07-16T11:00:00+09:00", 5, 200.0),
        _sell("014680", "2026-07-16T14:00:00+09:00", 5, 210.0),
        # No order-log reason and NOT the symbol's latest exit → unknown.
        _buy("005930", "2026-07-15T10:00:00+09:00", 4, 100.0),
        _sell("005930", "2026-07-15T11:00:00+09:00", 4, 101.0),
    ]
    exit_state = {
        "last_exit_reason_by_symbol": {"014680": "take_profit", "005930": "stop_loss"}
    }

    report = build_weekly_postmortem(
        orders=orders,
        exit_state=exit_state,
        now=NOW,
        window_days=7,
    )

    by_key = {
        (trade["symbol"], trade["exit_at"]): trade for trade in report["closed_trades"]
    }
    logged = by_key[("005930", "2026-07-16T13:00:00+09:00")]
    assert logged["exit_reason"] == "stop_loss"
    assert logged["reason_source"] == "order_log"

    fallback = by_key[("014680", "2026-07-16T14:00:00+09:00")]
    assert fallback["exit_reason"] == "take_profit"
    assert fallback["reason_source"] == "exit_state"

    # 005930's earlier exit cannot borrow the exit_state entry (it only
    # describes the most recent exit) and the log has no reason → unknown.
    earlier = by_key[("005930", "2026-07-15T11:00:00+09:00")]
    assert earlier["exit_reason"] is None
    assert earlier["reason_source"] is None

    assert report["exit_reason_distribution"] == {
        "stop_loss": 1,
        "take_profit": 1,
        "unknown": 1,
    }


def test_postmortem_per_symbol_repeat_performance():
    from app.reporting.trade_postmortem import build_weekly_postmortem

    orders = [
        # 005930: two closed round-trips — one win (+100), one loss (-40).
        _buy("005930", "2026-07-15T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-15T13:00:00+09:00", 10, 110.0),
        _buy("005930", "2026-07-16T10:00:00+09:00", 4, 100.0),
        _sell("005930", "2026-07-16T13:00:00+09:00", 4, 90.0),
        # 014680: one winning round-trip (+50).
        _buy("014680", "2026-07-16T11:00:00+09:00", 5, 200.0),
        _sell("014680", "2026-07-16T14:00:00+09:00", 5, 210.0),
    ]

    report = build_weekly_postmortem(
        orders=orders,
        exit_state={},
        now=NOW,
        window_days=7,
    )

    assert report["per_symbol"] == {
        "005930": {"trades": 2, "wins": 1, "net_krw": 60.0},
        "014680": {"trades": 1, "wins": 1, "net_krw": 50.0},
    }


def test_postmortem_summary_win_rate_and_profit_factor():
    from app.reporting.trade_postmortem import build_weekly_postmortem

    orders = [
        # Wins: +100, +50 · losses: -40, -10.
        _buy("005930", "2026-07-15T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-15T13:00:00+09:00", 10, 110.0),
        _buy("014680", "2026-07-15T11:00:00+09:00", 5, 200.0),
        _sell("014680", "2026-07-15T14:00:00+09:00", 5, 210.0),
        _buy("005930", "2026-07-16T10:00:00+09:00", 4, 100.0),
        _sell("005930", "2026-07-16T13:00:00+09:00", 4, 90.0),
        _buy("000660", "2026-07-16T11:00:00+09:00", 1, 100.0),
        _sell("000660", "2026-07-16T14:00:00+09:00", 1, 90.0),
    ]

    report = build_weekly_postmortem(
        orders=orders,
        exit_state={},
        now=NOW,
        window_days=7,
    )

    summary = report["summary"]
    assert summary["win_rate"] == 0.5
    assert summary["avg_win_krw"] == 75.0
    assert summary["avg_loss_krw"] == -25.0
    assert summary["profit_factor"] == 3.0

    # No losses → profit factor is undefined (None), never a division error.
    all_wins = build_weekly_postmortem(
        orders=orders[:4],
        exit_state={},
        now=NOW,
        window_days=7,
    )
    assert all_wins["summary"]["win_rate"] == 1.0
    assert all_wins["summary"]["avg_loss_krw"] is None
    assert all_wins["summary"]["profit_factor"] is None


def test_postmortem_never_raises_on_empty_inputs():
    from app.reporting.trade_postmortem import build_weekly_postmortem

    report = build_weekly_postmortem(orders=[], exit_state={}, now=NOW)
    assert report["window"] == {
        "start": (NOW - timedelta(days=7)).isoformat(),
        "end": NOW.isoformat(),
        "days": 7,
    }
    assert report["closed_trades"] == []
    assert report["exit_reason_distribution"] == {}
    assert report["per_symbol"] == {}
    assert report["summary"] == {
        "win_rate": None,
        "avg_win_krw": None,
        "avg_loss_krw": None,
        "profit_factor": None,
    }

    # Hostile inputs degrade instead of raising.
    hostile = build_weekly_postmortem(
        orders=[
            None,  # type: ignore[list-item]
            "junk",  # type: ignore[list-item]
            {"action": "sell_order_succeeded", "symbol": "005930", "timestamp": "bad-ts"},
            {"action": "sell_order_succeeded", "symbol": "", "qty": None},
        ],
        exit_state={"last_exit_reason_by_symbol": "not-a-dict"},
        now=NOW,
    )
    assert hostile["closed_trades"] == []
    assert hostile["summary"]["win_rate"] is None


def test_render_postmortem_lines_korean_sections():
    from app.reporting.trade_postmortem import (
        build_weekly_postmortem,
        render_postmortem_lines,
    )

    orders = [
        _buy("005930", "2026-07-15T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-15T13:00:00+09:00", 10, 110.0, trigger="take_profit"),
        _buy("014680", "2026-07-16T10:00:00+09:00", 4, 100.0),
        _sell("014680", "2026-07-16T13:00:00+09:00", 4, 90.0, trigger="stop_loss"),
    ]
    report = build_weekly_postmortem(orders=orders, exit_state={}, now=NOW)

    lines = render_postmortem_lines(report)
    assert isinstance(lines, list)
    assert all(isinstance(line, str) for line in lines)
    joined = "\n".join(lines)
    # 한국어 섹션: 헤더, 청산 요약, 청산 사유 분포, 심볼별 성과, 보유시간.
    assert "포스트모템" in joined
    assert "청산" in joined
    assert "승률" in joined
    assert "사유" in joined
    assert "보유" in joined
    assert "005930" in joined
    assert "take_profit" in joined
    assert "stop_loss" in joined

    # Renderer never raises on the fully-degraded empty report.
    empty_lines = render_postmortem_lines(
        build_weekly_postmortem(orders=[], exit_state={}, now=NOW)
    )
    assert isinstance(empty_lines, list) and empty_lines
