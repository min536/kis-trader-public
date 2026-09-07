"""R-d tests: preopen_operator_brief manual-trades section.

Design: docs/manual_trade_reconciliation_design_20260704.md §5.
- build_manual_trades_section: suspects with last_detected_at within 2 KR calendar
  days (resolved included but flagged) + last_intent_adjustments.
- print section: warn lines when suspects, dim "수동 매매 의심 없음" when none.
- C8: "state unavailable (정보 없음)" and "no suspects" MUST be different strings.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from unittest import mock

from app.core.time_utils import get_korean_now
from app.tools import preopen_operator_brief as brief_mod


def _capture_section(section) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        brief_mod.print_manual_trades_section(section)
    return buf.getvalue()


class BuildManualTradesSectionTests(unittest.TestCase):
    def test_recent_suspect_included(self) -> None:
        now = get_korean_now()
        state = {
            "manual_trade_suspects_by_symbol": {
                "005930": {
                    "first_detected_at": now.isoformat(),
                    "last_detected_at": now.isoformat(),
                    "last_event_type": "unexpected_missing_position",
                    "classification": "suspected_manual_sell",
                    "expected_qty": 10,
                    "actual_qty": 0,
                    "occurrences": 1,
                }
            },
            "last_intent_adjustments": [
                {"symbol": "005930", "before_qty": 3, "after_qty": 0,
                 "cause": "position_gone", "detected_at": now.isoformat()},
            ],
        }
        section = brief_mod.build_manual_trades_section(state)
        self.assertTrue(section["state_available"])
        symbols = [s["symbol"] for s in section["suspects"]]
        self.assertIn("005930", symbols)
        self.assertEqual(len(section["intent_adjustments"]), 1)

    def test_old_suspect_excluded_beyond_two_days(self) -> None:
        now = get_korean_now()
        old = now - timedelta(days=5)
        state = {
            "manual_trade_suspects_by_symbol": {
                "000660": {
                    "first_detected_at": old.isoformat(),
                    "last_detected_at": old.isoformat(),
                    "last_event_type": "unexpected_new_position",
                    "classification": "suspected_manual_buy",
                    "expected_qty": 0,
                    "actual_qty": 3,
                    "occurrences": 1,
                }
            },
        }
        section = brief_mod.build_manual_trades_section(state)
        self.assertEqual(section["suspects"], [])

    def test_resolved_suspect_flagged(self) -> None:
        now = get_korean_now()
        state = {
            "manual_trade_suspects_by_symbol": {
                "005930": {
                    "first_detected_at": now.isoformat(),
                    "last_detected_at": now.isoformat(),
                    "last_event_type": "unexpected_missing_position",
                    "classification": "suspected_manual_sell",
                    "expected_qty": 10,
                    "actual_qty": 0,
                    "occurrences": 1,
                    "resolved_at": now.isoformat(),
                }
            },
        }
        section = brief_mod.build_manual_trades_section(state)
        self.assertTrue(section["suspects"][0]["resolved"])

    def test_state_unavailable_marks_flag(self) -> None:
        section = brief_mod.build_manual_trades_section(None)
        self.assertFalse(section["state_available"])
        self.assertEqual(section["suspects"], [])


class PrintManualTradesSectionTests(unittest.TestCase):
    def test_prints_suspect_warn_line(self) -> None:
        section = {
            "state_available": True,
            "suspects": [
                {"symbol": "005930", "classification": "suspected_manual_sell",
                 "expected_qty": 10, "actual_qty": 0, "last_detected_at": "t",
                 "occurrences": 1, "resolved": False},
            ],
            "intent_adjustments": [],
        }
        out = _capture_section(section)
        self.assertIn("005930", out)
        self.assertIn("suspected_manual_sell", out)

    def test_no_suspects_prints_dim_none_line(self) -> None:
        section = {"state_available": True, "suspects": [], "intent_adjustments": []}
        out = _capture_section(section)
        self.assertIn("수동 매매 의심 없음", out)

    def test_state_unavailable_prints_info_missing(self) -> None:
        section = {"state_available": False, "suspects": [], "intent_adjustments": []}
        out = _capture_section(section)
        self.assertIn("정보 없음", out)

    def test_c8_unavailable_and_no_suspects_are_distinct_strings(self) -> None:
        no_suspects = _capture_section(
            {"state_available": True, "suspects": [], "intent_adjustments": []}
        )
        unavailable = _capture_section(
            {"state_available": False, "suspects": [], "intent_adjustments": []}
        )
        # C8: the two branches must not collapse to the same operator line.
        self.assertNotEqual(no_suspects.strip(), unavailable.strip())
        self.assertNotIn("정보 없음", no_suspects)
        self.assertNotIn("수동 매매 의심 없음", unavailable)


class BuildBriefIncludesManualTradesTests(unittest.TestCase):
    def test_build_brief_includes_manual_trades_key(self) -> None:
        with mock.patch.object(
            brief_mod, "build_readiness_report", return_value={"checks": []}
        ), mock.patch.object(
            brief_mod, "build_health_summary", return_value={}
        ), mock.patch.object(
            brief_mod, "run_budget_analysis", return_value={"meta": {}}
        ), mock.patch.object(
            brief_mod, "run_core_analysis", return_value={}
        ), mock.patch.object(
            brief_mod, "detect_latest_market_date", return_value="20260704"
        ), mock.patch.object(
            brief_mod, "_load_runtime_state_readonly", return_value=None
        ):
            brief = brief_mod.build_brief(account="acct", date="20260704", session="REGULAR")
        self.assertIn("manual_trades", brief)
        self.assertFalse(brief["manual_trades"]["state_available"])


if __name__ == "__main__":
    unittest.main()
