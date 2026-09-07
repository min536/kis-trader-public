from app.tools import analyze_core_bucket
from app.tools import core_bucket_analysis


_CORE_BUCKET_ANALYSIS_NAMES = (
    "_pct",
    "_safe_mean",
    "_safe_median",
    "_safe_float",
    "_coalesce_metric",
    "_build_candidate_detail_index",
    "_enrich_rows",
    "_metric_stats",
    "_top_symbol_rows",
    "_component_profile",
    "_sample_rows",
    "_strategy_hit_rate",
    "_passed_count_distribution",
    "_top_fail_patterns",
    "_pattern_passing_rules",
    "_strategy_rate_map",
    "_infer_required_pass_count",
    "_rule_contrast_interpretation",
    "_threshold_simulation",
    "_rule_lift_opportunities",
    "_symbol_failure_diagnosis",
    "_shadow_analysis",
    "_build_human_summary",
)


def _snapshot_rows() -> list[dict]:
    return [
        {
            "cycle_id": "c1",
            "selected_buy_candidate": {
                "symbol": "005930",
                "score": 80,
                "passed_count": 4,
                "enabled_count": 5,
                "net_profit_buffer_bps": "12.5",
                "expected_cost_bps": 3.2,
                "expected_cost_penalty": -0.1,
                "cost_quality_score": 0.9,
                "cost_block_reason": "none",
                "score_highlights": ["trend"],
                "score_penalties": ["cost"],
                "final_reason": "ok",
                "score_components": {
                    "trend_alignment_score": 0.4,
                    "macd_momentum_score": 0.2,
                    "mean_reversion_bonus": 0.3,
                },
                "feature_map": {
                    "mean_reversion_features": {"overextension_penalty": 0.05}
                },
            },
            "scanner_candidates_top": [
                {
                    "symbol": "000660",
                    "score": "70",
                    "passed_count": "3",
                    "enabled_count": 5,
                    "net_profit_buffer_bps": 8,
                    "expected_cost_bps": 4.1,
                    "cost_block_reason": "spread",
                    "score_components": {
                        "trend_alignment_score": 0.1,
                        "expected_cost_penalty": 0.2,
                    },
                    "feature_map": {
                        "technical_features": {"macd_momentum_score": 0.15},
                        "mean_reversion_features": {"mean_reversion_bonus": 0.1},
                    },
                    "score_highlights": ["cheap"],
                    "score_penalties": ["weak"],
                    "final_reason": "runner",
                }
            ],
        }
    ]


def _rows() -> list[dict]:
    return [
        {
            "cycle_id": "c1",
            "symbol": "005930",
            "symbol_name": "Samsung",
            "selection_bucket": "core",
            "score_deep": 72.0,
            "rejection_reason": "passed_count_insufficient",
            "passed_count_deep": 2,
            "strategy_pass_pattern": "P F P F F",
        },
        {
            "cycle_id": "c1",
            "symbol": "000660",
            "symbol_name": "SK",
            "selection_bucket": "rotating",
            "score_deep": 82.0,
            "final_candidate": True,
            "passed_count_deep": 4,
            "strategy_pass_pattern": "P P P F P",
        },
        {
            "cycle_id": "c2",
            "symbol": "035420",
            "symbol_name": "NAVER",
            "selection_bucket": "core",
            "score_deep": 60.0,
            "rejection_reason": "cost_filter_blocked",
            "passed_count_deep": "bad",
            "strategy_pass_pattern": "F F F F F",
        },
    ]


def _enriched_rows() -> list[dict]:
    return core_bucket_analysis._enrich_rows(
        _rows(),
        core_bucket_analysis._build_candidate_detail_index(_snapshot_rows()),
    )


def test_core_bucket_analysis_facade_bindings_are_preserved():
    for name in _CORE_BUCKET_ANALYSIS_NAMES:
        assert getattr(analyze_core_bucket, name) is getattr(core_bucket_analysis, name)


def test_scalar_helpers_direct_outputs():
    assert core_bucket_analysis._pct(1, 4) == "25.0%"
    assert core_bucket_analysis._pct(1, 0) == "0.0%"
    assert core_bucket_analysis._safe_mean([1.0, 2.0]) == 1.5
    assert core_bucket_analysis._safe_mean([]) == 0.0
    assert core_bucket_analysis._safe_median([1.0, 3.0, 2.0]) == 2.0
    assert core_bucket_analysis._safe_float("1.5") == 1.5
    assert core_bucket_analysis._safe_float("bad") is None
    assert core_bucket_analysis._coalesce_metric("bad", None, "2.5") == 2.5


