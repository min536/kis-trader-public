from pathlib import Path
from unittest import mock

from app.tools import postrun_diagnostics
from app.tools import postrun_report


_POSTRUN_REPORT_NAMES = (
    "_compact_counter",
    "_build_filtered_core_bucket_summary",
    "build_report",
    "_resolve_next_steps",
    "_derive_diagnostic_conclusion",
    "build_md_report",
)


def test_postrun_report_facade_bindings_are_preserved():
    for name in _POSTRUN_REPORT_NAMES:
        assert getattr(postrun_diagnostics, name) is getattr(postrun_report, name)


def test_compact_counter_sorts_and_limits_numeric_values():
    assert postrun_report._compact_counter(
        {"b": "3", "a": 3, "skip": "bad", "zero": 0},
        limit=2,
    ) == [("a", 3), ("b", 3)]


def test_resolve_next_steps_includes_session_and_last_n_args():
    assert postrun_report._resolve_next_steps(
        "score_threshold_bottleneck",
        date="20260612",
        account="acct",
        session="REGULAR",
        last_n=5,
    ) == [
        {
            "label": "deep_eval → final_candidate 병목 상세",
            "cmd": (
                "python3 -m app.tools.analyze_selection_bottleneck"
                " --date 20260612 --account acct --session REGULAR"
            ),
        },
        {
            "label": "core vs non-core score 성분 비교",
            "cmd": (
                "python3 -m app.tools.analyze_core_bucket"
                " --date 20260612 --account acct --session REGULAR"
            ),
        },
    ]


def test_derive_diagnostic_conclusion_score_bottleneck_full_dict():
    report = {
        "bucket_final_exec": {
            "core": {"deep_eval": 2, "final_candidate": 0, "executed": 0}
        },
        "core_bucket": {
            "core_rejection_reasons": {
                "score_below_threshold": 2,
                "passed_count_insufficient": 1,
            }
        },
        "technical_activation": {
            "meta": {"analyzed_rows": 10},
            "summary": {
                "history_sufficient_count": 8,
                "either_nonzero_count": 2,
            },
        },
    }

    assert postrun_report._derive_diagnostic_conclusion(report) == {
        "core_stuck": True,
        "core_deep": 2,
        "core_final": 0,
        "core_exec": 0,
        "dominant_core_reason": "score_below_threshold(2)",
        "semantic_counts": {
            "score_below_threshold": 2,
            "passed_count_insufficient": 1,
            "cost_filter_blocked": 0,
            "profit_buffer_insufficient": 0,
        },
        "analyzed_rows": 10,
        "hist_rate": 0.8,
        "act_rate": 0.2,
        "cold_start_signal": False,
        "low_activation": False,
        "bottleneck": "score_threshold_bottleneck",
        "hint": "전략은 통과했지만 최종 score가 선택 임계값 미만입니다. analyze_core_bucket으로 score 성분 분포(보너스/패널티)를 확인하세요.",
        "next_steps": [],
    }


def test_derive_diagnostic_conclusion_non_stuck_full_dict():
    report = {
        "bucket_final_exec": {
            "core": {"deep_eval": 2, "final_candidate": 1, "executed": 1}
        },
        "core_bucket": {"core_rejection_reasons": {}},
        "technical_activation": {"meta": {}, "summary": {}},
    }

    assert postrun_report._derive_diagnostic_conclusion(report) == {
        "core_stuck": False,
        "core_deep": 2,
        "core_final": 1,
        "core_exec": 1,
        "dominant_core_reason": "—",
        "semantic_counts": {
            "score_below_threshold": 0,
            "passed_count_insufficient": 0,
            "cost_filter_blocked": 0,
            "profit_buffer_insufficient": 0,
        },
        "analyzed_rows": 0,
        "hist_rate": 0.0,
        "act_rate": 0.0,
        "cold_start_signal": False,
        "low_activation": False,
        "bottleneck": "none_core_is_converting",
        "hint": "core 버킷이 정상적으로 final_candidate를 생성하고 있습니다.",
        "next_steps": [],
    }


