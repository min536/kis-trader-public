from types import SimpleNamespace
from unittest import mock

import pytest

from app.dashboard import data_loader
from app.dashboard import normalizers


_NORMALIZER_NAMES = (
    "_parse_iso_datetime",
    "_successful_sell_orders_since",
    "_summary_has_cycle_support",
    "_is_closed_session_summary_without_support",
    "_summary_equity",
    "_select_trusted_performance_summary",
    "_resolve_symbol_name",
    "_normalize_positions",
    "_normalize_positions_from_cycle",
    "_build_account_view",
    "_apply_account_weights",
    "_infer_order_side",
    "_normalize_orders",
    "_normalize_cycles",
    "_normalize_candidates",
    "_latest_action_record",
    "_build_engine_state_view",
    "_build_trade_journal",
)


def test_dashboard_normalizer_facade_bindings_are_preserved():
    for name in _NORMALIZER_NAMES:
        assert getattr(data_loader, name) is getattr(normalizers, name)


def test_parse_iso_datetime_and_sell_order_filtering():
    parsed = normalizers._parse_iso_datetime("2026-06-11T09:00:00+09:00")

    assert parsed is not None
    assert parsed.isoformat() == "2026-06-11T09:00:00+09:00"
    assert normalizers._parse_iso_datetime("not-a-date") is None
    assert normalizers._successful_sell_orders_since(
        [
            {"timestamp": "2026-06-11T09:00:00+09:00", "action": "order_succeeded"},
            {"timestamp": "2026-06-11T09:10:00+09:00", "action": "sell_order_succeeded"},
            {"timestamp": "2026-06-11T09:20:00+09:00", "action": "sell_order_succeeded"},
        ],
        since_timestamp="2026-06-11T09:15:00+09:00",
    ) == [
        {"timestamp": "2026-06-11T09:20:00+09:00", "action": "sell_order_succeeded"}
    ]


def test_summary_support_and_closed_session_filtering():
    summary = {
        "generated_at": "2026-06-11T09:02:00+09:00",
        "account_summary": {"positions_count": 1, "holdings_market_value_krw": 1000},
    }
    cycle_records = [
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "holdings_summary": {"position_count": 2},
        }
    ]

    assert normalizers._summary_has_cycle_support(
        summary,
        cycle_records=cycle_records,
    )
    with mock.patch.object(
        normalizers,
        "get_korean_market_session",
        return_value=SimpleNamespace(order_allowed=False),
    ):
        assert not normalizers._is_closed_session_summary_without_support(
            summary,
            cycle_records=cycle_records,
        )
        assert normalizers._is_closed_session_summary_without_support(
            {**summary, "generated_at": "2026-06-11T10:00:00+09:00"},
            cycle_records=cycle_records,
        )


def test_old_filtered_summary_does_not_mark_current_trusted_summary_as_fallback():
    old_unsupported = {
        "generated_at": "2026-06-14T10:00:00+09:00",
        "account_summary": {"positions_count": 10, "holdings_market_value_krw": 1_000_000},
    }
    current_supported = {
        "generated_at": "2026-06-15T09:02:00+09:00",
        "account_summary": {"positions_count": 11, "holdings_market_value_krw": 1_100_000},
    }
    cycle_records = [
        {
            "timestamp": "2026-06-15T09:02:30+09:00",
            "holdings_summary": {"position_count": 11},
        }
    ]

    with mock.patch.object(
        normalizers,
        "get_korean_market_session",
        side_effect=lambda *, now: SimpleNamespace(order_allowed=now.day == 15),
    ):
        selected, metadata = normalizers._select_trusted_performance_summary(
            [old_unsupported, current_supported],
            order_records=[],
            cycle_records=cycle_records,
        )

    assert selected is current_supported
    assert metadata == {
        "fallback_applied": False,
        "fallback_reason": None,
        "selected_generated_at": "2026-06-15T09:02:00+09:00",
        "latest_generated_at": "2026-06-15T09:02:00+09:00",
    }


