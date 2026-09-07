from types import SimpleNamespace
from unittest import mock

import pytest

from app.dashboard import view_models


_VIEW_MODEL_NAMES = (
    "_safe_json",
    "_safe_text",
    "_safe_count",
    "_to_datetime",
    "_age_minutes",
    "_freshness_label",
    "_event_tone",
    "_build_trace_events",
    "_build_queue_items",
    "_build_mission",
    "_focus_story",
    "_position_status",
    "_priority_sort_value",
    "_sort_positions",
)


def _candidate() -> dict:
    return {
        "symbol": "005930",
        "symbol_name": "삼성전자",
        "score": 82.5,
        "net_edge_bps": 50.0,
    }


def _dashboard_data() -> dict:
    candidate = _candidate()
    return {
        "market_status": SimpleNamespace(session="REGULAR"),
        "runtime_state": {"last_action": "buy_order_succeeded"},
        "runtime_state_summary": {},
        "orders": [
            {
                "timestamp": "2026-06-11T09:05:00+09:00",
                "action": "buy_order_succeeded",
                "result": "success",
                "symbol": "005930",
                "symbol_name": "삼성전자",
                "side": "BUY",
                "qty": 2,
                "reason": "entry ok",
            }
        ],
        "cycles": [
            {
                "timestamp": "2026-06-11T09:04:00+09:00",
                "cycle_id": "c1",
                "final_action": "BUY_ORDER_SUCCEEDED",
                "final_reason": "cycle ok",
                "selected_primary_action_name": "삼성전자",
                "selected_buy_symbol": "005930",
                "selection_details": {"selection_reason": "ranked first"},
                "scanner_candidates_top": [candidate],
            }
        ],
        "trade_journal": [
            {
                "symbol": "005930",
                "symbol_name": "삼성전자",
                "buy_ts": "2026-06-11T09:00:00+09:00",
                "sell_ts": "2026-06-11T09:06:00+09:00",
                "sell_reason": "take profit",
                "status": "closed",
            }
        ],
        "positions": [
            {
                "symbol": "005930",
                "symbol_name": "삼성전자",
                "account_weight_pct": 25.0,
                "weight_pct": 25.0,
                "evaluation_amount_krw": 250_000,
                "net_pnl_krw": 10_000,
                "net_pnl_pct": 4.0,
            },
            {
                "symbol": "000660",
                "symbol_name": "SK하이닉스",
                "account_weight_pct": 10.0,
                "weight_pct": 10.0,
                "evaluation_amount_krw": 100_000,
                "net_pnl_krw": -5_000,
                "net_pnl_pct": -3.5,
            },
        ],
        "candidate_rows": [candidate],
        "engine_state_view": {
            "market_session": "REGULAR",
            "scheduler_decision": "SELL_PRIORITY_WITH_BUY",
            "current_brake_state": "",
            "current_regime": "NORMAL",
            "sell_check_due": True,
            "buy_scan_due": True,
            "budget_status": {},
        },
        "account_view": {
            "operating_equity_krw": 1_000_000,
            "holdings_market_value_krw": 350_000,
        },
        "performance_summary": {},
        "performance_selection_meta": {},
        "research": {
            "proposals": [{"proposal_id": "p1"}],
            "snapshot_meta": {"stale_baselines": ["core"]},
            "snapshot_as_of": "2026-06-10",
        },
    }


def _summary() -> dict:
    return {
        "snapshot_health": "정상",
        "market_session": "REGULAR",
        "last_action": "buy_order_succeeded",
        "today_order_count": 1,
        "top_blocked_action": "차단 없음",
        "recent_cycle_at": None,
        "top_position": {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "account_weight_pct": 25.0,
        },
        "bottom_position": {
            "symbol": "000660",
            "symbol_name": "SK하이닉스",
            "net_pnl_pct": -3.5,
        },
    }


def test_dashboard_view_model_helpers_are_available():
    for name in _VIEW_MODEL_NAMES:
        assert hasattr(view_models, name)


def test_safe_helpers_and_freshness_labels():
    assert view_models._safe_json({"종목": "삼성전자"}) == '{\n  "종목": "삼성전자"\n}'
    assert view_models._safe_text("  value  ") == "value"
    assert view_models._safe_text("", "fallback") == "fallback"
    assert view_models._safe_count(3.8) == "3"
    assert view_models._safe_count("bad") == "—"
    assert view_models._to_datetime("2026-06-11T09:00:00+09:00").isoformat() == "2026-06-11T09:00:00+09:00"
    assert view_models._to_datetime("bad") is None
    assert view_models._age_minutes("2999-01-01T00:00:00+09:00") == 0
    assert view_models._freshness_label(None) == "시각 없음"
    with mock.patch.object(view_models, "_age_minutes", return_value=2):
        assert view_models._freshness_label("ignored") == "fresh"
    with mock.patch.object(view_models, "_age_minutes", return_value=7):
        assert view_models._freshness_label("ignored") == "7m stale"
    with mock.patch.object(view_models, "_age_minutes", return_value=11):
        assert view_models._freshness_label("ignored") == "11m+ stale"


@pytest.mark.parametrize(
    ("kind", "action", "result", "expected"),
    [
        ("order", "buy_blocked_risk_guard", "skipped", "danger"),
        ("order", "sell_order_succeeded", "success", "warning"),
        ("cycle", "HOLD", None, "neutral"),
        ("order", "buy_order_succeeded", "success", "info"),
    ],
)
def test_event_tone_representative_branches(kind, action, result, expected):
    assert view_models._event_tone(kind, action, result) == expected


