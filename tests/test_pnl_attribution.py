"""Behavior tests for the daily PnL attribution builder (plan §3 S1).

Pure-function contract: builders take parsed lists/dicts, never touch real
``data/`` files, never raise on hostile input.
"""

from __future__ import annotations


def _buy(symbol: str, ts: str, qty: int, reference_price: float) -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "qty": qty,
        "action": "order_succeeded",
        "account_signature": "mock_acct_test",
        "raw_response": {"reference_price_krw": reference_price},
    }


def _sell(symbol: str, ts: str, qty: int, reference_price: float) -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "qty": qty,
        "action": "sell_order_succeeded",
        "account_signature": "mock_acct_test",
        "raw_response": {"reference_price_krw": reference_price},
    }


def test_attribution_realized_by_symbol_from_paired_orders():
    from app.reporting.pnl_attribution import build_daily_attribution

    orders = [
        _buy("005930", "2026-07-17T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-17T13:00:00+09:00", 10, 110.0),
        _buy("014680", "2026-07-17T10:30:00+09:00", 5, 200.0),
        _sell("014680", "2026-07-17T14:00:00+09:00", 5, 190.0),
    ]

    report = build_daily_attribution(
        orders=orders,
        cycle_tail=[],
        exit_state={},
        target_date="2026-07-17",
    )

    assert report["date"] == "2026-07-17"
    assert report["realized_by_symbol"] == {"005930": 100.0, "014680": -50.0}
    assert report["totals"]["realized_krw"] == 50.0
    assert report["totals"]["trades_closed"] == 2
    assert report["totals"]["trades_opened"] == 2


def test_attribution_uses_recorded_realized_fields_when_present():
    from app.reporting.pnl_attribution import build_daily_attribution

    # Real order-log shape (confirmed 2026-07-18): sell records carry recorded
    # realized PnL at raw_response.sell_strategy_details.details.net_pnl_krw.
    sell = _sell("005930", "2026-07-17T13:00:00+09:00", 27, 290250.0)
    sell["raw_response"]["sell_strategy_details"] = {
        "details": {
            "average_cost": 315490,
            "gross_pnl_krw": -681480,
            "net_pnl_krw": -703866,
        }
    }
    orders = [
        _buy("005930", "2026-07-17T10:00:00+09:00", 27, 315490.0),
        sell,
    ]

    report = build_daily_attribution(
        orders=orders,
        cycle_tail=[],
        exit_state={},
        target_date="2026-07-17",
    )

    # Recorded net_pnl_krw wins over reference-price pairing.
    assert report["realized_by_symbol"] == {"005930": -703866.0}
    assert report["totals"]["realized_krw"] == -703866.0


def _cycle(
    ts: str,
    regime: str,
    *,
    brake: str = "inactive",
    pnl_pct: float = 0.0,
    equity: float = 1_000_000.0,
) -> dict:
    # Keys mirror data/cycle_snapshots_<account>.jsonl (plan §2 실측 키).
    return {
        "timestamp": ts,
        "current_regime": regime,
        "daily_pnl_brake_state": brake,
        "intraday_pnl_pct": pnl_pct,
        "account_signature": "mock_acct_test",
        "equity_krw": equity,
        "intraday_pnl_baseline_krw": 1_000_000.0,
    }


def test_attribution_regime_timeline_and_brake_from_cycle_tail():
    from app.reporting.pnl_attribution import build_daily_attribution

    cycle_tail = [
        _cycle("2026-07-16T15:20:00+09:00", "risk_off"),  # 전일 — 제외
        _cycle("2026-07-17T09:00:00+09:00", "neutral"),
        _cycle("2026-07-17T09:30:00+09:00", "neutral"),
        _cycle("2026-07-17T10:00:00+09:00", "bull"),
        _cycle("2026-07-17T10:30:00+09:00", "bull"),
        _cycle(
            "2026-07-17T11:00:00+09:00",
            "bull",
            brake="active",
            pnl_pct=-1.25,
            equity=1_010_000.0,
        ),
    ]

    report = build_daily_attribution(
        orders=[],
        cycle_tail=cycle_tail,
        exit_state={},
        target_date="2026-07-17",
    )

    # Run-length timeline over the target date only, in chronological order.
    assert report["regime_timeline"] == [("neutral", 2), ("bull", 3)]
    assert report["brake_state_last"] == "active"
    assert report["intraday_pnl_pct_last"] == -1.25
    assert report["account_signature"] == "mock_acct_test"
    # Unrealized delta = (equity - intraday baseline) - realized total.
    # No realized trades here → the full +10,000 intraday move is unrealized.
    assert report["unrealized_delta_krw"] == 10_000.0


