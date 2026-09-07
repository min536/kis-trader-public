from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from app.risk import regime


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        same_symbol_max_buys_per_day=4,
        buy_daily_max_order_submissions=100,
        buy_max_budget_per_trade_krw=50_000_000,
        buy_max_account_exposure_pct=30.0,
        buy_max_qty_per_trade=30,
        rebuy_cooldown_minutes=20,
        regime_caution_drawdown_pct=-2.5,
        regime_risk_off_drawdown_pct=-5.0,
        regime_normal_multiplier=1.0,
        regime_caution_multiplier=0.8,
        regime_risk_off_multiplier=0.35,
        run_mode="trade",
        enable_premarket_wait=True,
    )


class RegimeStateTests(unittest.TestCase):
    def test_risk_off_keeps_limited_replenishment_capacity(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "OK"},
            drawdown_state={"current_drawdown_pct": -27.4},
        )

        self.assertEqual(state["current_regime"], "RISK_OFF")
        self.assertEqual(state["effective_buy_daily_max_order_submissions"], 10)
        self.assertEqual(state["effective_buy_max_qty_per_trade"], 15)
        self.assertEqual(state["effective_same_symbol_max_buys_per_day"], 1)
        self.assertEqual(state["effective_rebuy_cooldown_minutes"], 80)

    def test_risk_off_respects_lower_base_daily_buy_limit(self) -> None:
        settings = _settings()
        settings.buy_daily_max_order_submissions = 2

        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "OK"},
            drawdown_state={"current_drawdown_pct": -27.4},
        )

        self.assertEqual(state["current_regime"], "RISK_OFF")
        self.assertEqual(state["effective_buy_daily_max_order_submissions"], 2)

    def test_normal_when_no_brake_no_drawdown(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        self.assertEqual(state["current_regime"], "NORMAL")

    def test_normal_when_ok_brake_status(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "OK"},
            drawdown_state=None,
        )
        self.assertEqual(state["current_regime"], "NORMAL")

    def test_caution_from_warning_brake_status(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "WARNING"},
            drawdown_state=None,
        )
        self.assertEqual(state["current_regime"], "CAUTION")

    def test_caution_from_drawdown_at_threshold(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state=None,
            drawdown_state={"current_drawdown_pct": -2.5},
        )
        self.assertEqual(state["current_regime"], "CAUTION")

    def test_risk_off_from_buy_pause_brake(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "BUY_PAUSE"},
            drawdown_state=None,
        )
        self.assertEqual(state["current_regime"], "RISK_OFF")

    def test_risk_off_from_hard_stop_ready_brake(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "HARD_STOP_READY"},
            drawdown_state=None,
        )
        self.assertEqual(state["current_regime"], "RISK_OFF")

    def test_risk_off_from_drawdown_at_threshold(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state=None,
            drawdown_state={"current_drawdown_pct": -5.0},
        )
        self.assertEqual(state["current_regime"], "RISK_OFF")

    def test_brake_takes_priority_over_drawdown(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(),
            daily_pnl_brake_state={"display_status": "BUY_PAUSE"},
            drawdown_state={"current_drawdown_pct": -10.0},
        )
        self.assertEqual(state["current_regime"], "RISK_OFF")
        self.assertNotIn("drawdown", state["regime_reason"])

    def test_normal_multiplier_applied_to_budget(self) -> None:
        settings = _settings()
        settings.regime_normal_multiplier = 1.0
        settings.buy_max_budget_per_trade_krw = 100_000
        state = regime.build_regime_state(
            settings=settings, daily_pnl_brake_state=None, drawdown_state=None
        )
        self.assertEqual(state["effective_buy_max_budget_per_trade_krw"], 100_000)
        self.assertEqual(state["regime_multiplier"], 1.0)

    def test_caution_multiplier_applied(self) -> None:
        settings = _settings()
        settings.regime_caution_multiplier = 0.8
        settings.buy_max_budget_per_trade_krw = 100_000
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "WARNING"},
            drawdown_state=None,
        )
        self.assertEqual(state["effective_buy_max_budget_per_trade_krw"], 80_000)
        self.assertEqual(state["regime_multiplier"], 0.8)

    def test_caution_cooldown_doubled(self) -> None:
        settings = _settings()
        settings.rebuy_cooldown_minutes = 20
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "WARNING"},
            drawdown_state=None,
        )
        self.assertEqual(state["effective_rebuy_cooldown_minutes"], 40)

    def test_caution_same_symbol_limit_reduced_by_one(self) -> None:
        settings = _settings()
        settings.same_symbol_max_buys_per_day = 4
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "WARNING"},
            drawdown_state=None,
        )
        self.assertEqual(state["effective_same_symbol_max_buys_per_day"], 3)

    def test_caution_daily_buy_capped_at_15(self) -> None:
        settings = _settings()
        settings.buy_daily_max_order_submissions = 100
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "WARNING"},
            drawdown_state=None,
        )
        self.assertEqual(state["effective_buy_daily_max_order_submissions"], 15)

    def test_risk_off_same_symbol_limit_clamped_to_1(self) -> None:
        settings = _settings()
        settings.same_symbol_max_buys_per_day = 4
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "BUY_PAUSE"},
            drawdown_state=None,
        )
        self.assertEqual(state["effective_same_symbol_max_buys_per_day"], 1)

    def test_effective_budget_floor_is_1(self) -> None:
        settings = _settings()
        settings.buy_max_budget_per_trade_krw = 1
        settings.regime_risk_off_multiplier = 0.0
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "BUY_PAUSE"},
            drawdown_state=None,
        )
        self.assertGreaterEqual(state["effective_buy_max_budget_per_trade_krw"], 1)

    def test_effective_qty_floor_is_1(self) -> None:
        settings = _settings()
        settings.buy_max_qty_per_trade = 1
        settings.regime_risk_off_multiplier = 0.0
        state = regime.build_regime_state(
            settings=settings,
            daily_pnl_brake_state={"display_status": "BUY_PAUSE"},
            drawdown_state=None,
        )
        self.assertGreaterEqual(state["effective_buy_max_qty_per_trade"], 1)

    def test_drawdown_pct_none_when_no_drawdown_state(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        self.assertIsNone(state["current_drawdown_pct"])

    def test_return_keys_present(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        for key in (
            "current_regime", "regime_reason", "regime_multiplier", "current_drawdown_pct",
            "effective_buy_max_budget_per_trade_krw", "effective_buy_max_account_exposure_pct",
            "effective_buy_max_qty_per_trade", "effective_rebuy_cooldown_minutes",
            "effective_same_symbol_max_buys_per_day", "effective_buy_daily_max_order_submissions",
        ):
            self.assertIn(key, state)

    def test_print_regime_header_present(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            regime.print_regime_state(regime_state=state)
        self.assertIn("regime", buf.getvalue().lower())

    def test_print_regime_shows_normal(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            regime.print_regime_state(regime_state=state)
        self.assertIn("NORMAL", buf.getvalue())

    def test_print_regime_drawdown_fallback_when_none(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        self.assertIsNone(state["current_drawdown_pct"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            regime.print_regime_state(regime_state=state)
        self.assertIn("데이터 부족", buf.getvalue())

    def test_print_regime_ends_with_blank_line(self) -> None:
        state = regime.build_regime_state(
            settings=_settings(), daily_pnl_brake_state=None, drawdown_state=None
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            regime.print_regime_state(regime_state=state)
        self.assertTrue(buf.getvalue().endswith("\n\n"))

    def test_print_premarket_header_present(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            regime.print_premarket_wait_notice(SimpleNamespace(session="PREMARKET"))
        self.assertIn("장전", buf.getvalue())

    def test_print_premarket_ends_with_blank_line(self) -> None:
        for session in ("PREMARKET", "AFTER_MARKET", "CLOSED"):
            with self.subTest(session=session):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    regime.print_premarket_wait_notice(SimpleNamespace(session=session))
                self.assertTrue(buf.getvalue().endswith("\n\n"))

    def test_light_cycle_false_when_not_trade_mode(self) -> None:
        settings = _settings()
        settings.run_mode = "scan_only"
        self.assertFalse(regime.should_run_light_session_cycle(
            settings=settings, session_status=SimpleNamespace(session="PREMARKET")
        ))

    def test_light_cycle_premarket_follows_enable_flag_true(self) -> None:
        settings = _settings()
        settings.enable_premarket_wait = True
        self.assertTrue(regime.should_run_light_session_cycle(
            settings=settings, session_status=SimpleNamespace(session="PREMARKET")
        ))

    def test_light_cycle_premarket_follows_enable_flag_false(self) -> None:
        settings = _settings()
        settings.enable_premarket_wait = False
        self.assertFalse(regime.should_run_light_session_cycle(
            settings=settings, session_status=SimpleNamespace(session="PREMARKET")
        ))

    def test_light_cycle_after_market_returns_true(self) -> None:
        self.assertTrue(regime.should_run_light_session_cycle(
            settings=_settings(), session_status=SimpleNamespace(session="AFTER_MARKET")
        ))

    def test_light_cycle_closed_returns_true(self) -> None:
        self.assertTrue(regime.should_run_light_session_cycle(
            settings=_settings(), session_status=SimpleNamespace(session="CLOSED")
        ))

    def test_light_cycle_open_session_returns_false(self) -> None:
        self.assertFalse(regime.should_run_light_session_cycle(
            settings=_settings(), session_status=SimpleNamespace(session="OPEN")
        ))


if __name__ == "__main__":
    unittest.main()