def test_build_trace_events_full_list():
    assert view_models._build_trace_events(_dashboard_data(), limit=3) == [
        {
            "id": "trade::005930::2026-06-11T09:06:00+09:00",
            "kind": "trade",
            "tone": "positive",
            "title": "삼성전자 · closed",
            "body": "buy 2026-06-11 09:00 / sell 2026-06-11 09:06 / reason take profit",
            "timestamp": "2026-06-11 09:06",
            "sort_key": "2026-06-11T09:06:00+09:00",
            "raw": _dashboard_data()["trade_journal"][0],
        },
        {
            "id": "order::2026-06-11T09:05:00+09:00::buy_order_succeeded::005930",
            "kind": "order",
            "tone": "info",
            "title": "매수 주문 성공 · 삼성전자",
            "body": "BUY / 2주 / entry ok",
            "timestamp": "2026-06-11 09:05:00",
            "sort_key": "2026-06-11T09:05:00+09:00",
            "raw": _dashboard_data()["orders"][0],
        },
        {
            "id": "cycle::c1",
            "kind": "cycle",
            "tone": "neutral",
            "title": "매수 주문 성공 · 삼성전자",
            "body": "cycle ok",
            "timestamp": "2026-06-11 09:04:00",
            "sort_key": "2026-06-11T09:04:00+09:00",
            "raw": _dashboard_data()["cycles"][0],
        },
    ]


def test_build_queue_items_full_list():
    assert view_models._build_queue_items(_dashboard_data(), _summary()) == [
        {
            "tone": "danger",
            "tag": "Risk",
            "area": "Capital",
            "title": "삼성전자 비중이 큽니다",
            "body": "계좌 비중 25.00%로 단일 포지션 영향이 큽니다.",
        },
        {
            "tone": "warning",
            "tag": "Watch",
            "area": "Position",
            "title": "SK하이닉스 손실 확대 중",
            "body": "수익률 -3.50% 상태입니다.",
        },
        {
            "tone": "info",
            "tag": "Idea",
            "area": "Selection",
            "title": "삼성전자가 최근 후보의 중심입니다",
            "body": "score 82.5 / net edge 50.0bps",
        },
    ]


def test_build_mission_full_dict_for_candidate():
    assert view_models._build_mission(_summary(), _dashboard_data()) == {
        "tone": "info",
        "eyebrow": "Operator Mission",
        "title": "삼성전자가 현재 선택 surface의 중심입니다.",
        "body": "score 82.5, net edge 50.0bps, selection reason ranked first",
    }


@pytest.mark.parametrize(
    ("focus", "expected"),
    [
        (
            "Execution",
            {
                "tone": "neutral",
                "eyebrow": "Execution Surface",
                "title": "최근 결정과 실제 실행 흔적을 시간축에서 읽습니다.",
                "body": "last action 매수 주문 성공 / scheduler 매도 우선, 매수 병행 / today submitted 1",
            },
        ),
        (
            "Capital",
            {
                "tone": "warning",
                "eyebrow": "Capital Surface",
                "title": "자본 배치와 집중 위험을 먼저 봅니다.",
                "body": "총자산 1,000,000원 / 보유 평가금액 350,000원 / largest 삼성전자",
            },
        ),
        (
            "Lab",
            {
                "tone": "info",
                "eyebrow": "Lab Surface",
                "title": "운영 반영 전 아이디어와 기준선 상태를 읽습니다.",
                "body": "proposals 1 / stale baselines 1 / snapshot 2026-06-10",
            },
        ),
        (
            "Triage",
            {
                "tone": "positive",
                "eyebrow": "Triage Surface",
                "title": "지금 무엇을 봐야 하는지 한 surface로 압축합니다.",
                "body": "market 정규장 / freshness 시각 없음 / candidate 삼성전자",
            },
        ),
    ],
)
def test_focus_story_full_dict(focus, expected):
    assert view_models._focus_story(_summary(), _dashboard_data(), focus) == expected


def test_position_status_priority_and_sorting():
    positions = [
        {"symbol": "005930", "account_weight_pct": 25.0, "net_pnl_pct": 4.0, "evaluation_amount_krw": 250_000},
        {"symbol": "000660", "account_weight_pct": 10.0, "net_pnl_pct": -3.5, "evaluation_amount_krw": 100_000},
        {"symbol": "035420", "account_weight_pct": 5.0, "net_pnl_pct": 1.0, "evaluation_amount_krw": 300_000},
    ]
    highlights = {"largest": positions[0], "worst": positions[1]}

    assert view_models._position_status(positions[0], highlights) == "largest"
    assert view_models._position_status(positions[1], highlights) == "worst"
    assert view_models._position_status({"symbol": "111111", "net_pnl_pct": -0.1}, {}) == "loss"
    assert view_models._position_status({"symbol": "222222", "net_pnl_pct": 0.0}, {}) == "steady"
    assert view_models._priority_sort_value(positions[1]) == 17.0
    assert [item["symbol"] for item in view_models._sort_positions(positions, "size")] == ["005930", "000660", "035420"]
    assert [item["symbol"] for item in view_models._sort_positions(positions, "pnl")] == ["000660", "035420", "005930"]
    assert [item["symbol"] for item in view_models._sort_positions(positions, "market_value")] == ["035420", "005930", "000660"]
    assert [item["symbol"] for item in view_models._sort_positions(positions, "priority")] == ["005930", "000660", "035420"]