def test_summary_equity_and_trusted_performance_summary_fallback():
    summaries = [
        {
            "generated_at": "2026-06-11T09:00:00+09:00",
            "account_summary": {
                "positions_count": 10,
                "holdings_market_value_krw": 1_000_000,
                "realized_net_pnl_krw": 0,
            },
        },
        {
            "generated_at": "2026-06-11T09:10:00+09:00",
            "account_summary": {
                "positions_count": 2,
                "holdings_market_value_krw": 300_000,
                "realized_net_pnl_krw": 0,
            },
        },
    ]

    with mock.patch.object(
        normalizers,
        "get_korean_market_session",
        return_value=SimpleNamespace(order_allowed=True),
    ):
        selected, metadata = normalizers._select_trusted_performance_summary(
            summaries,
            order_records=[],
            cycle_records=[],
        )

    assert normalizers._summary_equity(
        {"equity": {"total_equity_krw": 123_000}}
    ) == 123_000
    assert selected is summaries[0]
    assert metadata == {
        "fallback_applied": True,
        "fallback_reason": "최근 성과 요약의 포지션/보유금액이 급감했지만 대응되는 매도 체결 로그가 없어 마지막 정상 스냅샷으로 대체했습니다.",
        "selected_generated_at": "2026-06-11T09:00:00+09:00",
        "latest_generated_at": "2026-06-11T09:10:00+09:00",
    }
    assert normalizers._select_trusted_performance_summary(
        [],
        order_records=[],
        cycle_records=[],
    ) == (None, {"fallback_applied": False})


def test_resolve_symbol_name_uses_candidates_before_lookup():
    with mock.patch.object(normalizers, "get_symbol_name", return_value="lookup-name") as lookup:
        assert normalizers._resolve_symbol_name("005930", " null ", " 삼성전자 ") == "삼성전자"
        assert normalizers._resolve_symbol_name("000660", None) == "lookup-name"

    lookup.assert_called_once_with("000660")


def test_normalize_positions_full_list():
    performance_summary = {
        "positions": [
            {
                "symbol": "005930",
                "symbol_name": "삼성전자",
                "holding_qty": 2,
                "quantity": 3,
                "average_cost_krw": 900,
                "average_price": 910,
                "current_price_krw": 1000,
                "current_price": 1010,
                "evaluation_amount_krw": 2000,
                "market_value": 2020,
                "gross_pnl_krw": 180,
                "gross_pnl": 190,
                "gross_pnl_pct": 9.5,
                "net_pnl_krw": 160,
                "net_pnl": 170,
                "net_pnl_pct": 8.4,
                "weight_pct": 12.5,
            }
        ]
    }

    assert normalizers._normalize_positions(performance_summary) == [
        {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "holding_qty": 2,
            "quantity": 3,
            "average_cost_krw": 900,
            "average_price": 910,
            "current_price_krw": 1000,
            "current_price": 1010,
            "evaluation_amount_krw": 2000,
            "market_value": 2020,
            "gross_pnl_krw": 180,
            "gross_pnl": 190,
            "gross_pnl_pct": 9.5,
            "net_pnl_krw": 160,
            "net_pnl": 170,
            "net_pnl_pct": 8.4,
            "weight_pct": 12.5,
        }
    ]


def test_normalize_positions_from_cycle_full_list():
    latest_cycle = {
        "holdings_summary": {
            "positions": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "holding_qty": 2,
                    "quantity": 3,
                    "average_cost": 900,
                    "average_price": 910,
                    "current_price": 1000,
                    "market_value": 2000,
                    "gross_pnl_krw": 180,
                    "gross_pnl": 190,
                    "gross_pnl_pct": 9.5,
                    "net_pnl_krw": None,
                    "net_pnl": None,
                    "net_pnl_pct": None,
                    "weight_pct": 12.5,
                }
            ]
        }
    }

    assert normalizers._normalize_positions_from_cycle(latest_cycle) == [
        {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "holding_qty": 2,
            "quantity": 3,
            "average_cost_krw": 900,
            "average_price": 910,
            "current_price_krw": 1000,
            "current_price": 1000,
            "evaluation_amount_krw": 2000,
            "market_value": 2000,
            "gross_pnl_krw": 180,
            "gross_pnl": 190,
            "gross_pnl_pct": 9.5,
            "net_pnl_krw": None,
            "net_pnl": None,
            "net_pnl_pct": None,
            "weight_pct": 12.5,
        }
    ]


