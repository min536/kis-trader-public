from app.tools import replay_cycles
from app.tools import replay_views


_REPLAY_VIEW_NAMES = (
    "_match",
    "_parse_clock",
    "_record_time",
    "_time_match",
    "_record_symbol_set",
    "_symbol_match",
    "_compact_reason",
    "_candidate_name",
    "_safe_float",
    "_coalesce_float",
    "_technical_overlay_payload",
    "_selected_buy",
    "_selected_primary_name",
    "_sell_final_review_name",
    "_top_candidates",
    "_runner_up",
    "_pre_gating_view",
    "_staged_scan_view",
    "_buy_gap_text",
    "_buy_funnel_view",
)


def _record() -> dict:
    return {
        "timestamp": "2026-04-10T09:00:45+09:00",
        "final_action": "BUY_ORDER_SUCCEEDED",
        "market_session": {"session": "REGULAR"},
        "selected_primary_action_symbol": "005930",
        "rate_limit_partial_stop_symbol": "000660",
        "sell_watch_evaluated_symbols": ["035420"],
        "sell_watch_skipped_symbols": ["051910"],
        "selected_buy_candidate": {
            "symbol": "005930",
            "display_name": "Samsung",
            "passed_count": 2,
            "enabled_count": 5,
            "feature_map": {"technical_features": {"trend_alignment_score": 0.3}},
            "score_components": {"macd_momentum_score": 0.2},
            "feature_summaries": {"technical_features": "trend+macd"},
            "score_highlights": ["trend_alignment"],
        },
        "selected_sell_candidate": {"symbol": "035420", "name": "NAVER"},
        "sell_watch_final_review": {"symbol": "000660", "display_name": "SK"},
        "scanner_candidates_top": [
            {"symbol": "005930", "display_name": "Samsung"},
            {"symbol": "000660", "display_name": "SK", "score": 70},
        ],
        "pre_gating": {"mode": "direct"},
        "pre_gating_summary": "summary",
        "pre_gating_rejected_count": 2,
        "pre_gating_rejected_symbols": ["000660"],
        "pre_gating_reasons_by_symbol": {"000660": "cooldown"},
        "pre_gating_stage": "before",
        "reentry_state_by_symbol": {"000660": "blocked"},
        "last_exit_reason_by_symbol": {"000660": "stop"},
        "reentry_block_reason_counts": {"cooldown": 1},
        "reentry_allowed_count": 1,
        "reentry_blocked_count": 2,
        "buy_scan_profile": "layered",
        "buy_scan_universe_core_count": 10,
        "buy_scan_universe_rotating_count": 5,
        "buy_scan_universe_exploration_count": 2,
        "buy_scan_pre_gating_count": 8,
        "buy_scan_shallow_ranked_count": 4,
        "buy_scan_deep_eval_limit": 3,
        "buy_scan_exploration_quota_used": 1,
        "buy_scan_layered_symbols_preview": {"core": ["005930"]},
        "buy_scan_shallow_shortlist_preview": ["005930", "000660"],
        "buy_scan_requested_count": 12,
        "buy_scan_evaluated_count": 3,
        "executed_order_count": 1,
        "sell_evaluated_count": 4,
        "sell_triggered_count": 1,
        "pre_gate_rejection_counts": {"cooldown": 2},
    }


def test_replay_view_facade_bindings_are_preserved():
    for name in _REPLAY_VIEW_NAMES:
        assert getattr(replay_cycles, name) is getattr(replay_views, name)


def test_replay_small_helpers_direct_outputs():
    record = _record()

    assert replay_views._match(record, action="BUY", session="REGULAR")
    assert replay_views._parse_clock("09:00:45").isoformat() == "09:00:45"
    assert replay_views._record_time(record).isoformat() == "09:00:45"
    assert replay_views._time_match(
        record,
        from_time=replay_views._parse_clock("09:00"),
        to_time=replay_views._parse_clock("09:01"),
    )
    assert sorted(replay_views._record_symbol_set(record)) == [
        "000660",
        "005930",
        "035420",
        "051910",
    ]
    assert replay_views._symbol_match(record, symbol="000660")
    assert replay_views._compact_reason("x" * 125) == ("x" * 117) + "..."
    assert replay_views._candidate_name({"name": "Name"}) == "Name"
    assert replay_views._safe_float("1.5") == 1.5
    assert replay_views._safe_float("bad") is None
    assert replay_views._coalesce_float("bad", None, "2.5") == 2.5
    assert replay_views._selected_primary_name(record) == "005930"
    assert replay_views._sell_final_review_name(record) == "SK"
    assert replay_views._buy_gap_text(record) == "2/3 (gap=1)"


