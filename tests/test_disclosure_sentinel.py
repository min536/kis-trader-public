"""S1 — DisclosureSentinel pure core (time/transport injected, no I/O).

Polls DART's list.json for holdings' disclosures. Mirrors the engine_sentinel
pattern: deterministic evaluator with injected now/transport/state, window +
interval self-gating, dedupe by rcept_no with a bounded seen-list. No network
and no real data/ access — every test injects a fake transport and tmp state.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.notifications.disclosure_sentinel import (
    DisclosureEvent,
    DisclosureSentinel,
    DisclosureSentinelConfig,
    classify_report,
    load_disclosure_sentinel_config,
    should_notify,
)

KST = ZoneInfo("Asia/Seoul")


def test_classify_report_categories() -> None:
    # One representative report_nm per branch, in the module's priority order.
    assert classify_report("거래정지") == "TRADING_HALT"
    assert classify_report("매매거래 정지") == "TRADING_HALT"
    assert classify_report("유상증자결정") == "CORP_ACTION"
    assert classify_report("감자결정") == "CORP_ACTION"
    assert classify_report("주식액면분할결정") == "CORP_ACTION"
    assert classify_report("조회공시요구(풍문또는보도)") == "WATCH"
    assert classify_report("현금ㆍ현물배당결정") == "DIVIDEND"
    assert classify_report("정기주주총회소집결의") == "OTHER"


def _in_window_now() -> datetime:
    # Friday 10:00 KST — inside the default 07:00-18:00 window.
    return datetime(2026, 7, 17, 10, 0, tzinfo=KST)


def test_poll_filters_to_holdings_only() -> None:
    # Two pages of filings; only rows whose stock_code is currently held become
    # events. Non-held rows (000660) are walked past silently.
    def transport(params: dict) -> dict:
        page = params["page_no"]
        if page == 1:
            return {
                "status": "000",
                "page_no": 1,
                "total_page": 2,
                "list": [
                    {
                        "rcept_no": "20260717000001",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "report_nm": "거래정지",
                        "rcept_dt": "20260717",
                        "flr_nm": "삼성전자",
                    },
                    {
                        "rcept_no": "20260717000002",
                        "corp_name": "에스케이하이닉스",
                        "stock_code": "000660",
                        "report_nm": "유상증자결정",
                        "rcept_dt": "20260717",
                        "flr_nm": "에스케이하이닉스",
                    },
                ],
            }
        if page == 2:
            return {
                "status": "000",
                "page_no": 2,
                "total_page": 2,
                "list": [
                    {
                        "rcept_no": "20260717000003",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "report_nm": "현금ㆍ현물배당결정",
                        "rcept_dt": "20260717",
                        "flr_nm": "삼성전자",
                    },
                ],
            }
        return {"status": "013", "message": "no data"}

    sentinel = DisclosureSentinel(config=DisclosureSentinelConfig())
    state: dict = {}
    events = sentinel.poll(
        now=_in_window_now(),
        holdings={"005930"},
        transport=transport,
        state=state,
    )

    assert [e.rcept_no for e in events] == ["20260717000001", "20260717000003"]
    assert {e.stock_code for e in events} == {"005930"}
    halt = events[0]
    assert halt.corp_name == "삼성전자"
    assert halt.category == "TRADING_HALT"
    assert halt.report_nm == "거래정지"
    assert halt.rcept_dt == "20260717"
    assert halt.url == "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20260717000001"


def test_poll_dedupes_by_rcept_no_across_calls() -> None:
    # The same filing seen on a later poll (state carries seen_rcept_nos) must
    # not be re-emitted.
    def transport(params: dict) -> dict:
        if params["page_no"] == 1:
            return {
                "status": "000",
                "page_no": 1,
                "total_page": 1,
                "list": [
                    {
                        "rcept_no": "20260717000001",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "report_nm": "거래정지",
                        "rcept_dt": "20260717",
                    },
                ],
            }
        return {"status": "013"}

    sentinel = DisclosureSentinel(config=DisclosureSentinelConfig())
    now1 = _in_window_now()
    state: dict = {}

    first = sentinel.poll(
        now=now1, holdings={"005930"}, transport=transport, state=state
    )
    assert [e.rcept_no for e in first] == ["20260717000001"]
    assert "20260717000001" in state["seen_rcept_nos"]

    # Interval elapsed -> polls again, but the filing is already seen.
    now2 = now1 + timedelta(seconds=601)
    second = sentinel.poll(
        now=now2, holdings={"005930"}, transport=transport, state=state
    )
    assert second == []


def test_poll_respects_window_and_interval_gate() -> None:
    calls: list[dict] = []

    def transport(params: dict) -> dict:
        calls.append(params)
        return {"status": "013"}

    sentinel = DisclosureSentinel(config=DisclosureSentinelConfig())

    # Outside the 07:00-18:00 window -> no poll, transport untouched.
    before_open = datetime(2026, 7, 17, 6, 30, tzinfo=KST)
    assert (
        sentinel.poll(
            now=before_open, holdings={"005930"}, transport=transport, state={}
        )
        == []
    )
    assert calls == []

    # In window but the interval has not elapsed (next_poll_epoch in the future).
    now = _in_window_now()
    state = {"next_poll_epoch": now.timestamp() + 300}
    assert (
        sentinel.poll(now=now, holdings={"005930"}, transport=transport, state=state)
        == []
    )
    assert calls == []


def test_poll_early_stops_when_page_all_seen() -> None:
    # Page 2 is entirely already-seen filings -> the walk stops without ever
    # requesting page 3, even though total_page advertises more.
    requested_pages: list[int] = []

    def transport(params: dict) -> dict:
        page = params["page_no"]
        requested_pages.append(page)
        if page == 1:
            return {
                "status": "000",
                "page_no": 1,
                "total_page": 3,
                "list": [
                    {
                        "rcept_no": "P1A",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "report_nm": "거래정지",
                        "rcept_dt": "20260717",
                    },
                ],
            }
        if page == 2:
            return {
                "status": "000",
                "page_no": 2,
                "total_page": 3,
                "list": [
                    {
                        "rcept_no": "OLD1",
                        "corp_name": "가",
                        "stock_code": "005930",
                        "report_nm": "유상증자결정",
                        "rcept_dt": "20260717",
                    },
                    {
                        "rcept_no": "OLD2",
                        "corp_name": "나",
                        "stock_code": "000660",
                        "report_nm": "감자결정",
                        "rcept_dt": "20260717",
                    },
                ],
            }
        return {
            "status": "000",
            "page_no": 3,
            "total_page": 3,
            "list": [
                {
                    "rcept_no": "P3A",
                    "corp_name": "다",
                    "stock_code": "005930",
                    "report_nm": "거래정지",
                    "rcept_dt": "20260717",
                },
            ],
        }

    sentinel = DisclosureSentinel(config=DisclosureSentinelConfig())
    state = {"seen_rcept_nos": ["OLD1", "OLD2"]}
    events = sentinel.poll(
        now=_in_window_now(), holdings={"005930"}, transport=transport, state=state
    )

    assert requested_pages == [1, 2]
    assert [e.rcept_no for e in events] == ["P1A"]


def test_poll_handles_status_013_as_empty() -> None:
    # DART status 013 = "no matching data": a normal empty result, not an error.
    def transport(params: dict) -> dict:
        return {"status": "013", "message": "조회된 데이타가 없습니다."}

    sentinel = DisclosureSentinel()
    state: dict = {}
    events = sentinel.poll(
        now=_in_window_now(), holdings={"005930"}, transport=transport, state=state
    )

    assert events == []
    assert "next_poll_epoch" in state


def _event(category: str) -> DisclosureEvent:
    return DisclosureEvent(
        rcept_no="20260717000001",
        corp_name="삼성전자",
        stock_code="005930",
        report_nm="report",
        category=category,
        rcept_dt="20260717",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20260717000001",
    )


def test_should_notify_only_risk_categories() -> None:
    assert should_notify(_event("TRADING_HALT")) is True
    assert should_notify(_event("CORP_ACTION")) is True
    assert should_notify(_event("WATCH")) is True
    assert should_notify(_event("DIVIDEND")) is False
    assert should_notify(_event("OTHER")) is False


def test_load_config_parses_env_and_defaults() -> None:
    # Missing keys -> defaults.
    default = load_disclosure_sentinel_config({})
    assert default.poll_interval_seconds == 600
    assert default.window == "07:00-18:00"
    assert default.page_count == 100
    assert default.max_pages == 30

    # Valid overrides parsed through (page_count stays fixed at 100).
    ok = load_disclosure_sentinel_config(
        {
            "DISCLOSURE_SENTINEL_POLL_INTERVAL_SECONDS": "300",
            "DISCLOSURE_SENTINEL_WINDOW": "08:00-16:00",
            "DISCLOSURE_SENTINEL_MAX_PAGES": "10",
        }
    )
    assert ok.poll_interval_seconds == 300
    assert ok.window == "08:00-16:00"
    assert ok.max_pages == 10
    assert ok.page_count == 100

    # Garbage number -> default; below-minimum -> default (guard, not clamp);
    # malformed window -> default window.
    guarded = load_disclosure_sentinel_config(
        {
            "DISCLOSURE_SENTINEL_POLL_INTERVAL_SECONDS": "not-a-number",
            "DISCLOSURE_SENTINEL_MAX_PAGES": "0",
            "DISCLOSURE_SENTINEL_WINDOW": "not-a-window",
        }
    )
    assert guarded.poll_interval_seconds == 600
    assert guarded.max_pages == 30
    assert guarded.window == "07:00-18:00"
