import pytest

from app.dashboard import metrics
from app.dashboard import cycle_insights


def test_cycle_insights_facade_bindings_are_preserved():
    assert metrics.engine_short_label is cycle_insights.engine_short_label
    assert metrics.build_buy_judgement_snapshot is cycle_insights.build_buy_judgement_snapshot
    assert metrics.build_cycle_readable_summary is cycle_insights.build_cycle_readable_summary


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "데이터 부족"),
        (" regular ", "정규장"),
        ("SELL_PRIORITY_WITH_BUY", "매도 우선, 매수 병행"),
        ("custom_state", "CUSTOM STATE"),
    ],
)
def test_engine_short_label_representative_branches(value, expected):
    assert cycle_insights.engine_short_label(value) == expected


def test_build_buy_judgement_snapshot_full_dict():
    selected_candidate = {
        "symbol": "005930",
        "display_name": "삼성전자",
        "score_summary": "candidate score",
        "math_score_summary": "candidate math",
        "score_highlights": ["quality", "momentum"],
        "score_penalties": ["valuation"],
        "expected_total_cost_krw": 1200,
        "expected_cost_bps": 13.4,
        "cost_quality_score": 0.82,
        "expected_cost_penalty": -0.1,
        "net_edge_bps": 42.5,
        "cost_block_reason": "spread ok",
        "mean_reversion_zscore": -1.23,
        "reversion_quality_score": 0.71,
        "overextension_penalty": 0.05,
        "mean_reversion_summary": "mean reversion ready",
        "portfolio_avg_correlation": 0.34,
        "portfolio_max_correlation": 0.52,
        "variance_increase_estimate": 0.018,
        "portfolio_risk_summary": "risk balanced",
    }
    runner_up = {
        "symbol": "000660",
        "display_name": "SK하이닉스",
        "score_summary": "runner score",
    }
    data = {
        "candidate_rows": [selected_candidate, runner_up],
        "cycles": [
            {
                "timestamp": "2026-06-11T09:03:00+09:00",
                "selected_buy_symbol": "005930",
                "selection_details": {"selection_reason": " highest score "},
                "buy_strategy_details": {"final_reason": " edge positive "},
                "rebalance_preview": {
                    "selection_reason": " rotate weakest ",
                    "preview_type": "replace",
                    "status": "ready",
                    "selected_pair": {"sell": "035420", "buy": "005930"},
                    "current_weakest_candidates": [{"symbol": "035420"}],
                    "replacement_candidates": [{"symbol": "005930"}],
                },
                "buy_position_sizing": {
                    "effective_math_multiplier": 0.8,
                    "math_sizing_summary": " size reduced ",
                    "math_sizing_reasons": ["risk budget"],
                },
                "cash_insufficient_reason": "cash ok",
                "raw": {
                    "last_selected_symbol": "005930",
                    "selected_buy_candidate": {
                        "symbol": "005930",
                        "score_summary": "raw score",
                    },
                },
            }
        ],
    }

    assert cycle_insights.build_buy_judgement_snapshot(data) == {
        "selected_candidate": selected_candidate,
        "selection_reason": "highest score",
        "buy_final_reason": "edge positive",
        "score_summary": "candidate score",
        "math_score_summary": "candidate math",
        "score_highlights": ["quality", "momentum"],
        "score_penalties": ["valuation"],
        "expected_total_cost_krw": 1200,
        "expected_cost_bps": 13.4,
        "cost_quality_score": 0.82,
        "expected_cost_penalty": -0.1,
        "net_edge_bps": 42.5,
        "cost_block_reason": "spread ok",
        "mean_reversion_zscore": -1.23,
        "reversion_quality_score": 0.71,
        "overextension_penalty": 0.05,
        "mean_reversion_summary": "mean reversion ready",
        "portfolio_avg_correlation": 0.34,
        "portfolio_max_correlation": 0.52,
        "variance_increase_estimate": 0.018,
        "portfolio_risk_summary": "risk balanced",
        "effective_math_multiplier": 0.8,
        "math_sizing_summary": "size reduced",
        "math_sizing_reasons": ["risk budget"],
        "cash_insufficient_reason": "cash ok",
        "rebalance_reason": "rotate weakest",
        "rebalance_type": "replace",
        "rebalance_status": "ready",
        "rebalance_selected_pair": {"sell": "035420", "buy": "005930"},
        "rebalance_current_weakest_candidates": [{"symbol": "035420"}],
        "rebalance_replacement_candidates": [{"symbol": "005930"}],
        "top_candidates": [selected_candidate, runner_up],
        "updated_at": "2026-06-11T09:03:00+09:00",
    }


def test_build_cycle_readable_summary_full_dict():
    cycle = {
        "risk_guard_result": {
            "buy": {"reason": "max exposure"},
            "sell": {"reason": "sell ok"},
        },
        "selection_details": {"selection_reason": " score leads "},
        "buy_strategy_details": {"final_reason": " buy edge "},
        "sell_strategy_details": {"reason": " hold winners "},
        "selected_buy_candidate_data": {
            "display_name": "삼성전자",
            "score_summary": "candidate score",
            "math_score_summary": "candidate math",
            "score_highlights": ["earnings", "trend"],
            "score_penalties": ["spread"],
            "mean_reversion_summary": "z-score supportive",
            "portfolio_risk_summary": "correlation acceptable",
        },
        "runner_up_candidate_data": {
            "name": "SK하이닉스",
            "score": 71.236,
            "passed_count": 4,
        },
        "rebalance_preview": {
            "selection_reason": "replace weak",
            "preview_type": "swap",
            "status": "ready",
        },
        "rebalance_selected_pair": {"sell": "035420", "buy": "005930"},
        "cash_insufficient_reason": "cash ok",
        "cost_block_reason": "cost ok",
        "scheduler_decision": "SELL_PRIORITY_WITH_BUY",
        "buy_scan_requested_count": 10,
        "buy_scan_evaluated_count": 8,
        "cycle_elapsed_ms": 153.6,
        "api_request_count": 12,
        "buy_position_sizing": {
            "effective_math_multiplier": 0.75,
            "math_sizing_summary": "75 percent",
        },
    }

    assert cycle_insights.build_cycle_readable_summary(cycle) == {
        "risk_summary": "max exposure",
        "selection_reason": "score leads",
        "buy_reason": "buy edge",
        "sell_reason": "hold winners",
        "rebalance_reason": "replace weak",
        "rebalance_type": "swap",
        "rebalance_status": "ready",
        "rebalance_pair": {"sell": "035420", "buy": "005930"},
        "cash_block_reason": "cash ok",
        "cost_block_reason": "cost ok",
        "scheduler_summary": "매도 우선, 매수 병행",
        "cadence_summary": "8 / 10 평가",
        "execution_summary": "requests 12 · elapsed 154ms",
        "selected_vs_runner_up": "삼성전자 vs SK하이닉스",
        "runner_up_summary": "score 71.24 · 통과 4개",
        "selected_score_summary": "candidate score",
        "math_score_summary": "candidate math",
        "mean_reversion_summary": "z-score supportive",
        "portfolio_risk_summary": "correlation acceptable",
        "effective_math_multiplier_text": "0.75x",
        "math_sizing_summary": "75 percent",
        "selected_score_highlights_text": "earnings, trend",
        "selected_score_penalties_text": "spread",
    }
