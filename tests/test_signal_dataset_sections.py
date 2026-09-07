"""Tests for signal_dataset_sections.py (R5-S5)."""
from __future__ import annotations


_MOVED = [
    "_h", "_ok", "_warn", "_bad", "_dim",
    "_fval", "_bool_val", "_fvals",
    "_median", "_mean", "_pct", "_fmt", "_delta_fmt",
    "_minute_of_day", "_time_bucket_label",
    "_bucket_order", "_bucket_code", "_bucket_count_text",
    "_short_bucket_counts", "_cycle_shortlist_cutoffs",
    "_section_bucket", "_section_outcome", "_section_rejection",
    "_section_time_of_day", "_section_time_bucket_crosstab",
    "_section_trade_budget_limited", "_section_bottleneck_summary",
    "_section_core_rescue", "_section_core_shadow",
]


def test_pin_all_29():
    import app.tools.analyze_signal_dataset as orig
    import app.tools.signal_dataset_sections as sds

    for name in _MOVED:
        assert hasattr(orig, name), f"analyze_signal_dataset missing {name}"
        assert hasattr(sds, name), f"signal_dataset_sections missing {name}"
        assert getattr(orig, name) is getattr(sds, name), (
            f"{name}: analyze_signal_dataset.{name} is not signal_dataset_sections.{name}"
        )


def test_minute_of_day_none():
    from app.tools.signal_dataset_sections import _minute_of_day
    assert _minute_of_day(None) is None


def test_minute_of_day_iso():
    from app.tools.signal_dataset_sections import _minute_of_day
    assert _minute_of_day("2026-04-07T09:30:00") == 570


def test_pct_basic():
    from app.tools.signal_dataset_sections import _pct
    assert _pct(1, 4) == " 25%"


def test_h_wraps_with_ansi():
    from app.tools.signal_dataset_sections import _h
    result = _h("hello")
    assert result == "\033[1m\033[96mhello\033[0m"


def test_median_odd():
    from app.tools.signal_dataset_sections import _median
    assert _median([1.0, 3.0, 5.0]) == 3.0


def test_mean_basic():
    from app.tools.signal_dataset_sections import _mean
    assert _mean([2.0, 4.0]) == 3.0


def test_fval_none_value():
    from app.tools.signal_dataset_sections import _fval
    assert _fval({"x": None}, "x") is None


def test_fval_empty_string():
    from app.tools.signal_dataset_sections import _fval
    assert _fval({"x": ""}, "x") is None


def test_bool_val_true_string():
    from app.tools.signal_dataset_sections import _bool_val
    assert _bool_val({"v": "true"}, "v") is True


def test_fvals_returns_floats():
    from app.tools.signal_dataset_sections import _fvals
    rows = [{"x": "1.0"}, {"x": None}, {"x": "3.0"}]
    assert _fvals(rows, "x") == [1.0, 3.0]


def test_fmt_none():
    from app.tools.signal_dataset_sections import _fmt
    assert _fmt(None) == "   —"


def test_delta_fmt_positive():
    from app.tools.signal_dataset_sections import _delta_fmt
    # d=0.5 > 0.05 → _ok (green) wrapping applied; assert full literal string
    assert _delta_fmt(1.0, 0.5) == "\033[92m  +0.50\033[0m"


def test_ok_wraps_with_green_ansi():
    from app.tools.signal_dataset_sections import _ok
    assert _ok("good") == "\033[92mgood\033[0m"


def test_warn_wraps_with_yellow_ansi():
    from app.tools.signal_dataset_sections import _warn
    assert _warn("meh") == "\033[93mmeh\033[0m"


def test_bad_wraps_with_red_ansi():
    from app.tools.signal_dataset_sections import _bad
    assert _bad("err") == "\033[91merr\033[0m"


def test_dim_wraps_with_dim_ansi():
    from app.tools.signal_dataset_sections import _dim
    assert _dim("low") == "\033[2mlow\033[0m"


def test_time_bucket_label_first():
    from app.tools.signal_dataset_sections import _time_bucket_label
    assert _time_bucket_label("2026-04-07T09:30:00") == "09:00–10:00"


def test_bucket_order_preferred():
    from app.tools.signal_dataset_sections import _bucket_order
    rows = [{"selection_bucket": "exploration"}, {"selection_bucket": "core"}]
    result = _bucket_order(rows)
    assert result == ["core", "exploration"]


