"""Tests for app.tools.core_bucket_console (R5-S6)."""
from __future__ import annotations

import app.tools.analyze_core_bucket as A
import app.tools.core_bucket_console as C


# ── 1. Pin tests ──────────────────────────────────────────────────────────────

_MOVED_NAMES = [
    "_dim",
    "_format_metric_row",
    "_header",
    "_ok",
    "_print_rule_lift_opportunities",
    "_print_shadow_section",
    "_print_symbol_failure_diagnosis",
    "_print_threshold_simulation",
    "_warn",
    "print_terminal",
]


def test_pin_all_moved_names() -> None:
    for name in _MOVED_NAMES:
        assert getattr(A, name) is getattr(C, name), f"{name} not the same object"


def test_no_duplicate_top_level_console_defs() -> None:
    import ast
    from pathlib import Path

    source = Path(C.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    ]

    assert len(names) == len(set(names))


# ── 2. ANSI helpers exact-value tests ────────────────────────────────────────

def test_dim_exact() -> None:
    assert C._dim("X") == "\033[2mX\033[0m"


def test_warn_exact() -> None:
    assert C._warn("X") == "\033[93mX\033[0m"


def test_ok_exact() -> None:
    assert C._ok("X") == "\033[92mX\033[0m"


def test_header_exact() -> None:
    assert C._header("X") == "\033[1m\033[96mX\033[0m"


# ── 3. _format_metric_row exact ───────────────────────────────────────────────

def test_format_metric_row_exact() -> None:
    # label padded to 24 chars, avg/median right-aligned to 8 with 3 decimal places
    # "score_deep" is 10 chars -> padded to 24 -> "score_deep              "
    # left: avg=1.500, median=2.000; right: avg=0.250, median=0.125
    result = C._format_metric_row(
        "score_deep",
        {"avg": 1.5, "median": 2.0},
        {"avg": 0.25, "median": 0.125},
    )
    expected = "  score_deep                  1.500 /    2.000       0.250 /    0.125"
    assert result == expected


# ── 4. print_terminal error path ──────────────────────────────────────────────

def test_print_terminal_error_path(capsys: object) -> None:
    C.print_terminal({"error": "boom"})
    out = capsys.readouterr().out.splitlines()
    assert out == ["Error: boom"]


# ── 5. print_terminal full output ─────────────────────────────────────────────