def test_build_filtered_core_bucket_summary_full_dict():
    rows = [
        {
            "cycle_id": "c1",
            "selection_bucket": "core",
            "deep_evaluated": True,
            "final_candidate": False,
            "rejection_reason": "score_below_threshold",
            "symbol": "005930",
            "symbol_name": "Samsung",
            "score_deep": 70.0,
        },
        {
            "cycle_id": "c1",
            "selection_bucket": "rotating",
            "deep_evaluated": True,
            "final_candidate": True,
            "symbol": "000660",
            "symbol_name": "SK",
            "score_deep": 82.0,
        },
        {
            "cycle_id": "c2",
            "selection_bucket": "core",
            "deep_evaluated": True,
            "final_candidate": False,
            "rejection_reason": "cost_filter_blocked",
            "symbol": "035420",
            "symbol_name": "NAVER",
            "score_deep": 65.0,
        },
    ]

    with mock.patch.object(postrun_report, "_log_path", return_value=Path("/tmp/fake.jsonl")), mock.patch.object(
        postrun_report,
        "_load_rows",
        return_value=(rows, ["warn"]),
    ), mock.patch.object(
        postrun_report,
        "_filter_session",
        side_effect=lambda loaded_rows, session: loaded_rows,
    ), mock.patch.object(
        postrun_report,
        "_filter_cycle_ids",
        side_effect=lambda loaded_rows, ids: [
            row for row in loaded_rows if row.get("cycle_id") in ids
        ],
    ):
        assert postrun_report._build_filtered_core_bucket_summary(
            account="acct",
            date="20260612",
            session="REGULAR",
            selected_cycle_ids=["c1"],
        ) == {
            "core_loser_count": 1,
            "non_core_final_count": 1,
            "top_core_symbols": [
                {"symbol": "005930", "symbol_name": "Samsung", "count": 1}
            ],
            "top_non_core_final_symbols": [
                {"symbol": "000660", "symbol_name": "SK", "count": 1}
            ],
            "core_rejection_reasons": {"score_below_threshold": 1},
            "avg_core_loser_score_deep": 70.0,
            "avg_non_core_final_score_deep": 82.0,
            "human_summary": "top core rejection=score_below_threshold(1), avg score_deep 70.00 vs 82.00",
            "load_warnings": ["warn"],
        }