def test_build_account_view_and_apply_account_weights_full_dict():
    positions = [
        {
            "evaluation_amount_krw": 2000,
            "average_cost_krw": 900,
            "holding_qty": 2,
            "net_pnl_krw": 200,
        }
    ]
    account_view = normalizers._build_account_view(
        {},
        positions=positions,
        latest_cycle={
            "raw": {
                "holdings_summary": {
                    "position_count": 1,
                    "holdings_market_value_krw": 2100,
                    "cash_orderable_krw": 3000,
                    "cash_total_krw": 3500,
                }
            }
        },
    )

    assert account_view == {
        "positions_count": 1,
        "holdings_market_value_krw": 2000,
        "total_cost_basis_krw": 1800,
        "orderable_cash_krw": 3000,
        "cash_total_krw": 3500,
        "operating_equity_krw": 5000,
        "total_unrealized_net_pnl_krw": 200,
        "cash_weight_pct": 60.0,
        "holdings_weight_pct": 40.0,
        "allocation_gap_pct": 0.0,
        "allocation_consistency": "ok",
        "total_return_pct": 11.11,
        "display_equity_krw": 5000,
        "data_quality": {
            "used_cycle_fallback": True,
            "positions_from_cycle": True,
            "is_data_insufficient": False,
            "allocation_consistency": "ok",
            "equity_source": "cycle_holdings",
            "equity_unavailable_reason": None,
        },
    }
    assert normalizers._apply_account_weights(
        positions,
        account_view=account_view,
    ) == [
        {
            "evaluation_amount_krw": 2000,
            "average_cost_krw": 900,
            "holding_qty": 2,
            "net_pnl_krw": 200,
            "account_weight_pct": 40.0,
        }
    ]


def test_build_account_view_empty_input_full_dict():
    assert normalizers._build_account_view(
        None,
        positions=[],
        latest_cycle=None,
    ) == {
        "positions_count": None,
        "holdings_market_value_krw": None,
        "total_cost_basis_krw": None,
        "orderable_cash_krw": None,
        "cash_total_krw": None,
        "operating_equity_krw": None,
        "total_unrealized_net_pnl_krw": None,
        "cash_weight_pct": None,
        "holdings_weight_pct": None,
        "allocation_gap_pct": None,
        "allocation_consistency": "unknown",
        "total_return_pct": None,
        "display_equity_krw": None,
        "data_quality": {
            "used_cycle_fallback": False,
            "positions_from_cycle": False,
            "is_data_insufficient": True,
            "allocation_consistency": "unknown",
            "equity_source": None,
            "equity_unavailable_reason": (
                "no equity source — performance summary absent and cycle "
                "holdings_summary empty (account snapshot did not populate equity)"
            ),
        },
    }


def test_build_account_view_marks_equity_unavailable_reason_when_no_source():
    """D2: with no performance summary and no cycle holdings, the view must say
    WHY equity is unavailable instead of silently presenting 0/None."""
    view = normalizers._build_account_view(None, positions=[], latest_cycle=None)
    dq = view["data_quality"]
    assert view["display_equity_krw"] is None
    assert dq["equity_source"] is None
    assert dq["equity_unavailable_reason"] is not None
    assert "performance" in dq["equity_unavailable_reason"].lower()


def test_build_account_view_reports_equity_source_when_present():
    """D2: when equity is derivable, the source is named and reason is cleared."""
    view = normalizers._build_account_view(
        {"account_summary": {"current_equity_krw": 1_000_000}},
        positions=[],
        latest_cycle=None,
    )
    dq = view["data_quality"]
    assert view["display_equity_krw"] == 1_000_000
    assert dq["equity_source"] == "account_summary"
    assert dq["equity_unavailable_reason"] is None


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"action": "sell_order_succeeded"}, "SELL"),
        ({"order_type": "market_sell"}, "SELL"),
        ({"action": "order_succeeded"}, "BUY"),
    ],
)
def test_infer_order_side(record, expected):
    assert normalizers._infer_order_side(record) == expected


