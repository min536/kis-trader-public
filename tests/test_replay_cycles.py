import unittest
from datetime import time

from app.tools.replay_cycles import (
    _focus_line,
    _symbol_match,
    _time_match,
)


class ReplayCyclesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {
            "timestamp": "2026-04-10T09:00:45+09:00",
            "final_action": "HOLD",
            "selected_primary_action_name": "000270 기아",
            "rate_limit_source": "sell_watch",
            "sell_watch_partial": True,
            "buy_scan_skipped_reason": "rate_limit_detected",
            "selected_buy_candidate": {
                "symbol": "000270",
                "display_name": "000270 기아",
                "passed_count": 2,
                "enabled_count": 5,
            },
            "sell_watch_evaluated_symbols": ["000270"],
            "selection_details": {
                "staged_scan": {
                    "core_rescue_applied": True,
                    "core_rescue_selected_symbol": "000270",
                }
            },
        }

    def test_time_match_accepts_range(self) -> None:
        self.assertTrue(
            _time_match(
                self.record,
                from_time=time(9, 0, 0),
                to_time=time(9, 1, 0),
            )
        )
        self.assertFalse(
            _time_match(
                self.record,
                from_time=time(9, 1, 0),
                to_time=None,
            )
        )

    def test_symbol_match_checks_selected_and_tracked_symbols(self) -> None:
        self.assertTrue(_symbol_match(self.record, symbol="000270"))
        self.assertFalse(_symbol_match(self.record, symbol="005930"))

    def test_focus_line_contains_core_fields(self) -> None:
        line = _focus_line(self.record)

        self.assertIn("sell_partial=YES", line)
        self.assertIn("buy_skip=rate_limit_detected", line)
        self.assertIn("buy_gap=2/3 (gap=1)", line)


if __name__ == "__main__":
    unittest.main()
