"""Behavior tests for the daily PnL attribution CLI (plan §3 S2).

Hermetic: every path resolves under a per-test ``KIS_STATE_ROOT`` (tmp_path)
with synthetic JSONL — the real ``data/``/``logs/`` are never touched. No
network; Slack send is an injected spy.
"""

from __future__ import annotations

import json

SIG = "mock_acct_cli_test"


def _write_fixture_root(tmp_path):
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()

    orders = [
        {
            "timestamp": "2026-07-17T10:00:00+09:00",
            "symbol": "005930",
            "qty": 10,
            "action": "order_succeeded",
            "account_signature": SIG,
            "raw_response": {"reference_price_krw": 100.0},
        },
        {
            "timestamp": "2026-07-17T13:00:00+09:00",
            "symbol": "005930",
            "qty": 10,
            "action": "sell_order_succeeded",
            "account_signature": SIG,
            "raw_response": {"reference_price_krw": 110.0},
        },
    ]
    (logs_dir / f"orders_{SIG}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in orders) + "\n"
    )

    cycles = [
        {
            "timestamp": "2026-07-17T11:00:00+09:00",
            "current_regime": "bull",
            "daily_pnl_brake_state": "inactive",
            "intraday_pnl_pct": 0.5,
            "account_signature": SIG,
            "equity_krw": 1_000_500.0,
            "intraday_pnl_baseline_krw": 1_000_000.0,
        }
    ]
    (data_dir / f"cycle_snapshots_{SIG}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in cycles) + "\n"
    )

    (data_dir / f"runtime_state_{SIG}.json").write_text(
        json.dumps({"last_exit_reason_by_symbol": {"005930": "stop_loss"}})
    )
    return tmp_path


def test_cli_builds_report_from_tmp_data_dir(tmp_path, monkeypatch):
    from app.tools.pnl_attribution_report import main

    _write_fixture_root(tmp_path)
    monkeypatch.setenv("KIS_STATE_ROOT", str(tmp_path))

    result = main(["--account", SIG, "--date", "2026-07-17"])

    assert result["date"] == "2026-07-17"
    assert len(result["reports"]) == 1
    report = result["reports"][0]
    assert report["account_signature"] == SIG
    assert report["realized_by_symbol"] == {"005930": 100.0}
    assert report["regime_timeline"] == [("bull", 1)]
    assert report["totals"]["trades_closed"] == 1


def test_cli_slack_send_optional_and_failure_swallowed(tmp_path, monkeypatch):
    from app.tools.pnl_attribution_report import run

    _write_fixture_root(tmp_path)
    monkeypatch.setenv("KIS_STATE_ROOT", str(tmp_path))

    # notify=None (no --slack) → nothing sent, report still builds.
    silent = run(account=SIG, target_date="2026-07-17", notify=None)
    assert silent["reports"][0]["realized_by_symbol"] == {"005930": 100.0}

    # Raising notifier → swallowed; the report survives untouched.
    calls: list[tuple] = []

    def _raising_notify(event_type, message, *, symbol, details):
        calls.append((event_type, message, symbol, details))
        raise RuntimeError("slack down")

    result = run(account=SIG, target_date="2026-07-17", notify=_raising_notify)

    assert len(calls) == 1
    assert calls[0][0] == "pnl_attribution"
    assert "실현" in calls[0][1]
    assert result["reports"][0]["realized_by_symbol"] == {"005930": 100.0}


def test_cli_json_out_writes_report(tmp_path, monkeypatch):
    from app.tools.pnl_attribution_report import main

    _write_fixture_root(tmp_path)
    monkeypatch.setenv("KIS_STATE_ROOT", str(tmp_path))
    out_path = tmp_path / "out" / "attribution.json"

    main(
        [
            "--account",
            SIG,
            "--date",
            "2026-07-17",
            "--json-out",
            str(out_path),
        ]
    )

    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["date"] == "2026-07-17"
    report = payload["reports"][0]
    assert report["account_signature"] == SIG
    assert report["realized_by_symbol"] == {"005930": 100.0}
    # JSON round-trip turns the timeline tuples into lists.
    assert report["regime_timeline"] == [["bull", 1]]


def test_pnl_attribution_event_routes_to_summary_channel():
    from app.notifications.slack import (
        EVENT_CHANNEL_ENV_BY_TYPE,
        PNL_ATTRIBUTION_EVENT_TYPE,
        SUMMARY_CHANNEL_ENV,
    )

    assert PNL_ATTRIBUTION_EVENT_TYPE == "pnl_attribution"
    assert EVENT_CHANNEL_ENV_BY_TYPE[PNL_ATTRIBUTION_EVENT_TYPE] == SUMMARY_CHANNEL_ENV