def test_normalize_orders_full_list():
    records = [
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "cycle_id": "c1",
            "action": "order_succeeded",
            "symbol": "005930",
            "raw_response": {"symbol_name": "삼성전자"},
            "qty": 2,
            "result": "ok",
            "reason": "entry",
            "environment": "mock",
        }
    ]

    assert normalizers._normalize_orders(records) == [
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "cycle_id": "c1",
            "side": "BUY",
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "qty": 2,
            "action": "order_succeeded",
            "result": "ok",
            "reason": "entry",
            "environment": "mock",
        }
    ]


def _cycle_record() -> dict:
    return {
        "timestamp": "2026-06-11T09:00:00+09:00",
        "cycle_id": "c1",
        "market_session": {"session": "REGULAR", "reason": "open"},
        "final_action": "BUY",
        "final_reason": "edge",
        "scheduler_state": {"decision": "SELL_PRIORITY_WITH_BUY"},
        "cycle_elapsed_ms": 123.4,
        "api_request_count": 7,
        "buy_scan_requested_count": 10,
        "buy_scan_evaluated_count": 8,
        "buy_scan_skipped_reason": "",
        "sell_evaluated_count": 3,
        "selected_primary_action_symbol": "005930",
        "selected_primary_action_side": "BUY",
        "selected_buy_candidate": {
            "symbol": "005930",
            "display_name": "삼성전자",
            "cost_block_reason": "none",
            "math_score_summary": "math",
            "mean_reversion_zscore": -1.2,
            "reversion_quality_score": 0.8,
            "overextension_penalty": 0.1,
            "mean_reversion_summary": "reversion",
            "portfolio_avg_correlation": 0.3,
            "portfolio_max_correlation": 0.5,
            "variance_increase_estimate": 0.02,
            "portfolio_risk_summary": "risk",
            "expected_cost_bps": 10.5,
            "expected_total_cost_krw": 1000,
            "net_edge_bps": 50.0,
            "score_summary": "score",
            "score_highlights": ["quality"],
            "score_penalties": ["spread"],
        },
        "selected_sell_candidate": {"symbol": "035420", "name": "NAVER"},
        "selection_details": {"selection_reason": "rank"},
        "buy_strategy_details": {"final_reason": "buy"},
        "sell_strategy_details": {"reason": "sell"},
        "risk_guard_result": {"buy": {"reason": "ok"}},
        "scanner_candidates_top": [
            {"symbol": "005930", "display_name": "삼성전자"},
            {"symbol": "000660", "display_name": "SK하이닉스", "score": 71},
        ],
        "buy_position_sizing": {
            "block_reason_label": "cash ok",
            "effective_math_multiplier": 0.75,
            "math_sizing_summary": "size",
            "math_sizing_reasons": ["budget"],
        },
        "buy_scan_budget_reserved": True,
        "buy_scan_reserve_used": False,
        "buy_scan_partial_budget": True,
        "sell_watch_capped_for_buy_scan": False,
        "rebalance_preview": {
            "preview_type": "swap",
            "status": "ready",
            "selected_pair": {"sell": "035420", "buy": "005930"},
            "selection_reason": "replace",
        },
    }


