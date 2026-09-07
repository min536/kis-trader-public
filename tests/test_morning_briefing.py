"""Behavior tests for the morning-briefing builder + renderer (plan §3 S1).

Pure + offline: ``build_morning_briefing`` is a value-in/value-out function
(already-parsed cycle snapshots, attribution dict, disclosure list, regime pick)
and performs no I/O and no broker/network calls. The renderer emits Korean
console/Slack lines. Both contract to *never raise* — missing or hostile input
degrades the affected section to ``None``/empty.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def _snapshot(**overrides) -> dict:
    base = {
        "masked_account_display": "5008****11",
        "environment": "mock",
        "equity_krw": 1_050_000,
        "cash_krw": 500_000,
        "orderable_cash_krw": 300_000,
        "intraday_pnl_pct": 1.25,
        "current_regime": "bull",
        "daily_pnl_brake_state": {"status": "NORMAL"},
        "timestamp": "2026-07-18T08:30:00+09:00",
    }
    base.update(overrides)
    return base


def test_briefing_accounts_section_with_t2_gap():
    from app.notifications.morning_briefing import build_morning_briefing

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    briefing = build_morning_briefing(
        accounts=[_snapshot()],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )

    assert briefing["generated_at"] == now.isoformat()
    assert len(briefing["accounts"]) == 1
    acct = briefing["accounts"][0]
    assert acct["display"] == "5008****11"
    assert acct["environment"] == "mock"
    assert acct["equity_krw"] == 1_050_000
    assert acct["cash_krw"] == 500_000
    assert acct["orderable_cash_krw"] == 300_000
    # T+2 pending = cash - orderable_cash (settlement still locked).
    assert acct["t2_pending_krw"] == 200_000
    assert acct["t2_pending_pct_of_cash"] == 40.0
    assert acct["intraday_pnl_pct"] == 1.25
    assert acct["regime"] == "bull"
    assert acct["stale"] is False


def test_briefing_t2_gap_never_negative():
    from app.notifications.morning_briefing import build_morning_briefing

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    # orderable_cash > cash can happen right after a sell inflates orderable while
    # dnca_tot_amt lags — the T+2 gap must floor at 0, never go negative.
    briefing = build_morning_briefing(
        accounts=[_snapshot(cash_krw=300_000, orderable_cash_krw=500_000)],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )

    acct = briefing["accounts"][0]
    assert acct["t2_pending_krw"] == 0
    assert acct["t2_pending_pct_of_cash"] == 0.0


def test_briefing_marks_stale_snapshot():
    from app.notifications.morning_briefing import build_morning_briefing

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    stale_snap = _snapshot(
        masked_account_display="STALE",
        timestamp=(now - timedelta(hours=13)).isoformat(),
    )
    fresh_snap = _snapshot(
        masked_account_display="FRESH",
        timestamp=(now - timedelta(hours=1)).isoformat(),
    )
    briefing = build_morning_briefing(
        accounts=[stale_snap, fresh_snap],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )

    stale_row, fresh_row = briefing["accounts"]
    assert stale_row["display"] == "STALE"
    assert stale_row["stale"] is True
    assert stale_row["snapshot_age_minutes"] >= 12 * 60
    assert fresh_row["display"] == "FRESH"
    assert fresh_row["stale"] is False
    assert fresh_row["snapshot_age_minutes"] == 60


def test_briefing_brake_display_uses_risk_helper():
    from app.notifications.morning_briefing import build_morning_briefing

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    # HARD_STOP -> "HARD_STOP_READY" is a mapping only the risk helper performs;
    # asserting it proves daily_pnl_brake_display_status is actually called
    # (not a passthrough of the raw status string).
    briefing = build_morning_briefing(
        accounts=[_snapshot(daily_pnl_brake_state={"status": "HARD_STOP"})],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )
    assert briefing["accounts"][0]["brake_display"] == "HARD_STOP_READY"

    # NORMAL -> "OK" (another helper-specific mapping).
    normal = build_morning_briefing(
        accounts=[_snapshot(daily_pnl_brake_state={"status": "NORMAL"})],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )
    assert normal["accounts"][0]["brake_display"] == "OK"


def test_briefing_sections_degrade_to_none_on_missing_inputs():
    from app.notifications.morning_briefing import build_morning_briefing

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)

    # Everything absent -> empty/None sections, no raise.
    empty = build_morning_briefing(
        accounts=[],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )
    assert empty["accounts"] == []
    assert empty["yesterday_attribution"] is None
    assert empty["disclosures_24h"] == []
    assert empty["regime_pick"] is None

    # A hostile / empty account dict degrades field-by-field without raising.
    degraded = build_morning_briefing(
        accounts=[{}],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )
    acct = degraded["accounts"][0]
    assert acct["display"] is None
    assert acct["t2_pending_krw"] == 0
    assert acct["t2_pending_pct_of_cash"] is None
    assert acct["snapshot_age_minutes"] is None
    assert acct["stale"] is True
    # Empty brake dict -> helper's DATA_INSUFFICIENT default.
    assert acct["brake_display"] == "DATA_INSUFFICIENT"


def test_render_briefing_lines_korean_sections_and_placeholders():
    from app.notifications.morning_briefing import (
        build_morning_briefing,
        render_briefing_lines,
    )

    now = datetime(2026, 7, 18, 8, 35, tzinfo=KST)
    full = build_morning_briefing(
        accounts=[_snapshot()],
        attribution={
            "date": "2026-07-17",
            "account_signature": "sig",
            "realized_by_symbol": {"005930": 100.0},
            "totals": {"realized_krw": 100.0, "trades_closed": 1, "trades_opened": 1},
        },
        disclosures=[
            {
                "corp_name": "삼성전자",
                "stock_code": "005930",
                "category": "CORP_ACTION",
                "report_nm": "무상증자결정",
            }
        ],
        regime_pick={
            "regime_id": 2,
            "artifact_version": "regime_2",
            "us_session_day": "2026-07-17",
            "fallback_reason": None,
        },
        now=now,
    )
    lines = render_briefing_lines(full)
    text = "\n".join(lines)
    for header in ("[계좌]", "[브레이크]", "[전일 귀속]", "[공시 24h]", "[레짐 픽]"):
        assert header in text
    # T+2 lock notation on the account line.
    assert "T+2 미결제" in text
    assert "현금의" in text
    # Embedded sections surface their content.
    assert "삼성전자" in text
    assert "regime 2" in text

    # Every section degrades to a "— 없음" placeholder when empty.
    empty = build_morning_briefing(
        accounts=[],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=now,
    )
    empty_lines = render_briefing_lines(empty)
    empty_text = "\n".join(empty_lines)
    for header in ("[계좌]", "[브레이크]", "[전일 귀속]", "[공시 24h]", "[레짐 픽]"):
        assert header in empty_text
    assert empty_text.count("— 없음") == 5


def test_render_briefing_equity_none_shows_na_not_zero() -> None:
    """A snapshot with equity_krw None (known upstream gap: the engine's
    after-close cycles persist snapshot fields as None) must render as
    ``평가 n/a`` — printing ``0원`` would misstate the account as empty."""
    from datetime import datetime, timedelta, timezone

    from app.notifications.morning_briefing import (
        build_morning_briefing,
        render_briefing_lines,
    )

    kst = timezone(timedelta(hours=9))
    briefing = build_morning_briefing(
        accounts=[
            {
                "masked_account_display": "5019***23-01",
                "environment": "mock",
                "equity_krw": None,
                "cash_krw": None,
                "orderable_cash_krw": None,
                "timestamp": "2026-07-17T15:25:43+09:00",
            }
        ],
        attribution=None,
        disclosures=[],
        regime_pick=None,
        now=datetime(2026, 7, 19, 8, 35, tzinfo=kst),
    )
    account_line = next(
        line for line in render_briefing_lines(briefing) if "5019***23-01" in line
    )
    assert "평가 n/a" in account_line
    assert "0원" not in account_line