_FULL_FIXTURE = {
    "candidate_file": "/path/to/candidate.jsonl",
    "cycle_snapshots_file": "/path/to/snapshots.jsonl",
    "session": "REGULAR",
    "total_rows": 5,
    "errors": [],
    "bucket_stats": {
        "alpha": {
            "total_rows": 3,
            "pre_gate_rejected": 1,
            "deep_eval": 2,
            "final_candidate": 1,
            "executed": 0,
            "score_deep": {
                "count": 2,
                "min": 1.5,
                "max": 3.5,
                "avg": 2.5,
                "median": 2.5,
            },
        },
        "beta": {
            "total_rows": 2,
            "pre_gate_rejected": 2,
            "deep_eval": 0,
            "final_candidate": 0,
            "executed": 0,
            "score_deep": {
                "count": 0,
                "min": 0.0,
                "max": 0.0,
                "avg": 0.0,
                "median": 0.0,
            },
        },
    },
    "core_losers_stats": {
        "count": 2,
        "top_symbols": [
            {"symbol": "000001", "symbol_name": "AlphaStock", "count": 2},
            {"symbol": "000002", "symbol_name": None, "count": 1},
        ],
        "reasons": {"passed_count_insufficient": 2},
        "outcomes": {"deep_eval_fail": 2},
        "samples": [
            {
                "symbol": "000001",
                "symbol_name": "AlphaStock",
                "cycle_id": "c1",
                "score_deep": 2.5,
                "passed_count": 2,
                "rejection_reason": "passed_count_insufficient",
                "technical_total_score": 0.8,
                "mean_reversion_bonus": 0.1,
                "expected_cost_penalty": -0.05,
            }
        ],
    },
    "non_core_final_stats": {
        "count": 1,
        "top_symbols": [
            {"symbol": "000003", "symbol_name": "GammaStock", "count": 1},
        ],
        "buckets": {"rotating": 1},
        "samples": [
            {
                "symbol": "000003",
                "symbol_name": "GammaStock",
                "cycle_id": "c2",
                "score_deep": 3.2,
                "passed_count": 3,
                "technical_total_score": 1.2,
                "mean_reversion_bonus": 0.2,
                "expected_cost_penalty": -0.02,
            }
        ],
    },
    "composition_comparison": {
        "core_deep_eval_losers": {
            "detail_coverage_count": 2,
            "row_count": 2,
            "detail_coverage_pct": "100.0%",
            "technical_nonzero_count": 2,
            "technical_available_count": 2,
            "mean_reversion_nonzero_count": 1,
            "cost_block_reasons": {"insufficient_buffer": 1},
            "top_highlights": {"trend_alignment": 2},
            "top_penalties": {"overextension": 1},
            "score_deep": {"avg": 2.5, "median": 2.5},
            "passed_count": {"avg": 2.0, "median": 2.0},
            "technical_total_score": {"avg": 0.8, "median": 0.8},
            "trend_alignment_score": {"avg": 0.6, "median": 0.6},
            "macd_momentum_score": {"avg": 0.4, "median": 0.4},
            "mean_reversion_bonus": {"avg": 0.1, "median": 0.1},
            "overextension_penalty": {"avg": -0.2, "median": -0.2},
            "net_profit_buffer_bps": {"avg": 10.0, "median": 10.0},
            "expected_cost_bps": {"avg": 5.0, "median": 5.0},
            "expected_cost_penalty": {"avg": -0.05, "median": -0.05},
            "cost_quality_score": {"avg": 0.7, "median": 0.7},
        },
        "non_core_final_candidates": {
            "detail_coverage_count": 1,
            "row_count": 1,
            "detail_coverage_pct": "100.0%",
            "technical_nonzero_count": 1,
            "technical_available_count": 1,
            "mean_reversion_nonzero_count": 1,
            "cost_block_reasons": {},
            "top_highlights": {"macd": 1},
            "top_penalties": {},
            "score_deep": {"avg": 3.2, "median": 3.2},
            "passed_count": {"avg": 3.0, "median": 3.0},
            "technical_total_score": {"avg": 1.2, "median": 1.2},
            "trend_alignment_score": {"avg": 0.9, "median": 0.9},
            "macd_momentum_score": {"avg": 0.7, "median": 0.7},
            "mean_reversion_bonus": {"avg": 0.2, "median": 0.2},
            "overextension_penalty": {"avg": 0.0, "median": 0.0},
            "net_profit_buffer_bps": {"avg": 15.0, "median": 15.0},
            "expected_cost_bps": {"avg": 4.0, "median": 4.0},
            "expected_cost_penalty": {"avg": -0.02, "median": -0.02},
            "cost_quality_score": {"avg": 0.9, "median": 0.9},
        },
    },
    "human_summary": "Core bucket deep-eval losers show lower passed_count.",
    "strategy_hit_rate": {
        "core_deep_eval_losers": {"parsed_rows": 0},
        "non_core_final_candidates": {"parsed_rows": 0},
    },
    "passed_count_distribution": {1: 2, 2: 3},
    "rule_contrast_interpretation": {
        "inferred_required_pass_count": 2,
        "verdict": "threshold_only",
        "explanation": "Core rows fail by threshold alone.",
        "structural_gap_rules": [],
        "threshold_candidate_rules": [],
        "shared_rules": [],
        "dominant_fail_pattern": "F F P F F",
        "dominant_fail_pattern_count": 3,
        "dominant_passing_rules": ["intraday_pullback"],
        "gap_to_threshold": 1,
    },
    "top_fail_patterns": [
        {
            "pattern": "F F P F F",
            "count": 3,
            "passing_rules": ["intraday_pullback"],
            "pass_count": 1,
        }
    ],
    "threshold_simulation": {},
    "rule_lift_opportunities": {},
    "symbol_failure_diagnosis": {},
    "shadow_analysis": {},
}

_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_YELLOW = "\033[93m"
_GREEN = "\033[92m"
_DIM_ESC = "\033[2m"