def test_normalize_cycles_full_list():
    record = _cycle_record()

    assert normalizers._normalize_cycles([record]) == [
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "cycle_id": "c1",
            "market_session": "REGULAR",
            "market_reason": "open",
            "final_action": "BUY",
            "final_reason": "edge",
            "scheduler_decision": "SELL_PRIORITY_WITH_BUY",
            "cycle_elapsed_ms": 123.4,
            "api_request_count": 7,
            "buy_scan_requested_count": 10,
            "buy_scan_evaluated_count": 8,
            "buy_scan_skipped_reason": "",
            "sell_evaluated_count": 3,
            "selected_primary_action_name": "005930",
            "selected_primary_action_symbol": "005930",
            "selected_primary_action_side": "BUY",
            "selected_buy_candidate": "삼성전자",
            "selected_buy_symbol": "005930",
            "selected_sell_candidate": "NAVER",
            "selected_sell_symbol": "035420",
            "selection_details": {"selection_reason": "rank"},
            "buy_strategy_details": {"final_reason": "buy"},
            "sell_strategy_details": {"reason": "sell"},
            "risk_guard_result": {"buy": {"reason": "ok"}},
            "scanner_candidates_top": [
                {"symbol": "005930", "display_name": "삼성전자"},
                {"symbol": "000660", "display_name": "SK하이닉스", "score": 71},
            ],
            "top_candidate_count": 2,
            "selected_buy_candidate_data": record["selected_buy_candidate"],
            "runner_up_candidate_data": {
                "symbol": "000660",
                "display_name": "SK하이닉스",
                "score": 71,
            },
            "runner_up_candidate": "SK하이닉스",
            "runner_up_symbol": "000660",
            "buy_position_sizing": record["buy_position_sizing"],
            "buy_scan_budget_reserved": True,
            "buy_scan_reserve_used": False,
            "buy_scan_partial_budget": True,
            "sell_watch_capped_for_buy_scan": False,
            "rebalance_preview": record["rebalance_preview"],
            "rebalance_type": "swap",
            "rebalance_status": "ready",
            "rebalance_selected_pair": {"sell": "035420", "buy": "005930"},
            "rebalance_selected_pair_reason": "replace",
            "cash_insufficient_reason": "cash ok",
            "cost_block_reason": "none",
            "math_score_summary": "math",
            "mean_reversion_zscore": -1.2,
            "reversion_quality_score": 0.8,
            "overextension_penalty": 0.1,
            "mean_reversion_summary": "reversion",
            "portfolio_avg_correlation": 0.3,
            "portfolio_max_correlation": 0.5,
            "variance_increase_estimate": 0.02,
            "portfolio_risk_summary": "risk",
            "effective_math_multiplier": 0.75,
            "math_sizing_summary": "size",
            "math_sizing_reasons": ["budget"],
            "expected_cost_bps": 10.5,
            "expected_total_cost_krw": 1000,
            "net_edge_bps": 50.0,
            "score_summary": "score",
            "score_highlights": ["quality"],
            "score_penalties": ["spread"],
            "raw": record,
        }
    ]


def test_normalize_candidates_full_list():
    assert normalizers._normalize_candidates(
        [
            {
                "scanner_candidates_top": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "passed_count": 4,
                        "score": 82.5,
                        "candidate": True,
                        "pass_pattern": "1111",
                        "net_profit_buffer_bps": 12.5,
                        "final_reason": "pass",
                        "score_summary": "score",
                        "score_highlights": ["quality"],
                        "score_penalties": ["spread"],
                        "expected_total_cost_krw": 1000,
                        "expected_cost_bps": 10.5,
                        "cost_quality_score": 0.8,
                        "expected_cost_penalty": -0.2,
                        "net_edge_bps": 50.0,
                        "cost_block_reason": "none",
                        "math_score_summary": "math",
                        "mean_reversion_zscore": -1.2,
                        "reversion_quality_score": 0.8,
                        "overextension_penalty": 0.1,
                        "mean_reversion_summary": "reversion",
                        "portfolio_avg_correlation": 0.3,
                        "portfolio_max_correlation": 0.5,
                        "variance_increase_estimate": 0.02,
                        "portfolio_risk_summary": "risk",
                    }
                ]
            }
        ]
    ) == [
        {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "passed_count": 4,
            "score": 82.5,
            "candidate": True,
            "pass_pattern": "1111",
            "profit_buffer_bps": 12.5,
            "final_reason": "pass",
            "score_summary": "score",
            "score_highlights": ["quality"],
            "score_penalties": ["spread"],
            "expected_total_cost_krw": 1000,
            "expected_cost_bps": 10.5,
            "cost_quality_score": 0.8,
            "expected_cost_penalty": -0.2,
            "net_edge_bps": 50.0,
            "cost_block_reason": "none",
            "math_score_summary": "math",
            "mean_reversion_zscore": -1.2,
            "reversion_quality_score": 0.8,
            "overextension_penalty": 0.1,
            "mean_reversion_summary": "reversion",
            "portfolio_avg_correlation": 0.3,
            "portfolio_max_correlation": 0.5,
            "variance_increase_estimate": 0.02,
            "portfolio_risk_summary": "risk",
        }
    ]


