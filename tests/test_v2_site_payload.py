from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.dashboard import v2_site_payload


def _dashboard_data() -> dict:
    candidate = {
        "symbol": "005930",
        "symbol_name": "삼성전자",
        "score": 82.5,
        "net_edge_bps": 50.0,
    }
    return {
        "market_status": SimpleNamespace(session="REGULAR"),
        "runtime_state": {
            "last_action": "buy_order_succeeded",
            "effective_buy_max_budget_per_trade_krw": 1_000_000,
            "effective_buy_max_account_exposure_pct": 30.0,
            "effective_rebuy_cooldown_minutes": 20,
            "buy_scan_profile": "regular",
        },
        "runtime_state_summary": {
            "buy_completed_count": 1,
            "sell_completed_count": 0,
        },
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
                "cycle_id": "c1",
                "price": 70000,
                "value": 140000,
            },
            {
                "timestamp": "2026-06-11T09:03:00+09:00",
                "action": "buy_blocked_cooldown",
                "result": "skipped",
                "symbol": "000660",
                "symbol_name": "SK하이닉스",
                "side": "BUY",
                "qty": 0,
                "reason": "cooldown",
            },
        ],
        "cycles": [
            {
                "timestamp": "2026-06-11T09:04:00+09:00",
                "cycle_id": "c1",
                "final_action": "BUY_ORDER_SUCCEEDED",
                "final_reason": "cycle ok",
                "market_session": "REGULAR",
                "selected_primary_action_name": "삼성전자",
                "selected_buy_symbol": "005930",
                "selected_buy_candidate": "삼성전자",
                "selection_details": {"selection_reason": "ranked first"},
                "scanner_candidates_top": [candidate],
                "buy_scan_requested_count": 12,
                "buy_scan_evaluated_count": 8,
                "top_candidate_count": 3,
                "cycle_elapsed_ms": 500.0,
                "api_request_count": 6,
                "buy_scan_skipped_reason": "cooldown_active",
                "raw": {
                    "buy_scan_profile": "regular",
                    "current_regime": "NORMAL",
                    "current_brake_state": "OK",
                },
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
                "holding_qty": 2,
                "average_cost_krw": 65000,
                "current_price_krw": 70000,
                "account_weight_pct": 25.0,
                "weight_pct": 25.0,
                "evaluation_amount_krw": 140000,
                "net_pnl_krw": 10000,
                "net_pnl_pct": 7.69,
            },
            {
                "symbol": "000660",
                "symbol_name": "SK하이닉스",
                "holding_qty": 1,
                "average_cost_krw": 100000,
                "current_price_krw": 95000,
                "account_weight_pct": 10.0,
                "weight_pct": 10.0,
                "evaluation_amount_krw": 95000,
                "net_pnl_krw": -5000,
                "net_pnl_pct": -5.0,
            },
        ],
        "candidate_rows": [candidate],
        "engine_state_view": {
            "market_session": "REGULAR",
            "market_reason": "정규장",
            "scheduler_decision": "SELL_PRIORITY_WITH_BUY",
            "current_brake_state": "OK",
            "current_regime": "NORMAL",
            "regime_multiplier": 1.0,
            "sell_check_due": True,
            "buy_scan_due": True,
            "buy_scan_requested_count": 12,
            "budget_status": {
                "recent_request_count": 2,
                "quotes_used_this_tick": 3,
            },
        },
        "account_view": {
            "masked_account_display": "mock_5019***23-01",
            "operating_equity_krw": 1_000_000,
            "display_equity_krw": 1_010_000,
            "raw_balance_total_evaluation_amount_krw": 1_000_000,
            "cash_total_krw": 765_000,
            "orderable_cash_krw": 765_000,
            "holdings_market_value_krw": 235_000,
            "positions_count": 2,
            "cash_weight_pct": 76.5,
            "holdings_weight_pct": 23.5,
            "total_unrealized_net_pnl_krw": 5_000,
            "realized_net_pnl_krw": 0,
            "total_return_pct": 0.5,
        },
        "performance_snapshots": [
            {"total_equity_krw": 990_000},
            {"total_equity_krw": 1_000_000},
        ],
        "performance_summary": {
            "generated_at": "2026-06-11T09:06:00+09:00",
            "drawdown": {"current_drawdown_pct": -0.5, "max_drawdown_pct": -1.0},
        },
        "performance_selection_meta": {},
        "research": {
            "proposals": [
                {
                    "proposal_id": "proposal_20260611_0001",
                    "status": "proposed",
                    "direction": "core",
                    "evaluation_verdict": "hold",
                    "delta_sharpe": 0.12,
                    "source_snapshot_date": "2026-06-10",
                    "registered_at": "2026-06-11T08:00:00+09:00",
                }
            ],
            "baselines": [
                {
                    "family": "core",
                    "sharpe": 0.7,
                    "total_return": 3.1,
                    "days_old": 2,
                    "is_stale": False,
                }
            ],
            "snapshot_meta": {"stale_baselines": ["explore"]},
            "snapshot_as_of": "2026-06-10",
            "ml_labels": [{"symbol": "005930"}],
            "native_backtest": {
                "window": {"start": "2025-06-10", "end": "2025-06-16"},
                "day_count": 5,
                "total_return_pct": 1.4172,
                "mdd_pct": -2.4926,
                "trade_count": 275,
                "win_rate": 0.534,
                "final_equity": 101_417_218,
                "artifact_version": "base@thr55",
                "partial": False,
            },
        },
    }