def test_bucket_code_core():
    from app.tools.signal_dataset_sections import _bucket_code
    assert _bucket_code("core") == "C"


def test_bucket_code_rotating():
    from app.tools.signal_dataset_sections import _bucket_code
    assert _bucket_code("rotating") == "R"


def test_bucket_code_exploration():
    from app.tools.signal_dataset_sections import _bucket_code
    assert _bucket_code("exploration") == "E"


def test_bucket_code_fallback():
    from app.tools.signal_dataset_sections import _bucket_code
    assert _bucket_code("zzz") == "Z"


def test_bucket_count_text_basic():
    from app.tools.signal_dataset_sections import _bucket_count_text
    rows = [{"selection_bucket": "core"}, {"selection_bucket": "core"}, {"selection_bucket": "rotating"}]
    result = _bucket_count_text(rows, ["core", "rotating"], lambda r: True)
    assert result == " 2/ 1"


def test_short_bucket_counts_basic():
    from app.tools.signal_dataset_sections import _short_bucket_counts
    rows = [{"selection_bucket": "core"}, {"selection_bucket": "core"}, {"selection_bucket": "rotating"}]
    result = _short_bucket_counts(rows, ["core", "rotating"])
    assert result == "C:2 / R:1"


def test_cycle_shortlist_cutoffs_basic():
    from app.tools.signal_dataset_sections import _cycle_shortlist_cutoffs
    rows = [
        {"cycle_id": "c1", "score_shallow": "0.8", "shallow_selected": "true"},
        {"cycle_id": "c1", "score_shallow": "0.6", "shallow_selected": "true"},
    ]
    result = _cycle_shortlist_cutoffs(rows)
    assert result["c1"] == 0.6



def test_feature_short_keys_match_feature_cols():
    """_FEATURE_SHORT must exist and its keys must match _FEATURE_COLS exactly."""
    import app.tools.signal_dataset_sections as sds
    assert hasattr(sds, "_FEATURE_SHORT"), "_FEATURE_SHORT missing from signal_dataset_sections"
    assert set(sds._FEATURE_SHORT.keys()) == set(sds._FEATURE_COLS)


def test_section_core_shadow_with_rows(capsys):
    import app.tools.signal_dataset_sections as sds
    rows = [{
        "core_shadow_evaluated": "true",
        "core_shadow_trend_gate_passed": "true",
        "core_shadow_passed": "false",
        "final_candidate": "false",
        "core_shadow_pattern": "PPF",
    }]
    sds._section_core_shadow(rows)
    out = capsys.readouterr().out
    _h = sds._h; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  9. Core shadow analysis")
    assert "core_shadow evaluated" in out
    assert "trend gate passed" in out
    assert "shadow passed" in out
    assert "final_candidate" in out
    assert "pattern" in out
    assert "PPF" in out


def test_section_core_shadow_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_core_shadow([])
    out = capsys.readouterr().out
    _h = sds._h; _dim = sds._dim; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  9. Core shadow fields")
    assert lines[2] == _h("─" * W)
    assert _dim("  No core_shadow_evaluated=True rows in this dataset.") in out
    assert "(Fields will populate" in out
    assert lines[-1] == ""


def test_section_core_rescue_with_rows(capsys):
    import app.tools.signal_dataset_sections as sds
    rows = [{
        "core_rescue_applied": "true",
        "selection_bucket": "core",
        "deep_evaluated": "true",
        "final_candidate": "false",
        "executed": "false",
        "score_shallow": "0.5",
        "score_deep": "0.6",
        "passed_count_deep": "4",
        "core_rescue_reason": "shallow_miss",
        "ts": "2026-04-07T09:30:00",
    }]
    sds._section_core_rescue(rows)
    out = capsys.readouterr().out
    assert "rescued rows" in out
    assert "buckets" in out
    assert "funnel" in out
    assert "medians" in out


def test_section_core_rescue_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_core_rescue([])
    out = capsys.readouterr().out
    _h = sds._h; _dim = sds._dim; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  8. Core rescue analysis")
    assert lines[2] == _h("─" * W)
    assert _dim("  No core_rescue_applied=True rows in this dataset.") in out
    assert "--all-stages" in out
    assert lines[-1] == ""


