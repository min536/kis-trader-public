"""S2 — run_disclosure_sentinel_tick adapter (the I/O boundary).

Wires the pure DisclosureSentinel to holdings (file-side), the DART transport,
persisted state and the env-scoped Slack notifier. Time/transport/holdings/
state are injected for determinism; the real path resolves KST now + urllib +
the runtime_state glob. Sends must be fail-safe: a broken path can never kill
the bot's health loop, and with DART_API_KEY unset the tick is fully inert.
No network and no real data/ writes — tmp_path only.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.notifications.disclosure_sentinel import (
    DisclosureSentinel,
    run_disclosure_sentinel_tick,
)

KST = ZoneInfo("Asia/Seoul")


class _FakeNotifier:
    def __init__(self) -> None:
        self.sends: list[tuple[str, str]] = []

    def send(self, event_type: str, message: str, **_kwargs: object) -> object:
        self.sends.append((event_type, message))
        return {"ok": True}


class _MemStateStore:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {}
        self.saved = False

    def load(self) -> dict:
        return self.state

    def save(self, state: dict) -> None:
        self.state = state
        self.saved = True


def _now() -> datetime:
    return datetime(2026, 7, 17, 10, 0, tzinfo=KST)  # Friday, in window


def test_tick_noop_without_api_key() -> None:
    calls = {"transport": 0}

    def transport(params: dict) -> dict:
        calls["transport"] += 1
        return {"status": "013"}

    notifier = _FakeNotifier()
    run_disclosure_sentinel_tick(
        env={},  # no DART_API_KEY -> fully inert
        sentinel=DisclosureSentinel(),
        notifier=notifier,
        now=_now(),
        transport=transport,
        holdings_reader=lambda: {"005930"},
        state_store=_MemStateStore(),
    )

    assert calls["transport"] == 0
    assert notifier.sends == []


def test_tick_disabled_by_kill_switch() -> None:
    calls = {"transport": 0}

    def transport(params: dict) -> dict:
        calls["transport"] += 1
        return {"status": "013"}

    notifier = _FakeNotifier()
    run_disclosure_sentinel_tick(
        env={"DART_API_KEY": "key-abc", "DISCLOSURE_SENTINEL_ENABLED": "0"},
        sentinel=DisclosureSentinel(),
        notifier=notifier,
        now=_now(),
        transport=transport,
        holdings_reader=lambda: {"005930"},
        state_store=_MemStateStore(),
    )

    assert calls["transport"] == 0
    assert notifier.sends == []


def test_tick_appends_events_and_notifies_risk_only(tmp_path) -> None:
    def transport(params: dict) -> dict:
        if params["page_no"] == 1:
            return {
                "status": "000",
                "page_no": 1,
                "total_page": 1,
                "list": [
                    {
                        "rcept_no": "R1",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "report_nm": "유상증자결정",
                        "rcept_dt": "20260717",
                    },
                    {
                        "rcept_no": "R2",
                        "corp_name": "에스케이하이닉스",
                        "stock_code": "000660",
                        "report_nm": "현금ㆍ현물배당결정",
                        "rcept_dt": "20260717",
                    },
                ],
            }
        return {"status": "013"}

    notifier = _FakeNotifier()
    run_disclosure_sentinel_tick(
        env={
            "DART_API_KEY": "key-abc",
            "DISCLOSURE_SENTINEL_STATE_DIR": str(tmp_path),
        },
        sentinel=DisclosureSentinel(),
        notifier=notifier,
        now=_now(),
        transport=transport,
        holdings_reader=lambda: {"005930", "000660"},
    )

    # Every match (risk or not) is logged to JSONL.
    lines = (tmp_path / "disclosure_events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    records = [json.loads(line) for line in lines]
    assert {r["category"] for r in records} == {"CORP_ACTION", "DIVIDEND"}
    assert all("detected_at" in r for r in records)

    # Only the risk category (CORP_ACTION) is Slack-alerted; DIVIDEND is log-only.
    assert len(notifier.sends) == 1
    event_type, message = notifier.sends[0]
    assert event_type == "disclosure_alert"
    assert message == (
        "[공시] 삼성전자(005930) CORP_ACTION — 유상증자결정"
        " | https://dart.fss.or.kr/dsaf001/main.do?rcptNo=R1"
    )


def test_tick_survives_transport_exception(tmp_path, capsys) -> None:
    def transport(params: dict) -> dict:
        raise RuntimeError("dart unreachable")

    notifier = _FakeNotifier()
    run_disclosure_sentinel_tick(
        env={
            "DART_API_KEY": "key-abc",
            "DISCLOSURE_SENTINEL_STATE_DIR": str(tmp_path),
        },
        sentinel=DisclosureSentinel(),
        notifier=notifier,
        now=_now(),
        transport=transport,
        holdings_reader=lambda: {"005930"},
    )  # must not raise

    out = capsys.readouterr().out
    assert "disclosure_sentinel_tick_failed" in out
    assert notifier.sends == []


def test_default_holdings_reader_unions_accounts(tmp_path) -> None:
    from app.notifications.disclosure_sentinel import _default_holdings_reader

    (tmp_path / "runtime_state_acct1.json").write_text(
        json.dumps(
            {
                "broker_last_synced_positions_by_symbol": {
                    "005930": {"qty": 10},
                    "000660": {"qty": 5},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime_state_acct2.json").write_text(
        json.dumps(
            {"broker_last_synced_positions_by_symbol": {"035720": {"qty": 3}}}
        ),
        encoding="utf-8",
    )
    # A malformed file is skipped, not fatal.
    (tmp_path / "runtime_state_broken.json").write_text("{not json", encoding="utf-8")

    holdings = _default_holdings_reader({"KIS_RUNTIME_STATE_DIR": str(tmp_path)})

    assert holdings == {"005930", "000660", "035720"}
