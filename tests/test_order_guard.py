import unittest
from datetime import timedelta
from unittest.mock import patch

from app.core.time_utils import KOREA_TZ
from app.execution.order_guard import (
    build_sell_cooldown_key,
    evaluate_buy_order_guard,
    evaluate_rebalance_sell_guard,
    evaluate_sell_order_guard,
)
from app.strategy.reentry import ReEntryEligibilityResult


class OrderGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        from datetime import datetime

        self.fixed_now = datetime(2026, 4, 13, 9, 30, tzinfo=KOREA_TZ)

    def _recent_order(self, *, side: str, symbol: str, qty: int, minutes_ago: int, action: str) -> dict:
        return {
            "date": self.fixed_now.date().isoformat(),
            "side": side,
            "symbol": symbol,
            "qty": qty,
            "action": action,
            "timestamp": (self.fixed_now - timedelta(minutes=minutes_ago)).isoformat(),
        }

    def test_buy_guard_uses_injected_now_for_today_keying(self) -> None:
        # No get_korean_now patch: prove the injected `now` (ADR-4 clock
        # injection), not the real Korean clock, drives the "today" date used by
        # the per-symbol daily-buy-count guard. The recent order is dated to
        # self.fixed_now; with the real clock it would not be "today" and the
        # guard would allow, so a block proves the injected now is honored.
        state = {
            "recent_orders": [
                self._recent_order(
                    side="BUY",
                    symbol="000270",
                    qty=1,
                    minutes_ago=5,
                    action="order_submitted",
                )
            ]
        }

        result = evaluate_buy_order_guard(
            state=state,
            symbol="000270",
            qty=1,
            block_rebuy_symbols_bought_today=False,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=1,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            now=self.fixed_now,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_same_symbol_daily_limit")

    def test_sell_guard_uses_injected_now_for_cooldown(self) -> None:
        # No get_korean_now patch: prove the injected `now` drives the SELL
        # duplicate-order cooldown. The recent SELL is dated to self.fixed_now;
        # with the real clock it would not be "today" and the guard would allow.
        state = {
            "recent_orders": [
                self._recent_order(
                    side="SELL",
                    symbol="000270",
                    qty=1,
                    minutes_ago=5,
                    action="order_submitted",
                )
            ]
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="000270",
            holding_qty=10,
            qty=1,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=20,
            blocked_cooldown_minutes=0,
            trigger="take_profit",
            now=self.fixed_now,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_sell_duplicate_guard")
        self.assertTrue(result.is_cooldown)

    @patch("app.execution.order_guard.get_korean_now")
    def test_buy_guard_blocks_rebuy_cooldown(self, mock_now) -> None:
        mock_now.return_value = self.fixed_now
        state = {
            "recent_orders": [
                self._recent_order(
                    side="BUY",
                    symbol="000270",
                    qty=1,
                    minutes_ago=5,
                    action="order_succeeded",
                )
            ]
        }

        result = evaluate_buy_order_guard(
            state=state,
            symbol="000270",
            qty=1,
            block_rebuy_symbols_bought_today=True,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=20,
            same_symbol_max_buys_per_day=4,
            order_cooldown_minutes=1,
            blocked_cooldown_minutes=1,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_reentry_cooldown")
        self.assertTrue(result.is_cooldown)

    @patch("app.execution.order_guard.get_korean_now")
    def test_buy_guard_blocks_same_symbol_daily_limit(self, mock_now) -> None:
        mock_now.return_value = self.fixed_now
        state = {
            "recent_orders": [
                self._recent_order(
                    side="BUY",
                    symbol="005930",
                    qty=1,
                    minutes_ago=30,
                    action="order_submitted",
                ),
                self._recent_order(
                    side="BUY",
                    symbol="005930",
                    qty=1,
                    minutes_ago=60,
                    action="order_succeeded",
                ),
            ]
        }

        result = evaluate_buy_order_guard(
            state=state,
            symbol="005930",
            qty=1,
            block_rebuy_symbols_bought_today=True,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=2,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_same_symbol_daily_limit")

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_blocks_duplicate_pending_intent(self, mock_now) -> None:
        mock_now.return_value = self.fixed_now
        state = {
            "pending_sell_intents_by_symbol": {
                "247540": {"qty": 7, "submitted_at": self.fixed_now.isoformat()}
            }
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="247540",
            holding_qty=10,
            qty=5,
            block_resell_symbols_sold_today=True,
            allow_one_sell_trigger_per_symbol_per_day=True,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            trigger="stop_loss",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_sell_duplicate_pending_intent")
        self.assertEqual(result.details["sellable_residual_qty"], 3)

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_blocks_signature_cooldown(self, mock_now) -> None:
        # last_sell_attempt_signature now stores a qty-free cooldown key.
        mock_now.return_value = self.fixed_now
        state = {
            "last_sell_attempt_signature": "SELL:003670:take_profit",
            "last_sell_attempt_at": (self.fixed_now - timedelta(minutes=3)).isoformat(),
            "recent_orders": [],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="003670",
            holding_qty=10,
            qty=2,
            block_resell_symbols_sold_today=True,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=5,
            trigger="take_profit",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_sell_cooldown")
        self.assertTrue(result.is_cooldown)

    def test_sell_cooldown_key_excludes_qty(self) -> None:
        # Key must be identical regardless of qty — qty is not a component.
        key_qty6 = build_sell_cooldown_key(symbol="015760", trigger="live_power_breakdown")
        key_qty3 = build_sell_cooldown_key(symbol="015760", trigger="live_power_breakdown")
        self.assertEqual(key_qty6, "SELL:015760:live_power_breakdown")
        self.assertEqual(key_qty6, key_qty3)
        # Verify the key has exactly 2 colons (SELL:{symbol}:{trigger}), not 3.
        self.assertEqual(key_qty6.count(":"), 2)

    def test_sell_cooldown_key_handles_none_trigger(self) -> None:
        key = build_sell_cooldown_key(symbol="015760", trigger=None)
        self.assertEqual(key, "SELL:015760:-")

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_blocks_repeated_partial_sell_different_qty(self, mock_now) -> None:
        # 2026-05-11 재현: 015760 live_power_breakdown 6주 성공 후
        # 다음 사이클에서 qty=3으로 다시 trigger되어도 cooldown에 걸려야 한다.
        mock_now.return_value = self.fixed_now
        state = {
            # qty-free cooldown key가 저장된 상태 (실제 운영 시 build_sell_cooldown_key로 저장)
            "last_sell_attempt_signature": "SELL:015760:live_power_breakdown",
            "last_sell_attempt_at": (self.fixed_now - timedelta(minutes=5)).isoformat(),
            "recent_orders": [
                self._recent_order(
                    side="SELL", symbol="015760", qty=6, minutes_ago=5,
                    action="sell_order_submitted",
                )
            ],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="015760",
            holding_qty=6,
            qty=3,  # qty가 달라졌지만 cooldown에 걸려야 함
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=15,
            blocked_cooldown_minutes=30,
            trigger="live_power_breakdown",
        )

        self.assertFalse(result.allowed)
        self.assertTrue(result.is_cooldown)

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_blocks_repeated_partial_sell_same_qty(self, mock_now) -> None:
        # qty가 같아도 기존과 동일하게 차단되어야 한다.
        mock_now.return_value = self.fixed_now
        state = {
            "last_sell_attempt_signature": "SELL:015760:live_power_breakdown",
            "last_sell_attempt_at": (self.fixed_now - timedelta(minutes=5)).isoformat(),
            "recent_orders": [],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="015760",
            holding_qty=6,
            qty=3,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=30,
            trigger="live_power_breakdown",
        )

        self.assertFalse(result.allowed)
        self.assertTrue(result.is_cooldown)
        self.assertEqual(result.action, "blocked_sell_cooldown")

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_different_trigger_not_blocked_by_other_trigger_cooldown(self, mock_now) -> None:
        # live_power_breakdown의 blocked_cooldown이 다른 trigger(take_profit)를 막으면 안 된다.
        # _matches_last_attempt_cooldown 경로의 trigger 격리를 검증한다.
        # (order_cooldown_minutes=0으로 _within_cooldown 경로는 비활성화)
        mock_now.return_value = self.fixed_now
        state = {
            "last_sell_attempt_signature": "SELL:015760:live_power_breakdown",
            "last_sell_attempt_at": (self.fixed_now - timedelta(minutes=5)).isoformat(),
            "recent_orders": [
                self._recent_order(
                    side="SELL", symbol="015760", qty=3, minutes_ago=5,
                    action="sell_order_submitted",
                )
            ],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="015760",
            holding_qty=6,
            qty=3,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,   # exact-duplicate guard 비활성화
            blocked_cooldown_minutes=30,
            trigger="take_profit",
        )

        self.assertTrue(result.allowed)
        self.assertIsNone(result.action)

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_order_cooldown_blocks_exact_duplicate_regardless_of_trigger(self, mock_now) -> None:
        # order_cooldown_minutes 경로는 trigger와 무관하게 same symbol+qty 중복 주문을 차단한다.
        # recent_orders에는 trigger 정보가 없으므로 qty-match 기반 exact duplicate guard로만 작동한다.
        mock_now.return_value = self.fixed_now
        state = {
            "recent_orders": [
                self._recent_order(
                    side="SELL", symbol="015760", qty=3, minutes_ago=5,
                    action="sell_order_submitted",
                )
            ],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="015760",
            holding_qty=6,
            qty=3,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=15,
            blocked_cooldown_minutes=0,
            trigger="take_profit",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_sell_duplicate_guard")

    @patch("app.execution.order_guard.get_korean_now")
    def test_sell_guard_stop_loss_bypasses_cooldown(self, mock_now) -> None:
        # stop_loss(emergency)는 cooldown을 bypass해야 한다.
        mock_now.return_value = self.fixed_now
        state = {
            "last_sell_attempt_signature": "SELL:015760:stop_loss",
            "last_sell_attempt_at": (self.fixed_now - timedelta(minutes=2)).isoformat(),
            "recent_orders": [
                self._recent_order(
                    side="SELL", symbol="015760", qty=5, minutes_ago=2,
                    action="sell_order_submitted",
                )
            ],
        }

        result = evaluate_sell_order_guard(
            state=state,
            symbol="015760",
            holding_qty=5,
            qty=5,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=15,
            blocked_cooldown_minutes=30,
            trigger="stop_loss",
        )

        self.assertTrue(result.allowed)

    @patch("app.execution.order_guard.get_korean_now")
    def test_buy_cooldown_still_requires_qty_match(self, mock_now) -> None:
        # BUY cooldown은 기존과 동일하게 qty 일치를 요구해야 한다.
        mock_now.return_value = self.fixed_now
        state = {
            "recent_orders": [
                self._recent_order(
                    side="BUY", symbol="005930", qty=5, minutes_ago=3,
                    action="order_submitted",
                )
            ]
        }

        # qty=3으로 매수 시도 — recent order는 qty=5 → BUY cooldown은 작동 안 해야 함
        result = evaluate_buy_order_guard(
            state=state,
            symbol="005930",
            qty=3,
            block_rebuy_symbols_bought_today=False,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=10,
            order_cooldown_minutes=10,
            blocked_cooldown_minutes=0,
        )

        self.assertTrue(result.allowed)

    def test_rebalance_guard_blocks_when_limit_used(self) -> None:
        result = evaluate_rebalance_sell_guard(
            state={"rebalance_sell_submissions_today": 2},
            max_submissions_per_day=2,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_rebalance_sell_limit")

    def test_buy_guard_maps_same_day_stop_loss_reentry_block(self) -> None:
        reentry_decision = ReEntryEligibilityResult(
            allowed=False,
            state="blocked_same_day_stop_loss_reentry",
            reason="same-day stop-loss reentry blocked",
            cooldown_minutes_effective=120,
            requires_fresh_setup=True,
            exit_reason="stop_loss",
            residual_position_present=False,
            diagnostics=("same_day_stop_loss_floor=120",),
        )

        result = evaluate_buy_order_guard(
            state={"recent_orders": []},
            symbol="051910",
            qty=1,
            block_rebuy_symbols_bought_today=True,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=20,
            same_symbol_max_buys_per_day=4,
            order_cooldown_minutes=1,
            blocked_cooldown_minutes=1,
            reentry_decision=reentry_decision,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_same_day_stop_loss_reentry")
        self.assertTrue(result.is_cooldown)


if __name__ == "__main__":
    unittest.main()
