"""SELL intent lifecycle: guard observability (T1), failure release (T3), TTL safety net (T2).

Backs docs/todo_20260710.md §A — the 2026-07-09 023530/082740 lockouts where a
pending SELL intent kept `blocked_sell_no_sellable_residual` firing for hours.
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app import main as main_module
from app.core.time_utils import get_korean_now
from app.execution.order_guard import evaluate_sell_order_guard
from app.runtime_state import (
    mark_sell_intent_submitted,
    prune_stale_sell_intents,
    prune_stale_sell_intents_with_audit,
)
from tests.test_sell_order_flow_characterization import (  # type: ignore[import-not-found]
    _flow_kwargs,
    _patched_sell_flow,
    _state,
)

_EGW00201 = {
    "rt_cd": "1",
    "msg_cd": "EGW00201",
    "msg1": "초당 거래건수를 초과하였습니다.",
}


class SellIntentReleasedOnFailureTests(unittest.TestCase):
    """T3 — every broker-failure path must leave the intent released."""

    def test_rt_cd_failure_releases_pending_intent(self) -> None:
        state = _state()
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=_EGW00201):
            with self.assertRaises(RuntimeError):
                main_module._run_sell_order_flow(**kwargs)

        self.assertEqual(state["pending_sell_intents_by_symbol"], {})

    def test_generic_exception_releases_pending_intent(self) -> None:
        state = _state()
        kwargs = _flow_kwargs(state=state)

        with _patched_sell_flow(sell_result=RuntimeError("broker down")):
            with self.assertRaises(RuntimeError):
                main_module._run_sell_order_flow(**kwargs)

        self.assertEqual(state["pending_sell_intents_by_symbol"], {})


class SellGuardBlockObservabilityTests(unittest.TestCase):
    """T1 — a blocked SELL must record *why* in the order log, not just a label."""

    def test_no_sellable_residual_block_logs_guard_details(self) -> None:
        state = _state()
        kwargs = _flow_kwargs(state=state)
        symbol = str(kwargs["sell_log_context"]["symbol"])  # type: ignore[index]
        holding_qty = int(kwargs["analysis"].holding_qty)  # type: ignore[union-attr]
        mark_sell_intent_submitted(state, symbol, holding_qty)

        with _patched_sell_flow() as records:
            result = main_module._run_sell_order_flow(**kwargs)

        self.assertTrue(result)
        blocked = [
            entry
            for entry in records.logs
            if entry["action"] == "blocked_sell_no_sellable_residual"
        ]
        self.assertEqual(len(blocked), 1)
        guard = blocked[0]["raw_response"]["sell_guard"]  # type: ignore[index]
        self.assertEqual(guard["holding_qty"], holding_qty)
        self.assertEqual(guard["pending_intent_qty"], holding_qty)
        self.assertEqual(guard["sellable_residual_qty"], 0)
        self.assertTrue(str(guard["pending_intent_submitted_at"]).strip())


class PruneStaleSellIntentsTests(unittest.TestCase):
    """T2 — a pending intent older than its TTL is released with an audit line."""

    def _state_with_intent(self, *, age_minutes: float, trigger: str | None = None) -> dict:
        state = _state()
        submitted_at = (
            get_korean_now() - timedelta(minutes=age_minutes)
        ).isoformat()
        mark_sell_intent_submitted(
            state, "023530", 18, submitted_at=submitted_at, trigger=trigger
        )
        return state

    def test_fresh_intent_is_kept(self) -> None:
        state = self._state_with_intent(age_minutes=1)
        released = prune_stale_sell_intents(state, ttl_minutes=10)
        self.assertEqual(released, [])
        self.assertIn("023530", state["pending_sell_intents_by_symbol"])

    def test_stale_intent_is_released(self) -> None:
        state = self._state_with_intent(age_minutes=11)
        released = prune_stale_sell_intents(state, ttl_minutes=10)
        self.assertEqual([entry["symbol"] for entry in released], ["023530"])
        self.assertEqual(released[0]["qty"], 18)
        self.assertEqual(released[0]["cause"], "intent_ttl_expired")
        self.assertEqual(state["pending_sell_intents_by_symbol"], {})

    def test_emergency_trigger_uses_shorter_ttl(self) -> None:
        state = self._state_with_intent(age_minutes=4, trigger="stop_loss")
        released = prune_stale_sell_intents(
            state, ttl_minutes=10, emergency_ttl_minutes=3
        )
        self.assertEqual([entry["symbol"] for entry in released], ["023530"])
        self.assertEqual(released[0]["ttl_minutes"], 3)

    def test_emergency_ttl_does_not_shorten_normal_trigger(self) -> None:
        state = self._state_with_intent(age_minutes=4, trigger="take_profit")
        released = prune_stale_sell_intents(
            state, ttl_minutes=10, emergency_ttl_minutes=3
        )
        self.assertEqual(released, [])

    def test_missing_submitted_at_is_backfilled_not_released(self) -> None:
        state = _state()
        state["pending_sell_intents_by_symbol"] = {"023530": {"qty": 18}}
        released = prune_stale_sell_intents(state, ttl_minutes=10)
        self.assertEqual(released, [])
        entry = state["pending_sell_intents_by_symbol"]["023530"]
        self.assertTrue(str(entry["submitted_at"]).strip())

    def _with_recent_order(self, state: dict, action: str) -> dict:
        state["recent_orders"] = [
            {
                "side": "SELL",
                "symbol": "023530",
                "qty": 18,
                "action": action,
                "timestamp": get_korean_now().isoformat(),
            }
        ]
        return state

    def test_accepted_order_still_in_flight_is_never_released(self) -> None:
        """A broker-accepted SELL is in flight — releasing it risks a double sell.

        stop_loss bypasses the order cooldown, so the intent reservation is the
        *only* thing standing between an unfilled accepted order and a resubmit.
        """
        state = self._state_with_intent(age_minutes=60, trigger="stop_loss")
        self._with_recent_order(state, "sell_order_succeeded")
        released = prune_stale_sell_intents(
            state, ttl_minutes=10, emergency_ttl_minutes=3
        )
        self.assertEqual(released, [])
        self.assertIn("023530", state["pending_sell_intents_by_symbol"])

    def test_submitted_but_unresolved_order_is_never_released(self) -> None:
        state = self._state_with_intent(age_minutes=60, trigger="stop_loss")
        self._with_recent_order(state, "sell_order_submitted")
        released = prune_stale_sell_intents(
            state, ttl_minutes=10, emergency_ttl_minutes=3
        )
        self.assertEqual(released, [])

    def test_broker_rejected_order_is_released_at_ttl(self) -> None:
        """EGW00201 = rejected, never accepted → nothing in flight → safe to release."""
        state = self._state_with_intent(age_minutes=4, trigger="stop_loss")
        self._with_recent_order(state, "sell_order_failed")
        released = prune_stale_sell_intents(
            state, ttl_minutes=10, emergency_ttl_minutes=3
        )
        self.assertEqual([entry["symbol"] for entry in released], ["023530"])

    def test_released_intent_unblocks_the_sell_guard(self) -> None:
        """The 023530 lockout, end to end: stale intent → guard allows the retry."""
        state = self._state_with_intent(age_minutes=11, trigger="stop_loss")
        blocked = evaluate_sell_order_guard(
            state=state,
            symbol="023530",
            holding_qty=18,
            qty=18,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            trigger="stop_loss",
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.action, "blocked_sell_no_sellable_residual")

        prune_stale_sell_intents(state, ttl_minutes=10, emergency_ttl_minutes=3)

        allowed = evaluate_sell_order_guard(
            state=state,
            symbol="023530",
            holding_qty=18,
            qty=18,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            trigger="stop_loss",
        )
        self.assertTrue(allowed.allowed)


class PruneWithAuditTests(unittest.TestCase):
    """T2 후속 — 기본 TTL prune + last_intent_adjustments 감사 기록 (finalize 착지용).

    reconciliation을 거치지 않는 lane-scheduler 경로에서도 안전망이 작동해야 한다
    (docs/todo_20260710.md §A-4b 적대검증 W6).
    """

    def _stale_state(self) -> dict:
        state = _state()
        submitted_at = (get_korean_now() - timedelta(minutes=61)).isoformat()
        mark_sell_intent_submitted(state, "023530", 18, submitted_at=submitted_at)
        return state

    def test_stale_release_appends_audit_to_state(self) -> None:
        state = self._stale_state()
        released = prune_stale_sell_intents_with_audit(state)
        self.assertEqual([entry["symbol"] for entry in released], ["023530"])
        self.assertEqual(state["pending_sell_intents_by_symbol"], {})
        audit = state["last_intent_adjustments"]
        self.assertEqual(audit[-1]["symbol"], "023530")
        self.assertEqual(audit[-1]["before_qty"], 18)
        self.assertEqual(audit[-1]["after_qty"], 0)
        self.assertEqual(audit[-1]["cause"], "intent_ttl_expired")

    def test_fresh_intent_appends_nothing(self) -> None:
        state = _state()
        mark_sell_intent_submitted(state, "023530", 18)
        released = prune_stale_sell_intents_with_audit(state)
        self.assertEqual(released, [])
        self.assertNotIn("last_intent_adjustments", state)

    def test_audit_list_is_capped(self) -> None:
        state = self._stale_state()
        state["last_intent_adjustments"] = [
            {"symbol": f"S{i}", "cause": "drained"} for i in range(60)
        ]
        prune_stale_sell_intents_with_audit(state)
        self.assertLessEqual(len(state["last_intent_adjustments"]), 50)
        self.assertEqual(state["last_intent_adjustments"][-1]["cause"], "intent_ttl_expired")


if __name__ == "__main__":
    unittest.main()