def test_build_report_full_dict():
    candidate = {
        "meta": {"selected_cycle_ids": ["c1"]},
        "funnel": {"total": 3, "stages": {"raw": 3, "deep_eval": 2}},
        "distributions": {
            "rejection_reasons": {"score_below_threshold": 2, "other": 1}
        },
        "bucket_summary": {
            "core": {
                "total": 2,
                "stages": {"deep_eval": 2},
                "final_candidate_count": 0,
                "executed_count": 0,
                "avg_deep_score": 71.234,
            },
            "rotating": {
                "total": 1,
                "stages": {"deep_eval": 1, "final_candidate": 1, "executed": 1},
                "avg_deep_score": 82.0,
            },
        },
    }
    validation = {
        "checks": {"bad_a": {"count": 1}, "bad_b": {"count": 0}},
        "any_issues": True,
    }
    technical = {
        "meta": {"analyzed_rows": 10},
        "activation_summary": {
            "history_sufficient_count": 8,
            "either_nonzero_count": 2,
        },
        "bucket_breakdown": {"core": {"rows": 2}},
        "samples": {
            "technical_any_active": [
                {"symbol": "005930"},
                {"symbol": "000660"},
                {"symbol": "035420"},
                {"symbol": "111111"},
            ]
        },
    }
    budget = {
        "meta": {"cycles_analyzed": 2},
        "flags": {"rate_limit_triggered": 1},
        "recommendations": ["trim"],
    }
    rows = [
        {
            "cycle_id": "c1",
            "selection_bucket": "core",
            "deep_evaluated": True,
            "final_candidate": False,
            "rejection_reason": "score_below_threshold",
            "symbol": "005930",
            "symbol_name": "Samsung",
            "score_deep": 70.0,
        },
        {
            "cycle_id": "c1",
            "selection_bucket": "rotating",
            "deep_evaluated": True,
            "final_candidate": True,
            "symbol": "000660",
            "symbol_name": "SK",
            "score_deep": 82.0,
        },
    ]

    with mock.patch.object(postrun_report, "run_candidate_analysis", return_value=candidate), mock.patch.object(
        postrun_report,
        "run_validation",
        return_value=validation,
    ), mock.patch.object(
        postrun_report,
        "run_technical_activation_analysis",
        return_value=technical,
    ), mock.patch.object(
        postrun_report,
        "run_budget_analysis",
        return_value=budget,
    ), mock.patch.object(
        postrun_report,
        "_log_path",
        return_value=Path("/tmp/fake.jsonl"),
    ), mock.patch.object(
        postrun_report,
        "_load_rows",
        return_value=(rows, ["warn"]),
    ), mock.patch.object(
        postrun_report,
        "_filter_session",
        side_effect=lambda loaded_rows, session: loaded_rows,
    ), mock.patch.object(
        postrun_report,
        "_filter_cycle_ids",
        side_effect=lambda loaded_rows, ids: [
            row for row in loaded_rows if row.get("cycle_id") in ids
        ],
    ):
        assert postrun_report.build_report("acct", "20260612", "REGULAR", 5) == {
            "meta": {
                "account": "acct",
                "date": "20260612",
                "session": "REGULAR",
                "last_n_cycles": 5,
                "selected_cycle_ids": ["c1"],
            },
            "funnel": {"total": 3, "stages": {"raw": 3, "deep_eval": 2}},
            "bad_patterns": {
                "counts": {"bad_a": 1, "bad_b": 0},
                "total": 1,
                "any_issues": True,
            },
            "rejection_distribution": {"score_below_threshold": 2, "other": 1},
            "bucket_final_exec": {
                "core": {
                    "total": 2,
                    "deep_eval": 2,
                    "final_candidate": 0,
                    "executed": 0,
                    "avg_score": 71.23,
                },
                "rotating": {
                    "total": 1,
                    "deep_eval": 1,
                    "final_candidate": 1,
                    "executed": 1,
                    "avg_score": 82.0,
                },
                "exploration": {
                    "total": 0,
                    "deep_eval": 0,
                    "final_candidate": 0,
                    "executed": 0,
                    "avg_score": 0.0,
                },
            },
            "core_bucket": {
                "core_loser_count": 1,
                "non_core_final_count": 1,
                "top_core_symbols": [
                    {"symbol": "005930", "symbol_name": "Samsung", "count": 1}
                ],
                "top_non_core_final_symbols": [
                    {"symbol": "000660", "symbol_name": "SK", "count": 1}
                ],
                "core_rejection_reasons": {"score_below_threshold": 1},
                "avg_core_loser_score_deep": 70.0,
                "avg_non_core_final_score_deep": 82.0,
                "human_summary": "top core rejection=score_below_threshold(1), avg score_deep 70.00 vs 82.00",
                "load_warnings": ["warn"],
            },
            "technical_activation": {
                "meta": {"analyzed_rows": 10},
                "summary": {
                    "history_sufficient_count": 8,
                    "either_nonzero_count": 2,
                },
                "bucket_breakdown": {"core": {"rows": 2}},
                "samples": [
                    {"symbol": "005930"},
                    {"symbol": "000660"},
                    {"symbol": "035420"},
                ],
            },
            "budget": {
                "meta": {"cycles_analyzed": 2},
                "flags": {"rate_limit_triggered": 1},
                "recommendations": ["trim"],
            },
            "sources": {
                "candidate_analysis": candidate,
                "validation": validation,
                "technical_activation_analysis": technical,
                "budget_analysis": budget,
            },
            "conclusion": {
                "core_stuck": True,
                "core_deep": 2,
                "core_final": 0,
                "core_exec": 0,
                "dominant_core_reason": "score_below_threshold(1)",
                "semantic_counts": {
                    "score_below_threshold": 1,
                    "passed_count_insufficient": 0,
                    "cost_filter_blocked": 0,
                    "profit_buffer_insufficient": 0,
                },
                "analyzed_rows": 10,
                "hist_rate": 0.8,
                "act_rate": 0.2,
                "cold_start_signal": False,
                "low_activation": False,
                "bottleneck": "score_threshold_bottleneck",
                "hint": "전략은 통과했지만 최종 score가 선택 임계값 미만입니다. analyze_core_bucket으로 score 성분 분포(보너스/패널티)를 확인하세요.",
                "next_steps": [
                    {
                        "label": "deep_eval → final_candidate 병목 상세",
                        "cmd": (
                            "python3 -m app.tools.analyze_selection_bottleneck"
                            " --date 20260612 --account acct --session REGULAR"
                        ),
                    },
                    {
                        "label": "core vs non-core score 성분 비교",
                        "cmd": (
                            "python3 -m app.tools.analyze_core_bucket"
                            " --date 20260612 --account acct --session REGULAR"
                        ),
                    },
                ],
            },
        }