def test_latest_action_record_and_engine_state_view_full_dict():
    orders = [
        {
            "action": "skipped_buy_scan_budget_limited",
            "reason": "budget",
            "timestamp": "2026-06-11T09:01:00+09:00",
        },
        {
            "action": "blocked_buy_reentry_cooldown",
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "timestamp": "2026-06-11T09:02:00+09:00",
        },
        {"action": "blocked_same_symbol_daily_limit"},
    ]
    cycles = [
        {
            "market_session": "REGULAR",
            "market_reason": "open",
            "raw": {
                "scheduler_state": {
                    "decision": "SELL_PRIORITY_WITH_BUY",
                    "last_sell_check_at": "2026-06-11T08:50:00+09:00",
                    "last_buy_scan_at": "2026-06-11T08:55:00+09:00",
                    "sell_check_due": True,
                    "buy_scan_due": False,
                },
                "last_budget_status": {"recent_request_count": 2},
                "current_brake_state": "BUY_PAUSE",
                "regime_state": {
                    "current_regime": "CAUTION",
                    "regime_reason": "drawdown",
                    "regime_multiplier": 0.7,
                    "current_drawdown_pct": -2.5,
                },
                "buy_scan_requested_count": 10,
                "buy_scan_evaluated_count": 8,
                "api_request_count": 12,
                "cycle_elapsed_ms": 153.6,
            },
        }
    ]

    result = normalizers._build_engine_state_view(
        runtime_state={
            "last_action": "cycle",
            "last_cycle_result": "ok",
            "last_cycle_elapsed_ms": 153.6,
            "last_warning_count": 1,
            "last_error_count": 0,
            "last_snapshot_write_ok": True,
            "last_performance_write_ok": True,
            "last_budget_status": {"recent_request_count": 5, "quotes_used_this_tick": 1},
            "last_reentry_blocked_at": "2026-06-11T09:00:00+09:00",
            "buy_entries_by_symbol_today": {"005930": 1},
            "last_buy_entry_at_by_symbol": {"005930": "2026-06-11T08:40:00+09:00"},
        },
        cycles=cycles,
        orders=orders,
        market_status=SimpleNamespace(session="REGULAR"),
    )

    assert normalizers._latest_action_record(
        orders,
        {"blocked_buy_reentry_cooldown"},
    ) == orders[1]
    assert result == {
        "market_session": "REGULAR",
        "market_reason": "open",
        "last_action": "cycle",
        "last_cycle_result": "ok",
        "last_cycle_elapsed_ms": 153.6,
        "last_warning_count": 1,
        "last_error_count": 0,
        "last_snapshot_write_ok": True,
        "last_performance_write_ok": True,
        "scheduler_decision": "SELL_PRIORITY_WITH_BUY",
        "current_brake_state": "BUY_PAUSE",
        "current_regime": "CAUTION",
        "regime_reason": "drawdown",
        "regime_multiplier": 0.7,
        "regime_current_drawdown_pct": -2.5,
        "last_sell_check_at": "2026-06-11T08:50:00+09:00",
        "last_buy_scan_at": "2026-06-11T08:55:00+09:00",
        "sell_check_due": True,
        "buy_scan_due": False,
        "budget_status": {"recent_request_count": 5, "quotes_used_this_tick": 1},
        "budget_requests_used": 5,
        "budget_quotes_used": 1,
        "budget_backoff_remaining": None,
        "buy_scan_skip_action": "skipped_buy_scan_budget_limited",
        "buy_scan_skip_reason": "budget",
        "buy_scan_skip_at": "2026-06-11T09:01:00+09:00",
        "reentry_block_count": 1,
        "same_symbol_limit_block_count": 1,
        "recent_reentry_blocked_symbol": "005930",
        "recent_reentry_blocked_symbol_name": "삼성전자",
        "recent_reentry_blocked_at": "2026-06-11T09:02:00+09:00",
        "buy_entries_by_symbol_today": {"005930": 1},
        "last_buy_entry_at_by_symbol": {"005930": "2026-06-11T08:40:00+09:00"},
        "last_reentry_blocked_at_by_symbol": {},
        "buy_scan_requested_count": 10,
        "buy_scan_evaluated_count": 8,
        "api_request_count": 12,
        "cycle_elapsed_ms": 153.6,
    }