def _payload() -> dict:
    return v2_site_payload.build_v2_site_payload(
        _dashboard_data(),
        now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc),
    )


def test_minimal_payload_preserves_current_v2_contract_and_parity_keys() -> None:
    payload = _payload()

    assert set(v2_site_payload.V2_SITE_BASE_CONTRACT_KEYS) <= set(payload)
    assert set(v2_site_payload.V2_SITE_PARITY_CONTRACT_KEYS) <= set(payload)
    assert payload["ACCOUNT"]["total_equity_krw"] == 1_000_000
    assert payload["ENGINE"]["buy_scan_profile"] == "regular"
    assert payload["NAV_GROUPS"][-1]["items"][-1]["id"] == "lab"


def test_equity_history_preserves_real_snapshot_timestamps_without_fabrication() -> None:
    data = _dashboard_data()
    data["performance_snapshots"] = [
        {
            "timestamp": "2026-06-10T09:00:00+09:00",
            "trading_date": "2026-06-10",
            "total_equity_krw": 990_000,
        },
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "trading_date": "2026-06-11",
            "total_equity_krw": 1_000_000,
        },
    ]

    payload = v2_site_payload.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )

    assert payload["EQUITY_HISTORY"] == [
        {"ts": "2026-06-10T09:00:00+09:00", "v": 990_000},
        {"ts": "2026-06-11T09:00:00+09:00", "v": 1_000_000},
    ]

    data["performance_snapshots"] = []
    empty_payload = v2_site_payload.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )
    assert empty_payload["EQUITY_HISTORY"] == []
    assert empty_payload["INTRADAY"] == []
    assert empty_payload["PNL_HIST"] == []


def test_account_carries_data_quality_and_flags_insufficient_equity() -> None:
    # Healthy default snapshot → data is sufficient.
    healthy = _payload()
    healthy_dq = healthy["ACCOUNT"]["data_quality"]
    assert healthy_dq["is_insufficient"] is False
    assert "snapshot_health" in healthy_dq
    assert "last_sync_at" in healthy_dq
    assert healthy["ACCOUNT"]["last_sync_at"] == "2026-06-11T09:06:00+09:00"
    assert healthy_dq["last_sync_at"] == "2026-06-11T09:06:00+09:00"

    # Balance fetch failed (only a masked id survives, no equity figures) — the
    # 2026-07-06 EGW00123 incident. Equity renders 0, but the chip must mark it
    # as unavailable so 0 KRW is never mistaken for a real balance.
    data = _dashboard_data()
    data["account_view"] = {"masked_account_display": "mock_5019***23-01"}
    data["performance_snapshots"] = []
    data["performance_summary"] = {}
    payload = v2_site_payload.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )
    assert payload["ACCOUNT"]["total_equity_krw"] == 0
    assert payload["ACCOUNT"]["data_quality"]["is_insufficient"] is True
    assert payload["ACCOUNT"]["data_quality"]["reason"]


def test_account_surfaces_equity_source_and_reason_from_account_view() -> None:
    """D2: the account payload exposes the equity source + unavailable reason
    computed by _build_account_view, so a 0 KRW is explained, not silent."""
    from app.dashboard import v2_site_payload as vp

    data = _dashboard_data()
    data["account_view"] = {
        "masked_account_display": "mock_5019***23-01",
        "data_quality": {
            "is_data_insufficient": True,
            "equity_source": None,
            "equity_unavailable_reason": "no equity source — performance summary absent",
        },
    }
    data["performance_snapshots"] = []
    data["performance_summary"] = {}
    payload = vp.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )
    dq = payload["ACCOUNT"]["data_quality"]
    assert dq["equity_source"] is None
    assert "performance summary absent" in dq["equity_unavailable_reason"]
    # the precise account-view reason wins over the generic fallback text
    assert dq["reason"] == "no equity source — performance summary absent"


def test_payload_accepts_explicit_account_signature_and_options() -> None:
    payload = v2_site_payload.build_v2_site_payload(
        _dashboard_data(),
        now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc),
        account_signature="mock_acct_selected",
        account_options=[
            {
                "id": "mock_acct_selected",
                "label": "Mock selected",
                "env": "MOCK",
                "status": "active",
                "equity": 1_000_000,
            }
        ],
    )

    assert payload["ACCOUNT"]["signature"] == "mock_acct_selected"
    assert payload["ACCOUNT"]["display_name"] == "Mock selected"
    assert payload["ACCOUNT"]["env"] == "MOCK"
    assert payload["ACCOUNT"]["masked"] == "Mock selected"
    assert payload["OTHER_ACCOUNTS"] == [
        {
            "id": "mock_acct_selected",
            "label": "Mock selected",
            "env": "MOCK",
            "status": "active",
            "equity": 1_000_000,
        }
    ]