def test_candidate_detail_index_and_enriched_rows_full_dicts():
    index = core_bucket_analysis._build_candidate_detail_index(_snapshot_rows())

    assert index == {
        ("c1", "005930"): {
            "detail_source": "selected_buy_candidate",
            "passed_count": 4.0,
            "enabled_count": 5.0,
            "detail_score": 80.0,
            "net_profit_buffer_bps": 12.5,
            "expected_cost_bps": 3.2,
            "expected_cost_penalty": -0.1,
            "cost_quality_score": 0.9,
            "cost_block_reason": "none",
            "trend_alignment_score": 0.4,
            "macd_momentum_score": 0.2,
            "technical_total_score": 0.6,
            "mean_reversion_bonus": 0.3,
            "overextension_penalty": 0.05,
            "score_highlights": ["trend"],
            "score_penalties": ["cost"],
            "final_reason_detail": "ok",
        },
        ("c1", "000660"): {
            "detail_source": "scanner_candidates_top",
            "passed_count": 3.0,
            "enabled_count": 5.0,
            "detail_score": 70.0,
            "net_profit_buffer_bps": 8.0,
            "expected_cost_bps": 4.1,
            "expected_cost_penalty": 0.2,
            "cost_quality_score": None,
            "cost_block_reason": "spread",
            "trend_alignment_score": 0.1,
            "macd_momentum_score": 0.15,
            "technical_total_score": 0.25,
            "mean_reversion_bonus": 0.1,
            "overextension_penalty": None,
            "score_highlights": ["cheap"],
            "score_penalties": ["weak"],
            "final_reason_detail": "runner",
        },
    }
    assert core_bucket_analysis._enrich_rows(_rows(), index) == _enriched_rows()


def test_metric_profile_samples_and_symbol_rows_full_outputs():
    enriched = _enriched_rows()
    core_rows = [enriched[0], enriched[2]]

    assert core_bucket_analysis._metric_stats(enriched, "score_deep") == {
        "count": 3,
        "avg": 71.3333,
        "median": 72.0,
        "min": 60.0,
        "max": 82.0,
    }
    assert core_bucket_analysis._top_symbol_rows(enriched, limit=2) == [
        {"symbol": "005930", "symbol_name": "Samsung", "count": 1},
        {"symbol": "000660", "symbol_name": "SK", "count": 1},
    ]
    assert core_bucket_analysis._component_profile(core_rows) == {
        "row_count": 2,
        "detail_coverage_count": 1,
        "detail_coverage_pct": "50.0%",
        "score_deep": {"count": 2, "avg": 66.0, "median": 66.0, "min": 60.0, "max": 72.0},
        "passed_count": {"count": 1, "avg": 4.0, "median": 4.0, "min": 4.0, "max": 4.0},
        "technical_total_score": {"count": 1, "avg": 0.6, "median": 0.6, "min": 0.6, "max": 0.6},
        "trend_alignment_score": {"count": 1, "avg": 0.4, "median": 0.4, "min": 0.4, "max": 0.4},
        "macd_momentum_score": {"count": 1, "avg": 0.2, "median": 0.2, "min": 0.2, "max": 0.2},
        "mean_reversion_bonus": {"count": 1, "avg": 0.3, "median": 0.3, "min": 0.3, "max": 0.3},
        "overextension_penalty": {"count": 1, "avg": 0.05, "median": 0.05, "min": 0.05, "max": 0.05},
        "net_profit_buffer_bps": {"count": 1, "avg": 12.5, "median": 12.5, "min": 12.5, "max": 12.5},
        "expected_cost_bps": {"count": 1, "avg": 3.2, "median": 3.2, "min": 3.2, "max": 3.2},
        "expected_cost_penalty": {"count": 1, "avg": -0.1, "median": -0.1, "min": -0.1, "max": -0.1},
        "cost_quality_score": {"count": 1, "avg": 0.9, "median": 0.9, "min": 0.9, "max": 0.9},
        "cost_block_reasons": {"none": 1},
        "rejection_reasons": {
            "passed_count_insufficient": 1,
            "cost_filter_blocked": 1,
        },
        "technical_available_count": 1,
        "technical_nonzero_count": 1,
        "mean_reversion_nonzero_count": 1,
        "top_highlights": {"trend": 1},
        "top_penalties": {"cost": 1},
    }
    assert core_bucket_analysis._sample_rows(enriched, limit=2) == [
        {
            "cycle_id": "c1",
            "symbol": "000660",
            "symbol_name": "SK",
            "bucket": "rotating",
            "score_deep": 82.0,
            "passed_count": 3.0,
            "rejection_reason": None,
            "cost_block_reason": "spread",
            "technical_total_score": 0.25,
            "mean_reversion_bonus": 0.1,
            "expected_cost_penalty": 0.2,
        },
        {
            "cycle_id": "c1",
            "symbol": "005930",
            "symbol_name": "Samsung",
            "bucket": "core",
            "score_deep": 72.0,
            "passed_count": 4.0,
            "rejection_reason": "passed_count_insufficient",
            "cost_block_reason": "none",
            "technical_total_score": 0.6,
            "mean_reversion_bonus": 0.3,
            "expected_cost_penalty": -0.1,
        },
    ]


