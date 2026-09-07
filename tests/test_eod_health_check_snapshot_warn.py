from __future__ import annotations

import io
from contextlib import redirect_stdout

from app.tools.eod_health_check import print_eod_health_check


def _base_summary(**overrides):
    summary = {
        "account": "mock_acct",
        "date": "20260703",
        "session": "REGULAR",
        "candidate_rows": 0,
        "snapshot_rows": 0,
        "stats_rows": 0,
        "snapshot_rows_skipped_oversized": 0,
        "snapshot_tail_window_truncated": False,
    }
    summary.update(overrides)
    return summary


_EMPTY_REPORT = {"budget": {}, "conclusion": {}}
_EMPTY_JOIN = {
    "core_rescue_cycles": 0,
    "core_rescue_targets": 0,
    "core_rescue_deep_evaluated_rows": 0,
    "core_rescue_final_candidate_rows": 0,
    "core_rescue_executed_rows": 0,
}
_EMPTY_STALL = {
    "rescued_cycles": 0,
    "candidate_matches": 0,
    "selected_symbol_counts": {},
    "stage_counts": {},
    "outcome_counts": {},
    "buy_skip_counts": {},
    "stop_reason_counts": {},
    "rate_limit_triggered_count": 0,
    "sell_watch_partial_count": 0,
    "samples": [],
}


def _render(summary) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_eod_health_check(
            summary=summary,
            report=_EMPTY_REPORT,
            core_rescue_join=_EMPTY_JOIN,
            core_rescue_stall=_EMPTY_STALL,
            auto_detected_date=False,
        )
    return buf.getvalue()


def test_warn_when_snapshot_rows_skipped_oversized():
    out = _render(_base_summary(snapshot_rows_skipped_oversized=3))
    assert "WARN" in out
    assert "skipped" in out.lower()
    assert "3" in out


def test_warn_when_snapshot_tail_window_truncated():
    out = _render(_base_summary(snapshot_tail_window_truncated=True))
    assert "WARN" in out
    assert "truncat" in out.lower()


def test_no_snapshot_warn_when_clean():
    out = _render(_base_summary())
    # No skip/truncation WARN line when both are clean.
    assert "snapshot tail window" not in out.lower()
    assert "oversized" not in out.lower()