def test_section_bottleneck_summary_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_bottleneck_summary([])
    out = capsys.readouterr().out
    _h = sds._h; _dim = sds._dim; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  7. Bottleneck summary")
    assert lines[2] == _h("─" * W)
    assert "overview" in lines[3]
    assert "A blocked finals" in out
    assert "B core shortlist no-deep" in out
    assert "C core shallow misses" in out
    assert _dim("    No final_candidate + trade_budget_limited rows in this dataset.") in out
    assert _dim("    No core shortlist rows stalled before deep_eval in this dataset.") in out
    assert _dim("    No core shallow-ranked misses in this dataset.") in out
    assert lines[-1] == ""


def test_section_bottleneck_summary_exact_output(capsys):
    """Full line-list equality for _section_bottleneck_summary with non-empty rows.

    Fixture exercises: final_budget (A), other_final (A comparison),
    core_shortlisted_not_deep (B), and no core_shallow_miss (C dim fallback).
    Expected lines are hand-derived by tracing the implementation.
    """
    import app.tools.signal_dataset_sections as sds
    rows = [
        {
            "selection_bucket": "core",
            "final_candidate": "true",
            "rejection_reason": "trade_budget_limited",
            "score_deep": "0.80",
            "passed_count_deep": "5",
            "pullback_pct": "0.03",
            "shallow_selected": "true",
            "deep_evaluated": "true",
        },
        {
            "selection_bucket": "rotating",
            "final_candidate": "true",
            "rejection_reason": "",
            "score_deep": "0.60",
            "passed_count_deep": "4",
            "pullback_pct": "0.02",
            "shallow_selected": "true",
            "deep_evaluated": "true",
        },
        {
            "selection_bucket": "core",
            "final_candidate": "false",
            "shallow_selected": "true",
            "deep_evaluated": "false",
            "stage_reached": "shallow_selected",
        },
    ]
    sds._section_bottleneck_summary(rows)
    out = capsys.readouterr().out
    W = 72
    expected = [
        "\033[1m\033[96m" + "─" * W + "\033[0m",
        "\033[1m\033[96m  7. Bottleneck summary\033[0m",
        "\033[1m\033[96m" + "─" * W + "\033[0m",
        "  overview",
        "    A blocked finals          : 1",
        "    B core shortlist no-deep : 1",
        "    C core shallow misses     : 0",
        "",
        "  A. execution-budget blocked finals",
        "    count=1 | buckets=C:1 / R:0",
        "    medians | score=0.80 | passed=5.00 | pullback=0.03",
        "    vs other final | score Δ\033[92m  +0.20\033[0m | passed Δ\033[92m  +1.00\033[0m | pullback Δ\033[2m  +0.01\033[0m",
        "",
        "  B. core shortlisted-but-not-deep-evaluated",
        "    count=1",
        "    outcomes=?:1",
        "    stages=shallow_selected:1",
        "\033[2m    Exported dataset does not carry API/rate-limit cause flags for these rows.\033[0m",
        "",
        "  C. core shallow-score misses",
        "    count=0",
        "\033[2m    No core shallow-ranked misses in this dataset.\033[0m",
        "",
    ]
    assert out.splitlines() == expected


def test_section_trade_budget_limited_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_trade_budget_limited([])
    out = capsys.readouterr().out
    _h = sds._h; _dim = sds._dim; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  6. trade_budget_limited analysis")
    assert lines[2] == _h("─" * W)
    assert "budget_limited rows" in lines[3]
    assert _dim("  No trade_budget_limited rows in this dataset.") in out
    assert lines[-1] == ""


def test_section_time_bucket_crosstab_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_time_bucket_crosstab([])
    out = capsys.readouterr().out
    _h = sds._h; W = 72
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  5. Time x bucket x outcome/rejection")
    assert lines[2] == _h("─" * W)
    assert "bucket legend:" in lines[3]
    assert "time" in lines[4] and "rows" in lines[4]
    assert lines[-1] == ""


def test_section_time_of_day_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_time_of_day([])
    out = capsys.readouterr().out
    W = 72; _h = sds._h
    lines = out.splitlines()
    assert lines[0] == _h("─" * W)
    assert lines[1] == _h("  4. Time-of-day analysis")
    assert lines[2] == _h("─" * W)
    # column header must contain these labels
    assert "time" in lines[3] and "rows" in lines[3] and "deep" in lines[3]
    # separator
    assert all(c in "- " for c in lines[4].strip())
    # no data rows for empty input
    assert lines[-1] == ""