def test_build_engine_state_view_empty_input_full_dict():
    assert normalizers._build_engine_state_view(
        runtime_state={},
        cycles=[],
        orders=[],
        market_status=SimpleNamespace(session="CLOSED"),
    ) == {
        "market_session": "CLOSED",
        "market_reason": None,
        "last_action": None,
        "last_cycle_result": None,
        "last_cycle_elapsed_ms": None,
        "last_warning_count": None,
        "last_error_count": None,
        "last_snapshot_write_ok": None,
        "last_performance_write_ok": None,
        "scheduler_decision": None,
        "current_brake_state": None,
        "current_regime": None,
        "regime_reason": None,
        "regime_multiplier": None,
        "regime_current_drawdown_pct": None,
        "last_sell_check_at": None,
        "last_buy_scan_at": None,
        "sell_check_due": None,
        "buy_scan_due": None,
        "budget_status": {},
        "budget_requests_used": None,
        "budget_quotes_used": None,
        "budget_backoff_remaining": None,
        "buy_scan_skip_action": None,
        "buy_scan_skip_reason": None,
        "buy_scan_skip_at": None,
        "reentry_block_count": 0,
        "same_symbol_limit_block_count": 0,
        "recent_reentry_blocked_symbol": None,
        "recent_reentry_blocked_symbol_name": None,
        "recent_reentry_blocked_at": None,
        "buy_entries_by_symbol_today": {},
        "last_buy_entry_at_by_symbol": {},
        "last_reentry_blocked_at_by_symbol": {},
        "buy_scan_requested_count": None,
        "buy_scan_evaluated_count": None,
        "api_request_count": None,
        "cycle_elapsed_ms": None,
    }


def test_build_trade_journal_full_list():
    orders = [
        {
            "timestamp": "2026-06-11T09:00:00+09:00",
            "action": "order_succeeded",
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "qty": 2,
            "reason": "entry",
        },
        {
            "timestamp": "2026-06-11T09:30:00+09:00",
            "action": "sell_order_succeeded",
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "qty": 2,
            "reason": "exit",
        },
        {
            "timestamp": "2026-06-11T10:00:00+09:00",
            "action": "order_succeeded",
            "symbol": "000660",
            "symbol_name": "SK하이닉스",
            "qty": 1,
            "reason": "entry2",
        },
        {
            "timestamp": "2026-06-11T08:00:00+09:00",
            "action": "sell_order_succeeded",
            "symbol": "035420",
            "qty": 1,
            "reason": "manual",
        },
    ]
    assert normalizers._build_trade_journal(orders) == [
        {
            "symbol": "000660",
            "symbol_name": "SK하이닉스",
            "buy_ts": "2026-06-11T10:00:00+09:00",
            "sell_ts": "",
            "hold_mins": None,
            "qty": 1,
            "buy_reason": "entry2",
            "sell_reason": "",
            "status": "open",
        },
        {
            "symbol": "005930",
            "symbol_name": "삼성전자",
            "buy_ts": "2026-06-11T09:00:00+09:00",
            "sell_ts": "2026-06-11T09:30:00+09:00",
            "hold_mins": 30,
            "qty": 2,
            "buy_reason": "entry",
            "sell_reason": "exit",
            "status": "closed",
        },
        {
            "symbol": "035420",
            "symbol_name": "035420",
            "buy_ts": "",
            "sell_ts": "2026-06-11T08:00:00+09:00",
            "hold_mins": None,
            "qty": 1,
            "buy_reason": "",
            "sell_reason": "manual",
            "status": "sell_only",
        },
    ]


def test_build_trade_journal_empty_input_full_list():
    assert normalizers._build_trade_journal([]) == []
