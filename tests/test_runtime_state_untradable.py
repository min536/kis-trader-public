from __future__ import annotations

import unittest

from app.runtime_state import mark_buy_untradable_symbol, runtime_state_summary


class RuntimeStateUntradableTests(unittest.TestCase):
    def test_mark_buy_untradable_symbol_marks_buy_blocked_once(self) -> None:
        state = {
            "buy_untradable_symbols_today": [],
            "buy_blocked_symbols_today": [],
            "blocked_buy_symbols_today": [],
        }

        mark_buy_untradable_symbol(state, "252710")
        mark_buy_untradable_symbol(state, "252710")

        self.assertEqual(state["buy_untradable_symbols_today"], ["252710"])
        self.assertEqual(state["buy_blocked_symbols_today"], ["252710"])
        self.assertEqual(state["blocked_buy_symbols_today"], ["252710"])
        self.assertEqual(runtime_state_summary(state)["buy_untradable_count"], 1)


if __name__ == "__main__":
    unittest.main()
