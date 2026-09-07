"""Behavior tests for the morning-briefing CLI (plan §3 S2).

Hermetic: every path resolves under a per-test tmp ``--data-dir`` with synthetic
JSONL/JSON — the real ``data/`` is never touched. No network; Slack send is an
injected spy.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SIG = "mock_acct_briefing_test"


def _write_data_dir(tmp_path, *, now: datetime):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    date_str = now.date().isoformat()

    snapshot = {
        "timestamp": (now - timedelta(minutes=5)).isoformat(),
        "environment": "mock",
        "masked_account_display": "5008****11",
        "account_signature": SIG,
        "equity_krw": 1_050_000,
        "cash_krw": 500_000,
        "orderable_cash_krw": 300_000,
        "intraday_pnl_pct": 1.25,
        "current_regime": "bull",
        "daily_pnl_brake_state": {"status": "NORMAL"},
        "intraday_pnl_baseline_krw": 1_000_000,
    }
    (data_dir / f"cycle_snapshots_{SIG}.jsonl").write_text(
        json.dumps(snapshot) + "\n"
    )

    orders = [
        {
            "timestamp": f"{date_str}T10:00:00+09:00",
            "symbol": "005930",
            "qty": 10,
            "action": "order_succeeded",
            "account_signature": SIG,
            "raw_response": {"reference_price_krw": 100.0},
        },
        {
            "timestamp": f"{date_str}T13:00:00+09:00",
            "symbol": "005930",
            "qty": 10,
            "action": "sell_order_succeeded",
            "account_signature": SIG,
            "raw_response": {"reference_price_krw": 110.0},
        },
    ]
    (data_dir / f"orders_{SIG}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in orders) + "\n"
    )
    (data_dir / f"runtime_state_{SIG}.json").write_text(json.dumps({}))

    disclosures = [
        {
            "rcept_no": "20260718000001",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "report_nm": "무상증자결정",
            "category": "CORP_ACTION",
            "rcept_dt": date_str.replace("-", ""),
            "url": "https://dart.example/1",
            "detected_at": (now - timedelta(hours=2)).isoformat(),
        }
    ]
    (data_dir / "disclosure_events.jsonl").write_text(
        "\n".join(json.dumps(record) for record in disclosures) + "\n"
    )
    return data_dir, date_str


def test_cli_composes_briefing_from_tmp_data_dir(tmp_path, monkeypatch):
    from app.tools.morning_briefing_report import run

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    data_dir, date_str = _write_data_dir(tmp_path, now=now)

    monkeypatch.delenv("DISCLOSURE_SENTINEL_STATE_DIR", raising=False)
    regime_dir = tmp_path / "regime"
    regime_dir.mkdir()
    (regime_dir / f"morning_regime_{date_str}.json").write_text(
        json.dumps(
            {
                "regime_id": 2,
                "artifact_version": "regime_2",
                "us_session_day": "2026-07-17",
                "fallback_reason": None,
            }
        )
    )
    monkeypatch.setenv("MORNING_REGIME_ARTIFACT_DIR", str(regime_dir))

    briefing = run(data_dir=str(data_dir), now=now, notify=None)

    # Accounts section with the T+2 gap.
    assert len(briefing["accounts"]) == 1
    acct = briefing["accounts"][0]
    assert acct["display"] == "5008****11"
    assert acct["t2_pending_krw"] == 200_000
    assert acct["stale"] is False

    # Yesterday attribution reused from the B-track builder.
    attribution = briefing["yesterday_attribution"]
    assert isinstance(attribution, dict)
    assert attribution["realized_by_symbol"] == {"005930": 100.0}

    # Disclosures windowed to the last 24h.
    assert len(briefing["disclosures_24h"]) == 1
    assert briefing["disclosures_24h"][0]["corp_name"] == "삼성전자"

    # Regime pick artifact resolved from MORNING_REGIME_ARTIFACT_DIR.
    assert briefing["regime_pick"]["artifact_version"] == "regime_2"


def test_morning_briefing_event_routes_to_operator_channel():
    from app.notifications.slack import (
        EVENT_CHANNEL_ENV_BY_TYPE,
        MORNING_BRIEFING_EVENT_TYPE,
        OPERATOR_CHANNEL_ENV,
        resolve_channel_env_vars,
    )

    assert MORNING_BRIEFING_EVENT_TYPE == "morning_briefing"
    assert (
        EVENT_CHANNEL_ENV_BY_TYPE[MORNING_BRIEFING_EVENT_TYPE] == OPERATOR_CHANNEL_ENV
    )
    # Resolution reads the OPTIONS map (not the raw BY_TYPE map), so an
    # operator-facing event must be registered there too (DISCLOSURE precedent).
    assert resolve_channel_env_vars(MORNING_BRIEFING_EVENT_TYPE) == (
        OPERATOR_CHANNEL_ENV,
    )


def test_cli_slack_optional_and_failure_swallowed(tmp_path, monkeypatch):
    from app.tools.morning_briefing_report import run

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    data_dir, _date_str = _write_data_dir(tmp_path, now=now)
    monkeypatch.delenv("DISCLOSURE_SENTINEL_STATE_DIR", raising=False)
    monkeypatch.delenv("MORNING_REGIME_ARTIFACT_DIR", raising=False)

    # notify=None (no --slack) → nothing sent, briefing still builds.
    silent = run(data_dir=str(data_dir), now=now, notify=None)
    assert len(silent["accounts"]) == 1

    # Raising notifier → swallowed; the briefing survives untouched.
    calls: list[tuple] = []

    def _raising_notify(event_type, message, *, symbol, details):
        calls.append((event_type, message, symbol, details))
        raise RuntimeError("slack down")

    result = run(data_dir=str(data_dir), now=now, notify=_raising_notify)

    assert len(calls) == 1
    assert calls[0][0] == "morning_briefing"
    assert "모닝 브리핑" in calls[0][1]
    assert len(result["accounts"]) == 1


def test_plist_example_exists_and_references_cli():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    plist = repo_root / "launchd" / "com.kis-trader.morning-briefing.plist.example"
    assert plist.exists(), "launchd example template must ship"

    text = plist.read_text(encoding="utf-8")
    # References the CLI module and --slack.
    assert "app.tools.morning_briefing_report" in text
    assert "--slack" in text
    # Weekday pre-open schedule at 08:35 KST.
    assert "<key>Hour</key><integer>8</integer><key>Minute</key><integer>35</integer>" in text
    # Weekdays 1..5 only (no weekend runs).
    for weekday in range(1, 6):
        assert f"<key>Weekday</key><integer>{weekday}</integer>" in text