_FULL_EXPECTED = [
    f"{_BOLD}{_CYAN}{'═' * 76}{_RESET}",
    f"{_BOLD}{_CYAN}  Analyze Core Bucket Diagnostics{_RESET}",
    f"{_BOLD}{_CYAN}{'═' * 76}{_RESET}",
    "Candidate log : /path/to/candidate.jsonl",
    "Cycle snapshots: /path/to/snapshots.jsonl",
    "Session filter : REGULAR | Total rows loaded: 5",
    "",
    f"{_BOLD}{_CYAN}1. Overall Bucket Comparison{_RESET}",
    "  [alpha]",
    "    Total Rows   : 3",
    "    Pre-Gate Rej : 1",
    "    Deep Eval    : 2",
    "    Final Cand   : 1",
    "    Executed     : 0",
    "    Deep Scores  : 2 valid",
    "      min/max    : 1.5000 / 3.5000",
    "      avg/median : 2.5000 / 2.5000",
    "",
    "  [beta]",
    "    Total Rows   : 2",
    "    Pre-Gate Rej : 2",
    "    Deep Eval    : 0",
    "    Final Cand   : 0",
    "    Executed     : 0",
    "    Deep Scores  : NONE valid",
    "",
    f"{_BOLD}{_CYAN}2. Core Deep-Eval Losers vs Non-Core Final Candidates{_RESET}",
    "  Core deep-eval losers      : 2",
    "  Non-core final candidates  : 1",
    "",
    "  Top Core Loser Symbols:",
    "    000001 (AlphaStock) x 2",
    "    000002 (-) x 1",
    "  Top Non-Core Final Symbols:",
    "    000003 (GammaStock) x 1",
    "",
    "  Core Rejection Reasons:",
    "    passed_count_insufficient: 2",
    "  Core Selection Outcomes:",
    "    deep_eval_fail: 2",
    "  Non-Core Final Buckets:",
    "    rotating: 1",
    "",
    f"{_BOLD}{_CYAN}3. Score Composition Comparison{_RESET}",
    "  Detail coverage            2/2 (100.0%)    1/1 (100.0%)",
    "  Metric                    CORE losers avg/med      NON-CORE finals avg/med",
    f"{_DIM_ESC}  {'─' * 72}{_RESET}",
    "  score_deep                  2.500 /    2.500       3.200 /    3.200",
    "  passed_count                2.000 /    2.000       3.000 /    3.000",
    "  technical_total             0.800 /    0.800       1.200 /    1.200",
    "  trend_alignment             0.600 /    0.600       0.900 /    0.900",
    "  macd_momentum               0.400 /    0.400       0.700 /    0.700",
    "  mean_reversion_bonus        0.100 /    0.100       0.200 /    0.200",
    "  overextension_penalty      -0.200 /   -0.200       0.000 /    0.000",
    "  net_profit_buffer_bps      10.000 /   10.000      15.000 /   15.000",
    "  expected_cost_bps           5.000 /    5.000       4.000 /    4.000",
    "  expected_cost_penalty      -0.050 /   -0.050      -0.020 /   -0.020",
    "  cost_quality_score          0.700 /    0.700       0.900 /    0.900",
    "",
    "  Technical nonzero rows     2 / 2    1 / 1",
    "  Mean-reversion nonzero     1 / 2    1 / 1",
    "  Core cost block reasons    : {'insufficient_buffer': 1}",
    "  Non-core cost block reasons: {}",
    "  Core top highlights        : {'trend_alignment': 2}",
    "  Non-core top highlights    : {'macd': 1}",
    "  Core top penalties         : {'overextension': 1}",
    "  Non-core top penalties     : {}",
    "",
    f"{_BOLD}{_CYAN}4. Sample Rows{_RESET}",
    "  Core loser samples:",
    "    000001(AlphaStock) | cycle=c1 | score=2.5 | passed=2 | reason=passed_count_insufficient | tech=0.8 | mean_rev=0.1 | cost_penalty=-0.05",
    "  Non-core final samples:",
    "    000003(GammaStock) | cycle=c2 | score=3.2 | passed=3 | tech=1.2 | mean_rev=0.2 | cost_penalty=-0.02",
    "",
    f"{_BOLD}{_CYAN}5. Human Summary{_RESET}",
    "  Core bucket deep-eval losers show lower passed_count.",
    "",
    f"{_BOLD}{_CYAN}6. Strategy Hit Rate (strategy_pass_pattern){_RESET}",
    "  Parsed rows: core=0 | non-core=0",
    f"{_DIM_ESC}  strategy_pass_pattern not present in data (field added recently){_RESET}",
    "",
    f"{_BOLD}{_CYAN}7. passed_count Distribution (core deep-eval losers){_RESET}",
    "  inferred required_pass_count = 2",
    f"  passed_count= 1     2   40.0%  ██{_YELLOW} ✗ below threshold{_RESET}",
    f"  passed_count= 2     3   60.0%  ███{_GREEN} ✓ passes threshold{_RESET}",
    "",
    f"{_BOLD}{_CYAN}8. Top Fail-Pattern Combinations (core deep-eval losers){_RESET}",
    "  pattern       count  passing rules",
    f"{_DIM_ESC}  {'─' * 62}{_RESET}",
    "  F F P F F         3  [1 pass]  intraday_pullback",
    "",
    f"{_BOLD}{_CYAN}9. Rule-Contrast Interpretation{_RESET}",
    f"  Verdict       : {_GREEN}threshold_only{_RESET}",
    "  Explanation   : Core rows fail by threshold alone.",
    "  Dominant pattern: \"F F P F F\"  x3  → passing: ['intraday_pullback']",
    "  Gap to threshold: 1 more rule(s) needed beyond dominant pattern",
    "",
    f"{_BOLD}{_CYAN}10. Threshold Simulation{_RESET}",
    f"{_DIM_ESC}  required_pass_count could not be inferred from current rows.{_RESET}",
    "",
    f"{_BOLD}{_CYAN}11. One-More-Rule Opportunities{_RESET}",
    f"{_DIM_ESC}  rule-lift opportunities unavailable without inferred threshold.{_RESET}",
    "",
    f"{_BOLD}{_CYAN}12. Per-Symbol Failure Diagnosis{_RESET}",
    f"{_DIM_ESC}  symbol diagnosis unavailable without inferred threshold.{_RESET}",
    "",
    f"{_BOLD}{_CYAN}13. Shadow Evaluation Results (core_shadow_*){_RESET}",
    f"{_DIM_ESC}  No core_shadow_evaluated=True rows found.",
    f"  Shadow fields are written from the next live session onward.{_RESET}",
    "",
]


