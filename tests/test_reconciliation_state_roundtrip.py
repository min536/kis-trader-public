"""Gate C2-extension: additive reconciliation fields survive state round-trip.

Design: docs/manual_trade_reconciliation_design_20260704.md §6.2 (C2 확장).
``save_runtime_state`` dumps the whole dict and ``load_runtime_state`` →
``_normalize_state`` must pass the additive event fields and new bounded state
keys through unchanged (``_normalize_state`` L329-330 passthrough guard for
``last_reconciliation_events``; ``.update()`` passthrough for new keys).
"""
from __future__ import annotations

import unittest
from unittest import mock

from app import runtime_state
from app.auth import account_scope


class ReconciliationRoundTripTests(unittest.TestCase):
    def test_additive_fields_and_new_keys_survive_save_load(self) -> None:
        with mock.patch.object(runtime_state, "get_runtime_state_path") as get_path, \
                mock.patch.object(
                    account_scope, "get_runtime_state_read_paths"
                ) as _read_paths, \
                mock.patch.object(
                    runtime_state, "get_runtime_state_read_paths"
                ) as get_read_paths:
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmp:
                state_file = Path(tmp) / "runtime_state.json"
                get_path.return_value = state_file
                get_read_paths.return_value = (state_file,)
                _read_paths.return_value = (state_file,)

                scope = account_scope.get_account_scope_context()
                state = runtime_state._default_state()
                state["account_signature"] = scope["account_signature"]
                state["last_reconciliation_events"] = [
                    {
                        "type": "unexpected_new_position",
                        "symbol": "000660",
                        "expected_qty": 0,
                        "actual_qty": 3,
                        "reason": "new",
                        "classification": "suspected_manual_buy",
                        "detected_at": "2026-05-20T10:05:00+09:00",
                    }
                ]
                state["manual_trade_suspects_by_symbol"] = {
                    "000660": {
                        "first_detected_at": "2026-05-20T10:05:00+09:00",
                        "last_detected_at": "2026-05-20T10:05:00+09:00",
                        "last_event_type": "unexpected_new_position",
                        "classification": "suspected_manual_buy",
                        "expected_qty": 0,
                        "actual_qty": 3,
                        "occurrences": 1,
                    }
                }
                state["last_intent_adjustments"] = [
                    {
                        "symbol": "005930",
                        "before_qty": 3,
                        "after_qty": 0,
                        "cause": "position_gone",
                        "detected_at": "2026-05-20T10:05:00+09:00",
                    }
                ]

                self.assertTrue(runtime_state.save_runtime_state(state))
                loaded = runtime_state.load_runtime_state()

        # Additive event fields preserved.
        event = loaded["last_reconciliation_events"][0]
        self.assertEqual(event["classification"], "suspected_manual_buy")
        self.assertEqual(event["detected_at"], "2026-05-20T10:05:00+09:00")
        # New bounded state keys preserved through _normalize_state passthrough.
        self.assertIn("000660", loaded["manual_trade_suspects_by_symbol"])
        self.assertEqual(
            loaded["manual_trade_suspects_by_symbol"]["000660"]["occurrences"], 1
        )
        self.assertEqual(loaded["last_intent_adjustments"][0]["cause"], "position_gone")


if __name__ == "__main__":
    unittest.main()
