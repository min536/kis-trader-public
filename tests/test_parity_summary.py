"""Tests for backtester.engine_backtest.parity_summary (R7-B1)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Step 1: Pin 17 symbols — assertIs check (parity re-exports same object)
# ---------------------------------------------------------------------------

PINNED_NAMES = [
    "_normalize_date",
    "_parse_ts",
    "_bool",
    "_int",
    "_float",
    "_sum_count_dicts",
    "_collect_unique_list_values",
    "_last_dict_value",
    "_max_count_entry",
    "_build_rule_failure_summary",
    "_build_buy_diagnostics",
    "_enrich_engine_summary",
    "_unique_symbols",
    "_summarize_live_reference",
    "_summarize_engine_report",
    "_overlap",
    "_classify_parity",
]


def test_pin_symbols_assertIs():
    import backtester.engine_backtest.parity as parity
    import backtester.engine_backtest.parity_summary as parity_summary

    for name in PINNED_NAMES:
        obj_parity = getattr(parity, name)
        obj_summary = getattr(parity_summary, name)
        assert obj_parity is obj_summary, (
            f"parity.{name} is not parity_summary.{name} — facade re-export missing or wrong"
        )


# ---------------------------------------------------------------------------
# Step 2: Direct unit tests
# ---------------------------------------------------------------------------

def test_normalize_date_compact():
    from backtester.engine_backtest.parity_summary import _normalize_date
    result = _normalize_date("20260403")
    assert result == ("20260403", "2026-04-03", date(2026, 4, 3))


def test_normalize_date_iso():
    from backtester.engine_backtest.parity_summary import _normalize_date
    result = _normalize_date("2026-04-03")
    assert result == ("20260403", "2026-04-03", date(2026, 4, 3))


def test_parse_ts_z_suffix():
    from backtester.engine_backtest.parity_summary import _parse_ts
    result = _parse_ts("2026-04-03T09:00:00Z")
    assert result == datetime.fromisoformat("2026-04-03T09:00:00+00:00")


def test_parse_ts_empty():
    from backtester.engine_backtest.parity_summary import _parse_ts
    assert _parse_ts("") is None
    assert _parse_ts(None) is None


def test_parse_ts_invalid():
    from backtester.engine_backtest.parity_summary import _parse_ts
    assert _parse_ts("not-a-date") is None


def test_bool_variants():
    from backtester.engine_backtest.parity_summary import _bool
    assert _bool(True) is True
    assert _bool("true") is True
    assert _bool("False") is False
    assert _bool(1) is True


def test_int_none_empty():
    from backtester.engine_backtest.parity_summary import _int
    assert _int(None) is None
    assert _int("") is None


def test_int_float_string():
    from backtester.engine_backtest.parity_summary import _int
    assert _int("3.7") == 3


def test_int_invalid():
    from backtester.engine_backtest.parity_summary import _int
    assert _int("abc") is None


def test_float_boundaries():
    from backtester.engine_backtest.parity_summary import _float
    assert _float(None) is None
    assert _float("") is None
    assert _float("1.5") == 1.5
    assert _float("0") == 0.0


def test_sum_count_dicts():
    from backtester.engine_backtest.parity_summary import _sum_count_dicts
    rows = [
        {"rules": {"A": 2, "B": 1}},
        {"rules": {"A": 3, "C": 1}},
        {"other": "ignored"},
    ]
    result = _sum_count_dicts(rows, "rules")
    assert result == {"A": 5, "B": 1, "C": 1}


def test_sum_count_dicts_empty():
    from backtester.engine_backtest.parity_summary import _sum_count_dicts
    assert _sum_count_dicts([], "k") == {}


def test_collect_unique_list_values():
    from backtester.engine_backtest.parity_summary import _collect_unique_list_values
    rows = [
        {"tags": ["X", "Y"]},
        {"tags": ["Y", "Z"]},
        {"tags": None},
    ]
    result = _collect_unique_list_values(rows, "tags")
    assert result == ["X", "Y", "Z"]


def test_last_dict_value():
    from backtester.engine_backtest.parity_summary import _last_dict_value
    rows = [
        {"meta": {"a": 1}},
        {"meta": {"b": 2}},
        {"meta": "not-dict"},
    ]
    # reversed scan: last row has string (skip), second row has {"b": 2}
    result = _last_dict_value(rows, "meta")
    assert result == {"b": 2}


def test_last_dict_value_empty():
    from backtester.engine_backtest.parity_summary import _last_dict_value
    assert _last_dict_value([], "k") == {}


def test_max_count_entry():
    from backtester.engine_backtest.parity_summary import _max_count_entry
    values = {"A": 3, "B": 5, "C": 1}
    name, count = _max_count_entry(values)
    assert name == "B"
    assert count == 5


def test_max_count_entry_empty():
    from backtester.engine_backtest.parity_summary import _max_count_entry
    name, count = _max_count_entry({})
    assert name is None
    assert count == 0


def test_overlap_overlap():
    from backtester.engine_backtest.parity_summary import _overlap
    result = _overlap(["A", "B", "C"], ["B", "C", "D"])
    # overlap = {B, C}, union = {A, B, C, D}
    # jaccard = 2/4 = 0.5
    assert result == {
        "left_count": 3,
        "right_count": 3,
        "overlap_count": 2,
        "overlap_symbols": ["B", "C"],
        "jaccard": 0.5,
    }


def test_overlap_empty_union():
    from backtester.engine_backtest.parity_summary import _overlap
    result = _overlap([], [])
    assert result["jaccard"] == 1.0


# ---------------------------------------------------------------------------
# Step 3: _build_rule_failure_summary full tuple test
# ---------------------------------------------------------------------------

def test_build_rule_failure_summary():
    from backtester.engine_backtest.parity_summary import _build_rule_failure_summary
    rule_names = ["R1", "R2", "R3"]
    enabled_counts = {"R1": 10, "R2": 8, "R3": 0}
    pass_counts = {"R1": 8, "R2": 3}
    fail_counts, pass_rates, dominant = _build_rule_failure_summary(
        rule_names=rule_names,
        enabled_counts=enabled_counts,
        pass_counts=pass_counts,
    )
    # R1: enabled=10, passed=8, failed=2, pass_rate=0.8
    # R2: enabled=8, passed=3, failed=5, pass_rate=0.375
    # R3: enabled=0, passed=0, failed=0, pass_rate=None
    assert fail_counts == {"R1": 2, "R2": 5, "R3": 0}
    assert pass_rates == {"R1": 0.8, "R2": 0.375, "R3": None}
    # dominant = R2 (fail_count=5)
    assert dominant == {
        "rule": "R2",
        "fail_count": 5,
        "enabled_count": 8,
        "pass_count": 3,
        "pass_rate": 0.375,
    }


# ---------------------------------------------------------------------------
# Step 4: _build_buy_diagnostics — 3 cases
# ---------------------------------------------------------------------------

def test_build_buy_diagnostics_capacity_blocked():
    """max_positions>0, available_slots<=0 → capacity_blocked"""
    from backtester.engine_backtest.parity_summary import _build_buy_diagnostics
    engine = {
        "executed_buy_count": 0,
        "buy_capacity": {
            "positions_before_buy_pass": 5,
            "max_positions": 5,
            "available_slots_before_buy_pass": 0,
        },
        "buy_funnel": {},
        "buy_rejection_reason_counts": {},
        "buy_score_stats": {},
        "buy_sizing": {},
        "dominant_rule_failure": None,
    }
    result = _build_buy_diagnostics(engine)
    assert result == {
        "zero_buy": True,
        "stage": "capacity_blocked",
        "entered_rule_stage": False,
        "entered_scoring_stage": False,
        "entered_sizing_stage": False,
        "capacity_blocked_before_rule_eval": False,
        "primary_rejection_reason": None,
        "primary_rejection_count": 0,
        "dominant_rule_failure": None,
        "summary": (
            "capacity block: buy rule 평가 전에 슬롯이 없어 중단되었습니다 "
            "(positions_before_buy=5, max_positions=5, available_slots=0)."
        ),
    }


def test_build_buy_diagnostics_rule_blocked_with_dominant():
    """entered_rule_stage=0 but entered_scoring_stage=0 → rule_blocked with dominant_rule_failure"""
    from backtester.engine_backtest.parity_summary import _build_buy_diagnostics
    dominant = {
        "rule": "momentum_gate",
        "fail_count": 7,
        "pass_rate": 0.1,
    }
    engine = {
        "executed_buy_count": 0,
        "buy_capacity": {
            "positions_before_buy_pass": 2,
            "max_positions": 10,
            "available_slots_before_buy_pass": 8,
        },
        "buy_funnel": {
            "decision_evaluated": 5,  # entered_rule_stage = True
            "buy_signal": 0,          # entered_scoring_stage = False
        },
        "buy_rejection_reason_counts": {},
        "buy_score_stats": {},
        "buy_sizing": {},
        "dominant_rule_failure": dominant,
    }
    result = _build_buy_diagnostics(engine)
    assert result == {
        "zero_buy": True,
        "stage": "rule_blocked",
        "entered_rule_stage": True,
        "entered_scoring_stage": False,
        "entered_sizing_stage": False,
        "capacity_blocked_before_rule_eval": False,
        "primary_rejection_reason": None,
        "primary_rejection_count": 0,
        "dominant_rule_failure": dominant,
        "summary": (
            "rule block: buy rule 단계에서 신호가 0건입니다 "
            "(dominant_rule=momentum_gate, fail_count=7, pass_rate=0.1)."
        ),
    }


def test_build_buy_diagnostics_executed_buy():
    """executed_buy_count > 0 → stage=executed_buy, zero_buy=False"""
    from backtester.engine_backtest.parity_summary import _build_buy_diagnostics
    engine = {
        "executed_buy_count": 2,
        "buy_capacity": {},
        "buy_funnel": {},
        "buy_rejection_reason_counts": {},
        "buy_score_stats": {},
        "buy_sizing": {},
        "dominant_rule_failure": None,
    }
    result = _build_buy_diagnostics(engine)
    assert result == {
        "zero_buy": False,
        "stage": "executed_buy",
        "entered_rule_stage": False,
        "entered_scoring_stage": False,
        "entered_sizing_stage": False,
        "capacity_blocked_before_rule_eval": False,
        "primary_rejection_reason": None,
        "primary_rejection_count": 0,
        "dominant_rule_failure": None,
        "summary": "BUY가 실행되었습니다.",
    }


# ---------------------------------------------------------------------------
# Step 5: _classify_parity — 2 cases
# ---------------------------------------------------------------------------

def test_classify_parity_high_engine_report():
    """engine_report mode, score/possible >= 0.75 → 'high'"""
    from backtester.engine_backtest.parity_summary import _classify_parity
    # buy_final_overlap: right_count=1, overlap_count=1 → +1/+1
    # buy_executed_overlap: right_count=1, overlap_count=1 → +1/+1
    # sell_overlap: right_count=0 → skip
    # live_sell_reasons: empty → skip
    # score=2, possible=2 → ratio=1.0 >= 0.75 → "high"
    level, matched, divergence = _classify_parity(
        source_mode="engine_report",
        buy_executed_overlap={"right_count": 1, "overlap_count": 1, "overlap_symbols": ["AAA"]},
        buy_final_overlap={"right_count": 1, "overlap_count": 1, "overlap_symbols": ["AAA"]},
        sell_overlap={"right_count": 0, "overlap_count": 0, "overlap_symbols": []},
        live_sell_reasons={},
        engine_sell_reasons={},
        warnings=[],
    )
    assert level == "high"
    assert len(matched) == 2


def test_classify_parity_medium_downgrade_proxy():
    """Same match as high but source_mode='historical_signal_proxy' → demoted to 'medium'"""
    from backtester.engine_backtest.parity_summary import _classify_parity
    level, matched, divergence = _classify_parity(
        source_mode="historical_signal_proxy",
        buy_executed_overlap={"right_count": 1, "overlap_count": 1, "overlap_symbols": ["BBB"]},
        buy_final_overlap={"right_count": 1, "overlap_count": 1, "overlap_symbols": ["BBB"]},
        sell_overlap={"right_count": 0, "overlap_count": 0, "overlap_symbols": []},
        live_sell_reasons={},
        engine_sell_reasons={},
        warnings=[],
    )
    assert level == "medium"
    # demotion note should be present
    assert any("historical_signal_proxy" in note for note in divergence)


# ---------------------------------------------------------------------------
# Step 6: _summarize_live_reference — minimal fixture
# ---------------------------------------------------------------------------

def test_summarize_live_reference_minimal():
    from backtester.engine_backtest.parity_summary import _summarize_live_reference
    candidate_rows = [
        {
            "symbol": "A001",
            "session": "REGULAR",
            "pre_gate_passed": "true",
            "shallow_selected": "false",
            "deep_evaluated": "false",
            "final_candidate": "false",
            "executed": "false",
        }
    ]
    stats_rows = [
        {
            "ts": "2026-04-03T09:00:00",
            "session": "REGULAR",
            "pre_gate_passed": 3,
            "shallow_ranked_count": 2,
            "shallow_shortlist_size": 1,
            "deep_eval_count": 1,
            "final_candidate_count": 1,
            "executed_order_count": 0,
            "sell_evaluated_count": 2,
            "sell_triggered_count": 0,
        }
    ]
    snapshot_rows = [
        {
            "timestamp": "2026-04-03T09:05:00",
            "market_session": {"session": "REGULAR"},
            "selected_sell_candidate": {"triggered_rule_name": "stop_loss"},
        }
    ]
    order_rows = [
        {
            "timestamp": "2026-04-03T09:10:00",
            "symbol": "A001",
            "order_type": "market_buy",
            "action": "buy_succeeded",
            "result": "success",
        }
    ]

    result = _summarize_live_reference(
        account="TEST",
        date_text="20260403",
        iso_date="2026-04-03",
        session="REGULAR",
        candidate_rows=candidate_rows,
        stats_rows=stats_rows,
        snapshot_rows=snapshot_rows,
        order_rows=order_rows,
    )

    assert result["candidate_rows"] == 1
    assert result["cycle_rows"] == 1
    assert result["snapshot_rows"] == 1
    assert result["buy_success_count"] == 1
    assert result["sell_success_count"] == 0
    assert result["sell_reason_distribution"] == {"stop_loss": 1}
    assert result["day_unique_symbols"] == {
        "observed": ["A001"],
        "pre_gate_passed": ["A001"],
        "shallow_selected": [],
        "deep_evaluated": [],
        "final_candidate": [],
        "executed_buy": [],
        "successful_buy_orders": ["A001"],
        "successful_sell_orders": [],
    }
    assert result["cycle_stats_max"] == {
        "pre_gate_passed": 3,
        "shallow_ranked_count": 2,
        "shallow_shortlist_size": 1,
        "deep_eval_count": 1,
        "final_candidate_count": 1,
        "executed_order_count": 0,
        "sell_evaluated_count": 2,
        "sell_triggered_count": 0,
    }


# ---------------------------------------------------------------------------
# Step 7: _summarize_engine_report — tmp_path fixture
# ---------------------------------------------------------------------------

def test_summarize_engine_report(tmp_path: Path):
    from backtester.engine_backtest.parity_summary import _summarize_engine_report

    report_data = {
        "metrics": {"initial_cash": 5000000},
        "daily_records": [
            {
                "date": "2026-04-03",
                "buy_symbol": "B001",
                "sell_symbol": None,
                "buy_selected_score": 0.85,
                "buy_signal_count": 3,
                "buy_scored_candidate_count": 2,
                "buy_candidate_symbols": ["B001", "B002"],
                "buy_rule_names": ["momentum", "volume"],
                "buy_rule_enabled_counts": {"momentum": 5, "volume": 4},
                "buy_rule_pass_counts": {"momentum": 4, "volume": 3},
                "buy_rejection_reason_counts": {"low_score": 1},
                "buy_funnel": {"decision_evaluated": 5},
                "buy_capacity": {"max_positions": 10},
                "buy_score_stats": {"min_score_threshold": 0.6},
                "buy_sizing": {},
                "sell_evaluated_count": 1,
                "sell_triggered_count": 0,
            },
            {
                "date": "2026-04-04",
                "buy_symbol": "C999",
                "sell_symbol": None,
                "buy_selected_score": None,
                "buy_signal_count": 1,
                "buy_scored_candidate_count": 0,
                "buy_candidate_symbols": [],
                "buy_rule_names": [],
                "buy_rule_enabled_counts": {},
                "buy_rule_pass_counts": {},
                "buy_rejection_reason_counts": {},
                "buy_funnel": {},
                "buy_capacity": {},
                "buy_score_stats": {},
                "buy_sizing": {},
                "sell_evaluated_count": 0,
                "sell_triggered_count": 0,
            },
        ],
        "trade_log": [
            {
                "buy_date": "2026-04-03",
                "sell_date": None,
                "sell_trigger": None,
            }
        ],
    }

    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report_data), encoding="utf-8")

    result = _summarize_engine_report(
        report_path=str(report_path),
        iso_date="2026-04-03",
    )

    # Only the 2026-04-03 row should be included
    assert result["mode"] == "engine_report"
    assert result["initial_cash"] == 5000000
    assert result["seeded_position_count"] is None
    assert result["buy_signal_count"] == 3
    assert result["buy_scored_candidate_count"] == 2
    assert result["buy_candidate_symbols"] == ["B001", "B002"]
    assert result["buy_rule_names"] == ["momentum", "volume"]
    assert result["buy_rule_enabled_counts"] == {"momentum": 5, "volume": 4}
    assert result["buy_rule_pass_counts"] == {"momentum": 4, "volume": 3}
    assert result["buy_rejection_reason_counts"] == {"low_score": 1}
    assert result["buy_funnel"] == {"decision_evaluated": 5}
    assert result["buy_capacity"] == {"max_positions": 10}
    assert result["buy_score_stats"] == {"min_score_threshold": 0.6}
    assert result["buy_sizing"] == {}
    assert result["selected_buy_symbols"] == ["B001"]
    assert result["selected_buy_score"] == 0.85
    assert result["final_candidate_count"] == 1
    assert result["executed_buy_count"] == 1
    assert result["sell_evaluated_count"] == 1
    assert result["sell_triggered_count"] == 0
    assert result["selected_sell_symbols"] == []
    assert result["executed_sell_count"] == 0
    assert result["sell_reason_distribution"] == {}
    assert result["input_symbol_count"] is None