def test_section_rejection_empty(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_rejection([])
    out = capsys.readouterr().out
    W = 72; _h = sds._h
    short_features = ["score", "passed", "trend_aln", "macd_mom", "pullback"]
    hdr_line = (
        f"  {'reason':<32} {'n':>5} {'pct':>4} "
        + "  ".join(f"{c:>9}" for c in short_features)
        + "  (median)"
    )
    sep_line = f"  {'-'*32} {'-'*5} {'-'*4} " + "  ".join("-" * 9 for _ in short_features)
    expected = "\n".join([
        _h("─" * W),
        _h("  3. Rejection breakdown (deep_eval rows only)"),
        _h("─" * W),
        hdr_line,
        sep_line,
        "",
    ]) + "\n"
    assert out == expected


def test_section_outcome_empty_exact(capsys):
    import app.tools.signal_dataset_sections as sds
    sds._section_outcome([])
    out = capsys.readouterr().out
    W = 72; _h = sds._h; _dim = sds._dim; _fmt = sds._fmt
    col_w = 26
    hdr = f"  {'feature':<{col_w}}  {'final_candidate':>22}(n=0)  {'deep_eval_rejected':>22}(n=0)  {'not_selected_after':>22}(n=0)"
    sep = f"  {'-'*col_w}  {'-'*27}  {'-'*27}  {'-'*27}"
    # For all 7 feature cols with empty rows, all medians are None → "   —"
    # delta_fmt(None, None) = "    —"
    none_fmt = _fmt(None)  # "   —"
    f1 = f"  {'score_deep':<{col_w}}  {none_fmt:>27}  {none_fmt:>18} Δ    —  {none_fmt:>18} Δ    —"
    expected = "\n".join([
        _h("─" * W), _h("  2. final_candidate vs deep_eval_rejected"), _h("─" * W),
        hdr, sep, f1,
    ])
    # Just check first lines are correct
    actual_lines = out.splitlines()
    assert actual_lines[0] == _h("─" * W)
    assert actual_lines[1] == _h("  2. final_candidate vs deep_eval_rejected")
    assert actual_lines[2] == _h("─" * W)
    # feature header row
    assert "feature" in actual_lines[3]
    assert "(n=0)" in actual_lines[3]
    # separator row
    assert all(c in "- " for c in actual_lines[4].strip())
    # score_deep feature row
    assert "score_deep" in actual_lines[5]
    assert none_fmt.strip() in actual_lines[5]
    # last non-empty line is Δ explanation
    assert "Δ = final_candidate median" in out
    # trailing blank line
    assert out.endswith("\n\n")


def test_section_bucket_exact_output(capsys):
    import app.tools.signal_dataset_sections as sds
    rows = [{
        "selection_bucket": "core",
        "deep_evaluated": "true",
        "final_candidate": "true",
        "score_deep": "0.75",
        "passed_count_deep": "5",
        "trend_alignment_score": "0.60",
        "macd_momentum_score": "0.50",
        "pullback_pct": "0.03",
        "gap_up_open_pct": "0.02",
        "range_recovery_ratio": "0.80",
    }]
    sds._section_bucket(rows)
    out = capsys.readouterr().out
    _h = sds._h
    _ok = sds._ok
    _dim = sds._dim
    _fmt = sds._fmt
    W = 72
    hdr_line = (
        f"  {'bucket':<14} {'rows':>5} {'deep':>5} {'final':>6} "
        + "  ".join(f"{c:>9}" for c in ["score", "passed", "trend_aln", "macd_mom", "pullback", "gap_up", "range_rec"])
    )
    sep_line = f"  {'-'*14} {'-'*5} {'-'*5} {'-'*6} " + "  ".join("-" * 9 for _ in range(7))
    data_line = (
        f"  {'core':<14} {1:>5} {1:>5} {_ok(f'{1:>6}')} "
        + "  ".join(_fmt(v) for v in [0.75, 5.0, 0.60, 0.50, 0.03, 0.02, 0.80])
    )
    expected = "\n".join([
        _h("─" * W),
        _h("  1. Bucket comparison"),
        _h("─" * W),
        hdr_line,
        sep_line,
        data_line,
        "",
    ]) + "\n"
    assert out == expected