def test_print_terminal_full_output(capsys: object) -> None:
    C.print_terminal(_FULL_FIXTURE)
    out = capsys.readouterr().out.splitlines()
    assert out == _FULL_EXPECTED


# ── 6. _print_threshold_simulation direct tests ───────────────────────────────

def test_print_threshold_simulation_unavailable(capsys: object) -> None:
    C._print_threshold_simulation({})
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}10. Threshold Simulation{_RESET}",
        f"{_DIM_ESC}  required_pass_count could not be inferred from current rows.{_RESET}",
        "",
    ]


def test_print_threshold_simulation_available(capsys: object) -> None:
    payload = {
        "available": True,
        "current_required_pass_count": 3,
        "deep_eval_row_count": 100,
        "current_hits": 20,
        "scenarios": [
            {
                "threshold": 2,
                "would_meet_threshold_count": 25,
                "delta_vs_current_threshold": 5,
                "flipped_row_count": 3,
                "flipped_symbols": {"AAA": 2, "BBB": 1},
                "flipped_reasons": {"score_gap": 3},
            },
            {
                "threshold": 4,
                "would_meet_threshold_count": 10,
                "delta_vs_current_threshold": -10,
                "flipped_row_count": 0,
                "flipped_symbols": {},
                "flipped_reasons": {},
            },
        ],
    }
    C._print_threshold_simulation(payload)
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}10. Threshold Simulation{_RESET}",
        "  Current threshold : 3",
        "  Deep-eval rows    : 100",
        "  Current hits      : 20",
        "",
        "  threshold=2 -> meets=25 | delta=+5 | flipped=3",
        "    flipped symbols : {'AAA': 2, 'BBB': 1}",
        "    flipped reasons : {'score_gap': 3}",
        "  threshold=4 -> meets=10 | delta=-10 | flipped=0",
        "",
    ]


# ── 7. _print_rule_lift_opportunities direct tests ────────────────────────────

def test_print_rule_lift_unavailable(capsys: object) -> None:
    C._print_rule_lift_opportunities({})
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}11. One-More-Rule Opportunities{_RESET}",
        f"{_DIM_ESC}  rule-lift opportunities unavailable without inferred threshold.{_RESET}",
        "",
    ]


def test_print_rule_lift_empty_ranked(capsys: object) -> None:
    payload = {
        "available": True,
        "near_threshold_row_count": 7,
        "multi_rule_short_row_count": 3,
        "ranked_rules": [],
    }
    C._print_rule_lift_opportunities(payload)
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}11. One-More-Rule Opportunities{_RESET}",
        "  Near-threshold rows : 7 | multi-rule short rows : 3",
        f"{_DIM_ESC}  No rows are exactly one rule short today.{_RESET}",
        "",
    ]


def test_print_rule_lift_available(capsys: object) -> None:
    payload = {
        "available": True,
        "near_threshold_row_count": 4,
        "multi_rule_short_row_count": 1,
        "ranked_rules": [
            {
                "rule": "momentum_gate",
                "near_threshold_row_count": 4,
                "symbols": ["A001", "B002"],
                "non_core_final_pass_rate": 0.6,
            }
        ],
    }
    C._print_rule_lift_opportunities(payload)
    out = capsys.readouterr().out.splitlines()
    # rule padded to 24: "momentum_gate           "
    # near_threshold_row_count left-aligned to 3: "4  "
    # pct = 0.6*100 = 60.0 → "60%"
    assert out == [
        f"{_BOLD}{_CYAN}11. One-More-Rule Opportunities{_RESET}",
        "  Near-threshold rows : 4 | multi-rule short rows : 1",
        "  momentum_gate            near-miss rows=4   symbols=['A001', 'B002'] | non-core final pass=60%",
        "",
    ]


