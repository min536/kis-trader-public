"""Behavior tests for the weekly trade postmortem CLI (plan §3 S4).

Hermetic: paths resolve under a per-test ``KIS_STATE_ROOT`` (tmp_path) with
synthetic JSONL; ``now`` is injected for time-stable windows. No network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 17, 15, 30, tzinfo=KST)
SIG = "mock_acct_pm_cli"


def _order(action: str, symbol: str, ts: str, qty: int, reference_price: float) -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "qty": qty,
        "action": action,
        "account_signature": SIG,
        "raw_response": {
            "reference_price_krw": reference_price,
            "trigger": "stop_loss" if action == "sell_order_succeeded" else None,
        },
    }


def _write_fixture_root(tmp_path):
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()

    orders = [
        # Closed 10 days ago (outside a 7-day window).
        _order("order_succeeded", "000660", "2026-07-07T10:00:00+09:00", 5, 100.0),
        _order("sell_order_succeeded", "000660", "2026-07-07T14:00:00+09:00", 5, 120.0),
        # Closed 1 day ago (inside).
        _order("order_succeeded", "005930", "2026-07-16T10:00:00+09:00", 10, 100.0),
        _order("sell_order_succeeded", "005930", "2026-07-16T13:00:00+09:00", 10, 110.0),
    ]
    (logs_dir / f"orders_{SIG}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in orders) + "\n"
    )
    (data_dir / f"runtime_state_{SIG}.json").write_text(
        json.dumps({"last_exit_reason_by_symbol": {"005930": "stop_loss"}})
    )
    return tmp_path


def test_cli_builds_weekly_report_from_tmp_data_dir(tmp_path, monkeypatch):
    from app.tools.trade_postmortem_report import run

    _write_fixture_root(tmp_path)
    monkeypatch.setenv("KIS_STATE_ROOT", str(tmp_path))

    result = run(account=SIG, days=7, now=NOW)

    assert len(result["reports"]) == 1
    report = result["reports"][0]
    assert report["window"]["days"] == 7
    assert len(report["closed_trades"]) == 1
    trade = report["closed_trades"][0]
    assert trade["symbol"] == "005930"
    assert trade["pnl_krw"] == 100.0
    assert trade["exit_reason"] == "stop_loss"
    assert report["per_symbol"] == {
        "005930": {"trades": 1, "wins": 1, "net_krw": 100.0}
    }


def test_cli_days_option_bounds_window(tmp_path, monkeypatch):
    from app.tools.trade_postmortem_report import run

    _write_fixture_root(tmp_path)
    monkeypatch.setenv("KIS_STATE_ROOT", str(tmp_path))

    # days=7 → only the recent trade; days=14 → both round-trips.
    narrow = run(account=SIG, days=7, now=NOW)
    assert len(narrow["reports"][0]["closed_trades"]) == 1
    assert narrow["reports"][0]["window"]["days"] == 7

    wide = run(account=SIG, days=14, now=NOW)
    report = wide["reports"][0]
    assert report["window"]["days"] == 14
    assert sorted(trade["symbol"] for trade in report["closed_trades"]) == [
        "000660",
        "005930",
    ]


def test_trade_postmortem_event_routes_to_summary_channel():
    from app.notifications.slack import (
        EVENT_CHANNEL_ENV_BY_TYPE,
        SUMMARY_CHANNEL_ENV,
        TRADE_POSTMORTEM_EVENT_TYPE,
    )

    assert TRADE_POSTMORTEM_EVENT_TYPE == "trade_postmortem"
    assert (
        EVENT_CHANNEL_ENV_BY_TYPE[TRADE_POSTMORTEM_EVENT_TYPE] == SUMMARY_CHANNEL_ENV
    )