def test_strategy_rule_helpers_full_outputs():
    enriched = _enriched_rows()
    core_rows = [enriched[0], enriched[2]]
    non_core_rows = [enriched[1]]
    core_strategy = core_bucket_analysis._strategy_hit_rate(core_rows)
    non_core_strategy = core_bucket_analysis._strategy_hit_rate(non_core_rows)

    assert core_bucket_analysis._strategy_hit_rate(enriched) == {
        "parsed_rows": 3,
        "per_strategy": {
            "intraday_pullback": {"pass_count": 2, "total": 3, "pass_pct": "66.7%"},
            "rebound_from_low": {"pass_count": 1, "total": 3, "pass_pct": "33.3%"},
            "controlled_down_day": {"pass_count": 2, "total": 3, "pass_pct": "66.7%"},
            "gap_down_open": {"pass_count": 0, "total": 3, "pass_pct": "0.0%"},
            "range_recovery": {"pass_count": 1, "total": 3, "pass_pct": "33.3%"},
        },
    }
    assert core_bucket_analysis._passed_count_distribution(enriched) == {
        2: 1,
        4: 1,
        "bad": 1,
    }
    assert core_bucket_analysis._top_fail_patterns(enriched, limit=2) == [
        {
            "pattern": "P F P F F",
            "count": 1,
            "passing_rules": ["intraday_pullback", "controlled_down_day"],
            "pass_count": 2,
        },
        {
            "pattern": "P P P F P",
            "count": 1,
            "passing_rules": [
                "intraday_pullback",
                "rebound_from_low",
                "controlled_down_day",
                "range_recovery",
            ],
            "pass_count": 4,
        },
    ]
    assert core_bucket_analysis._pattern_passing_rules("P F P F P") == [
        "intraday_pullback",
        "controlled_down_day",
        "range_recovery",
    ]
    assert core_bucket_analysis._strategy_rate_map(core_strategy) == {
        "intraday_pullback": 0.5,
        "rebound_from_low": 0.0,
        "controlled_down_day": 0.5,
        "gap_down_open": 0.0,
        "range_recovery": 0.0,
    }
    assert core_bucket_analysis._infer_required_pass_count(core_rows) == 3
    assert core_bucket_analysis._rule_contrast_interpretation(
        core_strategy,
        non_core_strategy,
        core_rows,
        3,
    ) == {
        "verdict": "rule_family_mismatch",
        "explanation": (
            "2 rule(s) almost never fire for core but reliably fire for non-core: "
            "['rebound_from_low', 'range_recovery']. Core names cannot reach "
            "required_pass_count on these rules structurally."
        ),
        "structural_gap_rules": ["rebound_from_low", "range_recovery"],
        "threshold_candidate_rules": ["intraday_pullback", "controlled_down_day"],
        "shared_rules": [],
        "inferred_required_pass_count": 3,
        "dominant_fail_pattern": "P F P F F",
        "dominant_fail_pattern_count": 1,
        "dominant_passing_rules": ["intraday_pullback", "controlled_down_day"],
        "gap_to_threshold": 1,
    }


def test_shadow_analysis_and_human_summary_full_outputs():
    enriched = _enriched_rows()
    shadow_rows = [
        {
            "core_shadow_evaluated": True,
            "core_shadow_trend_gate_passed": True,
            "core_shadow_passed": True,
            "core_shadow_pattern": "P F P",
            "rejection_reason": "passed_count_insufficient",
            "passed_count_deep": 2,
        },
        {
            "core_shadow_evaluated": True,
            "core_shadow_trend_gate_passed": False,
            "core_shadow_passed": False,
            "core_shadow_pattern": "BLOCKED",
            "rejection_reason": "cost_filter_blocked",
        },
    ]

    assert core_bucket_analysis._shadow_analysis(shadow_rows) == {
        "available": True,
        "evaluated_count": 2,
        "trend_gate_passed_count": 1,
        "trend_gate_blocked_count": 1,
        "shadow_passed_count": 1,
        "shadow_pass_rate_pct": "100.0%",
        "top_patterns": [
            {
                "pattern": "P F P",
                "count": 1,
                "passing_rules": ["non_overextension", "intraday_stability"],
                "pass_count": 2,
            }
        ],
        "shadow_pass_existing_rejection_reasons": {"passed_count_insufficient": 1},
        "shadow_pass_existing_pcount": {2: 1},
    }
    assert core_bucket_analysis._shadow_analysis([]) == {
        "available": False,
        "evaluated_count": 0,
    }
    assert core_bucket_analysis._build_human_summary(
        core_bucket_analysis._component_profile([enriched[0], enriched[2]]),
        core_bucket_analysis._component_profile([enriched[1]]),
    ) == (
        "core final candidate 부재는 score gap 66.00 vs 82.00, "
        "core cost blocks present {'none': 1} 영향이 커 보입니다."
    )