# ── 8. _print_symbol_failure_diagnosis direct tests ──────────────────────────

def test_print_symbol_failure_unavailable(capsys: object) -> None:
    C._print_symbol_failure_diagnosis({})
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}12. Per-Symbol Failure Diagnosis{_RESET}",
        f"{_DIM_ESC}  symbol diagnosis unavailable without inferred threshold.{_RESET}",
        "",
    ]


def test_print_symbol_failure_available(capsys: object) -> None:
    payload = {
        "available": True,
        "symbols": [
            {
                "symbol": "A001",
                "symbol_name": "TestCo",
                "verdict": "near_miss",
                "rows": 5,
                "avg_passed_count_deep": 2.75,
                "best_score_deep": 3.14,
                "one_rule_short_count": 2,
                "top_missing_rules": [{"rule": "volume_gate"}],
                "action_hint": "Watch volume_gate rule",
            }
        ],
    }
    C._print_symbol_failure_diagnosis(payload)
    out = capsys.readouterr().out.splitlines()
    # avg_passed_count_deep: ":.2f" → "2.75"
    # best_score_deep: ":.2f" → "3.14"
    assert out == [
        f"{_BOLD}{_CYAN}12. Per-Symbol Failure Diagnosis{_RESET}",
        "  A001(TestCo) | near_miss | rows=5 | avg passed=2.75 | best score=3.14 | one-rule short=2",
        "    top missing rules: volume_gate",
        "    hint: Watch volume_gate rule",
        "",
    ]


# ── 9. _print_shadow_section direct tests ────────────────────────────────────

def test_print_shadow_unavailable(capsys: object) -> None:
    C._print_shadow_section({})
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_BOLD}{_CYAN}13. Shadow Evaluation Results (core_shadow_*){_RESET}",
        f"{_DIM_ESC}  No core_shadow_evaluated=True rows found.",
        f"  Shadow fields are written from the next live session onward.{_RESET}",
        "",
    ]


def test_print_shadow_available(capsys: object) -> None:
    sa = {
        "available": True,
        "evaluated_count": 10,
        "trend_gate_passed_count": 8,
        "trend_gate_blocked_count": 2,
        "shadow_passed_count": 5,
        "shadow_pass_rate_pct": "62.5%",
        "top_patterns": [
            {
                "pattern": "P P P",
                "count": 3,
                "passing_rules": ["rule_a", "rule_b", "rule_c"],
                "pass_count": 3,
            },
            {
                "pattern": "P P F",
                "count": 2,
                "passing_rules": ["rule_a", "rule_b"],
                "pass_count": 1,
            },
        ],
        "shadow_pass_existing_rejection_reasons": {"score_low": 2},
        "shadow_pass_existing_pcount": {2: 3, 3: 2},
    }
    C._print_shadow_section(sa)
    out = capsys.readouterr().out.splitlines()
    # pattern col: :<12, count col: >6, pass_count/marker, rules
    # "P P P      " (12 chars), "     3", "[3/3]", ok(" ✓"), "rule_a, rule_b, rule_c"
    # "P P F      " (12 chars), "     2", "[1/3]", warn(" ✗"), "rule_a, rule_b"
    # reasons sorted by -count: {"score_low": 2} → one entry
    # pcount sorted by key: 2, 3
    assert out == [
        f"{_BOLD}{_CYAN}13. Shadow Evaluation Results (core_shadow_*){_RESET}",
        "  Core deep-eval rows with shadow data : 10",
        "  Trend pre-gate (score >= 0.25)       : 8 passed, 2 blocked",
        "  Shadow passed (>= 2/3 rules)         : 5 of 8 trend-gate-passed  (62.5%)",
        "",
        "  Shadow pattern distribution (trend-gate-passed rows):",
        "  pattern       count  passing rules",
        f"{_DIM_ESC}  {'─' * 58}{_RESET}",
        f"  P P P             3  [3/3]{_GREEN} ✓{_RESET}  rule_a, rule_b, rule_c",
        f"  P P F             2  [1/3]{_YELLOW} ✗{_RESET}  rule_a, rule_b",
        "",
        "  Among shadow-passed rows — existing rejection reasons:",
        "    score_low                            2",
        "",
        "  Among shadow-passed rows — existing passed_count_deep:",
        "    passed_count=2: 3",
        "    passed_count=3: 2",
        "",
    ]
