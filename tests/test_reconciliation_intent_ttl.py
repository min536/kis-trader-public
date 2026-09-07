"""Reconciliation releases stale SELL intents and preserves their trigger (T2).

docs/todo_20260710.md §A-3 / §A-4.
"""
from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from app.core import reconciliation as reconciliation_module
from app.core.time_utils import get_korean_now


def _snapshot(positions: dict[str, int]) -> SimpleNamespace:
    return SimpleNamespace(
        held_positions=[
            SimpleNamespace(symbol=symbol, holding_qty=qty)
            for symbol, qty in positions.items()
        ]
    )


def _state(pending: dict[str, object]) -> dict:
    return {
        "pending_sell_intents_by_symbol": pending,
        "broker_last_synced_positions_by_symbol": {"023530": 18},
        "recent_orders": [],
    }


class ReconciliationIntentTtlTests(unittest.TestCase):
    def test_stale_intent_released_with_audit_adjustment(self) -> None:
        stale_at = (get_korean_now() - timedelta(minutes=45)).isoformat()
        state = _state(
            {"023530": {"qty": 18, "submitted_at": stale_at, "trigger": "stop_loss"}}
        )

        report = reconciliation_module.sync_reconciliation_state(
            state=state,
            portfolio_snapshot=_snapshot({"023530": 18}),
        )

        self.assertEqual(state["pending_sell_intents_by_symbol"], {})
        causes = [
            entry["cause"] for entry in report["intent_adjustments"]  # type: ignore[index]
        ]
        self.assertIn("intent_ttl_expired", causes)

    def test_fresh_intent_survives_and_keeps_trigger(self) -> None:
        fresh_at = get_korean_now().isoformat()
        state = _state(
            {"023530": {"qty": 18, "submitted_at": fresh_at, "trigger": "stop_loss"}}
        )

        reconciliation_module.sync_reconciliation_state(
            state=state,
            portfolio_snapshot=_snapshot({"023530": 18}),
        )

        entry = state["pending_sell_intents_by_symbol"]["023530"]
        self.assertEqual(entry["qty"], 18)
        # Without the trigger surviving a sync pass, the emergency (stop_loss)
        # TTL silently degrades to the slower default on the next cycle.
        self.assertEqual(entry["trigger"], "stop_loss")

    def test_position_gone_still_wins_over_ttl(self) -> None:
        fresh_at = get_korean_now().isoformat()
        state = _state({"023530": {"qty": 18, "submitted_at": fresh_at}})

        report = reconciliation_module.sync_reconciliation_state(
            state=state,
            portfolio_snapshot=_snapshot({}),
        )

        self.assertEqual(state["pending_sell_intents_by_symbol"], {})
        causes = [
            entry["cause"] for entry in report["intent_adjustments"]  # type: ignore[index]
        ]
        self.assertIn("position_gone", causes)
        self.assertNotIn("intent_ttl_expired", causes)


if __name__ == "__main__":
    unittest.main()