def test_build_md_report_full_lines():
    class FrozenDateTime:
        @classmethod
        def now(cls):
            return cls()

        def strftime(self, fmt):
            return "2026-06-12 15:30:00"

    report = {
        "meta": {
            "date": "20260612",
            "account": "acct",
            "session": "REGULAR",
            "last_n_cycles": 5,
        },
        "funnel": {
            "total": 3,
            "stages": {
                "raw": 3,
                "deep_eval": 2,
                "final_candidate": 1,
                "executed": 1,
            },
        },
        "bad_patterns": {
            "counts": {"bad_a": 1, "residual_reason_mismatch": 2},
            "total": 3,
        },
        "rejection_distribution": {"score_below_threshold": 2},
        "bucket_final_exec": {
            "core": {
                "total": 2,
                "deep_eval": 2,
                "final_candidate": 0,
                "executed": 0,
                "avg_score": 71.23,
            }
        },
        "core_bucket": {
            "core_loser_count": 1,
            "non_core_final_count": 1,
            "avg_core_loser_score_deep": 70.0,
            "avg_non_core_final_score_deep": 82.0,
            "core_rejection_reasons": {"score_below_threshold": 1},
            "human_summary": "core summary",
        },
        "technical_activation": {
            "meta": {"analyzed_rows": 0},
            "summary": {},
            "bucket_breakdown": {},
        },
        "budget": {"meta": {"cycles_analyzed": 0}},
        "conclusion": {
            "bottleneck": "score_threshold_bottleneck",
            "hint": "score hint",
            "core_stuck": True,
            "core_deep": 2,
            "core_final": 0,
            "core_exec": 0,
            "dominant_core_reason": "score_below_threshold(1)",
            "analyzed_rows": 0,
            "next_steps": [{"label": "Inspect", "cmd": "python3 tool"}],
        },
    }

    with mock.patch.object(postrun_report, "datetime", FrozenDateTime):
        assert postrun_report.build_md_report(report).splitlines() == [
            "# postrun_diagnostics — 20260612 / acct",
            "",
            "- **session**: REGULAR",
            "- **last_n_cycles**: 5",
            "- **generated**: 2026-06-12 15:30:00",
            "",
            "## 1. Funnel Summary",
            "",
            "Total rows: **3**",
            "",
            "| Stage | Count |",
            "|---|---|",
            "| pre_gate_rejected | 0 |",
            "| shallow_ranked | 0 |",
            "| shallow_selected | 0 |",
            "| deep_eval | 2 |",
            "| final_candidate | 1 |",
            "| executed | 1 |",
            "",
            "## 2. Bad Pattern Counts",
            "",
            "Verdict: **🔴 issues** — total bad rows: 3",
            "",
            "| Pattern | Count | Note |",
            "|---|---|---|",
            "| `residual_reason_mismatch` | 2 | 구 로그 잔재 |",
            "| `bad_a` | 1 | ⚠ |",
            "",
            "## 3. Recent Rejection Reasons",
            "",
            "| Reason | Count |",
            "|---|---|",
            "| score_below_threshold | 2 |",
            "",
            "## 4. Bucket → Deep-Eval / Final-Candidate / Executed",
            "",
            "| Bucket | Total | Deep-Eval | Final-Cand | Executed | Avg Score |",
            "|---|---|---|---|---|---|",
            "| core | 2 | 2 | 0 | 0 | 71.23 |",
            "| rotating | 0 | 0 | 0 | 0 | 0.00 |",
            "| exploration | 0 | 0 | 0 | 0 | 0.00 |",
            "",
            "## 5. Core Bucket Deep-Eval Analysis",
            "",
            "- core_deep_eval_losers: **1**",
            "- non_core_final_candidates: **1**",
            "- avg score_deep — core losers: **70.00** / non-core finals: **82.00**",
            "",
            "**Core rejection breakdown:**",
            "",
            "| Reason | Count |",
            "|---|---|",
            "| score_below_threshold | 1 |",
            "| passed_count_insufficient | 0 |",
            "| cost_filter_blocked | 0 |",
            "| profit_buffer_insufficient | 0 |",
            "",
            "> core summary",
            "",
            "## 6. Technical Feature Activation",
            "",
            "_unavailable or no analyzable deep-eval rows_",
            "",
            "## 7. Budget / Rate-Limit Pressure",
            "",
            "_no snapshot data available_",
            "",
            "## 8. Diagnostic Conclusion",
            "",
            "**BOTTLENECK: `score_threshold_bottleneck`**",
            "",
            "> score hint",
            "",
            "- core: 🔴 STUCK — deep=2 final=0 exec=0",
            "- top core reason: `score_below_threshold(1)`",
            "",
            "**Next steps:**",
            "",
            "1. Inspect",
            "   ```",
            "   python3 tool",
            "   ```",
        ]