def test_replay_candidate_and_technical_views_full_outputs():
    record = _record()

    assert replay_views._technical_overlay_payload(
        record["selected_buy_candidate"]
    ) == {
        "available": True,
        "active": True,
        "trend": 0.3,
        "macd": 0.2,
        "total": 0.5,
        "summary": "active (trend=0.30, macd=0.20)",
        "names": "trend+macd",
    }
    assert replay_views._technical_overlay_payload(None) == {
        "available": False,
        "active": False,
        "trend": None,
        "macd": None,
        "total": None,
        "summary": "없음",
        "names": "-",
    }
    assert replay_views._selected_buy(record) == record["selected_buy_candidate"]
    assert replay_views._top_candidates(record) == [
        {"symbol": "005930", "display_name": "Samsung"},
        {"symbol": "000660", "display_name": "SK", "score": 70},
    ]
    assert replay_views._runner_up(record) == {
        "symbol": "000660",
        "display_name": "SK",
        "score": 70,
    }


def test_replay_builder_views_full_dicts():
    record = _record()

    assert replay_views._pre_gating_view(record) == {
        "payload": {"mode": "direct"},
        "summary": "summary",
        "rejected_count": 2,
        "rejected_symbols": ["000660"],
        "reasons_by_symbol": {"000660": "cooldown"},
        "stage": "before",
        "reentry_state_by_symbol": {"000660": "blocked"},
        "last_exit_reason_by_symbol": {"000660": "stop"},
        "reentry_block_reason_counts": {"cooldown": 1},
        "reentry_allowed_count": 1,
        "reentry_blocked_count": 2,
    }
    assert replay_views._pre_gating_view(
        {
            "selection_details": {
                "pre_gating": {
                    "rejected": [{"symbol": "A", "reason_code": "r"}],
                    "stage": "legacy",
                    "reentry_allowed_count": 1,
                }
            },
            "buy_scan_requested_count": 2,
        }
    ) == {
        "payload": {
            "rejected": [{"symbol": "A", "reason_code": "r"}],
            "stage": "legacy",
            "reentry_allowed_count": 1,
        },
        "summary": None,
        "rejected_count": 1,
        "rejected_symbols": ["A"],
        "reasons_by_symbol": {"A": "r"},
        "stage": "legacy",
        "reentry_state_by_symbol": {},
        "last_exit_reason_by_symbol": {},
        "reentry_block_reason_counts": {},
        "reentry_allowed_count": 1,
        "reentry_blocked_count": 0,
    }
    assert replay_views._staged_scan_view(record) == {
        "profile": "layered",
        "core_count": 10,
        "rotating_count": 5,
        "exploration_count": 2,
        "pre_gating_count": 8,
        "shallow_ranked_count": 4,
        "deep_eval_limit": 3,
        "exploration_quota_used": 1,
        "layered_symbols_preview": {"core": ["005930"]},
        "shallow_shortlist_preview": ["005930", "000660"],
    }
    assert replay_views._buy_funnel_view(record) == {
        "universe_size": 12,
        "layered_universe_size": 0,
        "layered_out_count": 0,
        "pre_gate_passed": 10,
        "pre_gate_rejected_total": 2,
        "pre_gate_rejection_counts": {"cooldown": 2},
        "shallow_ranked_count": 4,
        "shallow_shortlist_size": 2,
        "deep_eval_count": 3,
        "final_candidate_count": 1,
        "executed_order_count": 1,
        "sell_evaluated_count": 4,
        "sell_triggered_count": 1,
    }