def test_attribution_never_raises_on_empty_inputs():
    from app.reporting.pnl_attribution import build_daily_attribution

    # Empty inputs → complete structure with degraded (None/empty) sections.
    report = build_daily_attribution(
        orders=[], cycle_tail=[], exit_state={}, target_date="2026-07-17"
    )
    assert report["date"] == "2026-07-17"
    assert report["account_signature"] is None
    assert report["realized_by_symbol"] == {}
    assert report["unrealized_delta_krw"] is None
    assert report["slippage_summary"] is None
    assert report["regime_timeline"] == []
    assert report["brake_state_last"] is None
    assert report["intraday_pnl_pct_last"] is None
    assert report["totals"] == {
        "realized_krw": 0.0,
        "trades_closed": 0,
        "trades_opened": 0,
    }

    # Hostile inputs (wrong types, junk records) degrade instead of raising.
    hostile = build_daily_attribution(
        orders=[None, "junk", {"action": "sell_order_succeeded"}, {"timestamp": 3}],  # type: ignore[list-item]
        cycle_tail=[None, {"timestamp": "2026-07-17T09:00:00+09:00", "current_regime": 7}],  # type: ignore[list-item]
        exit_state={"last_exit_reason_by_symbol": None},
        target_date="2026-07-17",
    )
    assert hostile["realized_by_symbol"] == {}
    assert hostile["regime_timeline"] == []


def test_attribution_slippage_section_optional():
    from app.reporting.pnl_attribution import build_daily_attribution

    # No fill records on the target date → section degrades to None.
    no_fills = build_daily_attribution(
        orders=[
            {
                "timestamp": "2026-07-17T09:00:00+09:00",
                "symbol": "",
                "action": "buy_scan_budget_reserved",
                "raw_response": {"engine_event": True},
            }
        ],
        cycle_tail=[],
        exit_state={},
        target_date="2026-07-17",
    )
    assert no_fills["slippage_summary"] is None

    # Fill records with reference + fill prices → adopted
    # build_fill_slippage_summary result as a plain dict (JSON-safe).
    buy = _buy("005930", "2026-07-17T10:00:00+09:00", 10, 100_000.0)
    buy["raw_response"]["fill_price_krw"] = 100_100.0
    sell = _sell("005930", "2026-07-17T13:00:00+09:00", 10, 110_000.0)
    sell["raw_response"]["fill_price_krw"] = 109_890.0

    with_fills = build_daily_attribution(
        orders=[buy, sell],
        cycle_tail=[],
        exit_state={},
        target_date="2026-07-17",
    )
    summary = with_fills["slippage_summary"]
    assert isinstance(summary, dict)
    assert summary["report_date"] == "2026-07-17"
    assert summary["sample_count"] == 2
    assert summary["buy"]["count"] == 1
    assert summary["sell"]["count"] == 1
    # adverse-positive convention: buy above / sell below reference.
    assert summary["buy"]["mean_bps"] > 0
    assert summary["sell"]["mean_bps"] > 0


def test_render_attribution_lines_korean_sections():
    from app.reporting.pnl_attribution import (
        build_daily_attribution,
        render_attribution_lines,
    )

    orders = [
        _buy("005930", "2026-07-17T10:00:00+09:00", 10, 100.0),
        _sell("005930", "2026-07-17T13:00:00+09:00", 10, 110.0),
    ]
    cycle_tail = [
        _cycle("2026-07-17T11:00:00+09:00", "bull", brake="active", pnl_pct=0.42),
    ]
    report = build_daily_attribution(
        orders=orders,
        cycle_tail=cycle_tail,
        exit_state={},
        target_date="2026-07-17",
    )

    lines = render_attribution_lines(report)
    assert isinstance(lines, list)
    assert all(isinstance(line, str) for line in lines)
    joined = "\n".join(lines)
    # 한국어 섹션: 헤더 날짜, 실현손익 심볼별, 미실현, 레짐, 브레이크.
    assert "2026-07-17" in joined
    assert "실현" in joined
    assert "005930" in joined
    assert "미실현" in joined
    assert "레짐" in joined
    assert "브레이크" in joined
    assert "bull" in joined

    # Renderer never raises on the fully-degraded empty report either.
    empty_lines = render_attribution_lines(
        build_daily_attribution(
            orders=[], cycle_tail=[], exit_state={}, target_date="2026-07-17"
        )
    )
    assert isinstance(empty_lines, list) and empty_lines