def test_payload_uses_active_option_label_for_current_account_display() -> None:
    payload = v2_site_payload.build_v2_site_payload(
        _dashboard_data(),
        now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc),
        account_signature="mock_acct_current",
        account_options=[
            {
                "id": "mock_acct_current",
                "label": "5019***23-01",
                "env": "MOCK",
                "status": "active",
                "equity": 1_000_000,
            }
        ],
    )

    assert payload["ACCOUNT"]["signature"] == "mock_acct_current"
    assert payload["ACCOUNT"]["display_name"] == "5019***23-01"
    assert payload["ACCOUNT"]["masked"] == "5019***23-01"


def test_trace_events_include_order_cycle_and_trade_kinds() -> None:
    events = _payload()["TRACE_EVENTS"]

    assert [event["kind"] for event in events[:3]] == ["trade", "order", "cycle"]
    assert events[0]["title"] == "삼성전자 · closed"


def test_lab_contract_summarizes_research_state() -> None:
    lab = _payload()["LAB"]

    assert lab["proposal_count"] == 1
    assert lab["baseline_count"] == 1
    assert lab["stale_baseline_count"] == 1
    assert lab["snapshot_as_of"] == "2026-06-10"
    assert lab["ml_label_count"] == 1
    assert lab["proposals"][0]["proposal_id"] == "proposal_20260611_0001"
    assert lab["native_backtest"]["total_return_pct"] == 1.4172
    assert lab["native_backtest"]["trade_count"] == 275


def test_book_contract_provides_streamlit_sort_variants() -> None:
    book = _payload()["BOOK"]

    assert [row["symbol"] for row in book["sorts"]["size"]] == ["005930", "000660"]
    assert [row["symbol"] for row in book["sorts"]["pnl"]] == ["000660", "005930"]
    assert book["selected_context"]["symbol"] == "005930"
    assert book["account_context"]["orderable_cash_krw"] == 765_000


def test_queue_mission_and_triage_reuse_dashboard_semantics() -> None:
    payload = _payload()

    assert payload["MISSION"]["eyebrow"] == "Operator Mission"
    assert payload["QUEUE"]
    assert payload["TRIAGE"]["anomaly"]["recent_guard_blocks"] == 1
    assert payload["TRIAGE"]["blocked_reasons"][0]["label"] == "buy_blocked_cooldown"


def test_payload_module_does_not_import_broker_or_streamlit_surfaces() -> None:
    source = inspect.getsource(v2_site_payload)

    assert "streamlit" not in source
    assert "inquire_balance" not in source
    assert "app.domestic_stock" not in source
    assert "app.main" not in source


def test_today_return_pct_is_prior_day_based_and_signed() -> None:
    # today P&L of −30,000 KRW must read as a negative % of the prior-day base,
    # not the cumulative +0.5% total_return_pct the payload used to reuse (RC12).
    data = _dashboard_data()
    data["account_view"]["realized_net_pnl_krw"] = 0
    data["account_view"]["total_unrealized_net_pnl_krw"] = -30_000
    payload = v2_site_payload.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )
    today_pct = payload["ACCOUNT"]["total_return_today_pct"]
    daily_pct = payload["ENGINE"]["daily_pnl_pct"]
    assert today_pct < 0
    assert daily_pct < 0
    assert today_pct == pytest.approx(daily_pct)


def test_unrealized_pct_uses_cost_base_not_cumulative_return() -> None:
    # 5,000 unrealized over a 230,000 cost base (235,000 mkt value − 5,000)
    # is ~2.17%, distinct from the cumulative +0.5% previously reused (RC12).
    payload = _payload()
    unrealized_pct = payload["ACCOUNT"]["total_unrealized_pnl_pct"]
    today_pct = payload["ACCOUNT"]["total_return_today_pct"]
    assert unrealized_pct == pytest.approx(5_000 / 230_000 * 100)
    assert unrealized_pct != today_pct


def test_return_pcts_clamp_to_zero_when_base_non_positive() -> None:
    # today ≥ equity ⇒ no positive prior-day base ⇒ 0.0, never a huge/negative %.
    for realized in (1_000_000, 2_000_000):  # equal to, then greater than equity
        data = _dashboard_data()
        data["account_view"]["realized_net_pnl_krw"] = realized
        data["account_view"]["total_unrealized_net_pnl_krw"] = 0
        payload = v2_site_payload.build_v2_site_payload(
            data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
        )
        assert payload["ACCOUNT"]["total_return_today_pct"] == 0.0
        assert payload["ENGINE"]["daily_pnl_pct"] == 0.0
    # unrealized exceeding market value ⇒ non-positive cost base ⇒ 0.0.
    data = _dashboard_data()
    data["account_view"]["holdings_market_value_krw"] = 100_000
    data["account_view"]["total_unrealized_net_pnl_krw"] = 150_000
    payload = v2_site_payload.build_v2_site_payload(
        data, now=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    )
    assert payload["ACCOUNT"]["total_unrealized_pnl_pct"] == 0.0
