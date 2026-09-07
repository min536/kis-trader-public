from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.runtime import sell_selftest as selftest_module


def _make_settings(**overrides) -> SimpleNamespace:
    settings = SimpleNamespace(
        buy_rule_enable_intraday_pullback=True,
        buy_rule_enable_rebound_from_low=True,
        buy_rule_enable_controlled_down_day=True,
        buy_rule_enable_gap_down_open=False,
        buy_rule_enable_range_recovery=False,
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
        buy_rule_rebound_from_low_pct=1.0,
        buy_rule_controlled_down_day_min=-3.0,
        buy_rule_controlled_down_day_max=-0.3,
        buy_rule_gap_down_open_min_pct=-4.0,
        buy_rule_gap_down_open_max_pct=-0.5,
        buy_rule_range_recovery_min_ratio=0.5,
        buy_rule_required_pass_count=2,
        sell_enable=True,
        sell_stop_loss_pct=-2.0,
        sell_take_profit_pct=3.0,
        sell_trailing_stop_pct=1.5,
        sell_rule_enable_live_leadership_loss=False,
        sell_rule_enable_live_power_breakdown=False,
        use_cost_aware_pnl=False,
        buy_fee_bps=1.5,
        buy_slippage_bps=0.0,
        sell_fee_bps=1.5,
        sell_tax_bps=15.0,
        sell_slippage_bps=0.0,
        confirm_buy=False,
        block_resell_symbols_sold_today=True,
        sell_blocked_cooldown_minutes=30,
        order_cooldown_minutes=10,
        enable_sell_test_scenarios=True,
        sell_test_mode="stop_loss",
        enable_sell_guard_selftest=True,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class SellTestActiveTests(unittest.TestCase):
    def test_active_only_when_enabled_and_mode_set(self) -> None:
        self.assertTrue(
            selftest_module.sell_test_active(
                SimpleNamespace(enable_sell_test_scenarios=True, sell_test_mode="stop_loss")
            )
        )
        self.assertFalse(
            selftest_module.sell_test_active(
                SimpleNamespace(enable_sell_test_scenarios=True, sell_test_mode="off")
            )
        )
        self.assertFalse(
            selftest_module.sell_test_active(
                SimpleNamespace(enable_sell_test_scenarios=False, sell_test_mode="stop_loss")
            )
        )


class PrintTestModeTests(unittest.TestCase):
    def test_prints_banner_when_sell_test_mode_active(self) -> None:
        settings = SimpleNamespace(
            enable_sell_test_scenarios=True,
            sell_test_mode="stop_loss",
            enable_sell_guard_selftest=False,
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            selftest_module.print_test_mode(settings)

        output = buffer.getvalue()
        self.assertIn("=== 테스트 모드 ===", output)
        self.assertIn("sell_test_mode=stop_loss", output)
        self.assertIn("실제 주문은 전송하지 않습니다.", output)
        self.assertNotIn("sell_guard_selftest=on", output)


class RunSellGuardSelftestTests(unittest.TestCase):
    def test_stop_loss_selftest_forces_second_pass_through_cooldown_path(self) -> None:
        settings = _make_settings()

        buffer = io.StringIO()
        with (
            mock.patch.object(selftest_module, "log_order_event") as mocked_log,
            mock.patch.object(selftest_module, "print_last_action") as mocked_last,
            contextlib.redirect_stdout(buffer),
        ):
            selftest_module.run_sell_guard_selftest(settings)

        output = buffer.getvalue()
        self.assertIn("=== SELL 가드 자가검증 ===", output)
        self.assertIn(
            "1차 검증: PASS | symbol=005930 삼성전자 | trigger=stop_loss | sell_qty=10",
            output,
        )
        self.assertIn("2차 검증: COOLDOWN_SKIP", output)
        self.assertIn("주문 가드: SELL cooldown skip", output)
        mocked_last.assert_called_once_with("SELL_SKIPPED_COOLDOWN")
        mocked_log.assert_called_once()
        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["action"], "blocked_sell_cooldown")
        self.assertEqual(log_kwargs["result"], "skipped")
        self.assertEqual(log_kwargs["environment"], "mock_test")
        self.assertEqual(log_kwargs["raw_response"]["sell_guard_selftest"], True)


class RunSellTestCycleTests(unittest.TestCase):
    def test_scan_only_mode_records_action_without_order_log(self) -> None:
        settings = _make_settings(run_mode="scan_only")
        state: dict = {}

        buffer = io.StringIO()
        with (
            mock.patch.object(selftest_module, "log_order_event") as mocked_log,
            mock.patch.object(
                selftest_module, "print_sell_decision"
            ) as mocked_decision,
            mock.patch.object(
                selftest_module, "record_cycle_action"
            ) as mocked_record,
            contextlib.redirect_stdout(buffer),
        ):
            selftest_module.run_sell_test_cycle(settings, state)

        output = buffer.getvalue()
        self.assertIn("=== 매도 테스트 모드 ===", output)
        self.assertIn("scenario=stop_loss", output)
        self.assertIn("stop_loss 검증용 시나리오입니다.", output)
        self.assertIn("=== 보유 종목 ===", output)
        self.assertIn("005930 | ", output)
        self.assertIn("scan_only 모드에서는 매도 테스트 판단만 확인하고 종료합니다.", output)
        mocked_decision.assert_called_once()
        mocked_log.assert_not_called()
        mocked_record.assert_called_once()
        record_kwargs = mocked_record.call_args.kwargs
        self.assertEqual(record_kwargs["action"], "SELL_TEST_SCAN_ONLY")
        self.assertEqual(record_kwargs["symbol"], "005930")
        self.assertEqual(record_kwargs["qty"], 0)

    def test_market_open_sell_signal_logs_preview_only(self) -> None:
        settings = _make_settings(run_mode="trade")
        state: dict = {}
        fake_session = SimpleNamespace(
            order_allowed=True,
            session="REGULAR",
            reason="장중",
            sell_block_action=None,
        )

        buffer = io.StringIO()
        with (
            mock.patch.object(
                selftest_module, "get_korean_market_session", return_value=fake_session
            ),
            mock.patch.object(
                selftest_module,
                "build_market_session_console_lines",
                return_value=["세션: REGULAR"],
            ),
            mock.patch.object(selftest_module, "print_sell_decision"),
            mock.patch.object(selftest_module, "print_sell_preview") as mocked_preview,
            mock.patch.object(
                selftest_module, "print_cycle_conclusion"
            ) as mocked_conclusion,
            mock.patch.object(selftest_module, "log_order_event") as mocked_log,
            mock.patch.object(selftest_module, "print_last_action") as mocked_last,
            contextlib.redirect_stdout(buffer),
        ):
            selftest_module.run_sell_test_cycle(settings, state)

        output = buffer.getvalue()
        self.assertIn(
            "SELL_TEST_MODE 검증 경로이므로 실제 매도 주문은 보내지 않고 미리보기만 기록합니다.", output
        )
        mocked_preview.assert_called_once()
        mocked_conclusion.assert_called_once()
        conclusion_kwargs = mocked_conclusion.call_args.kwargs
        self.assertEqual(conclusion_kwargs["side"], "SELL")
        self.assertEqual(conclusion_kwargs["reason"], "stop_loss")
        self.assertEqual(conclusion_kwargs["planned_qty"], 10)
        mocked_last.assert_called_once_with("SELL_TEST_PREVIEW_ONLY")
        mocked_log.assert_called_once()
        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["action"], "sell_preview_only")
        self.assertEqual(log_kwargs["result"], "success")
        self.assertEqual(log_kwargs["environment"], "mock_test")
        self.assertEqual(log_kwargs["qty"], 10)
        self.assertEqual(
            log_kwargs["raw_response"]["sell_test_mode"], "stop_loss"
        )

    def test_market_closed_sell_signal_blocks_without_preview_log(self) -> None:
        settings = _make_settings(run_mode="trade")
        state: dict = {}
        fake_session = SimpleNamespace(
            order_allowed=False,
            session="CLOSED",
            reason="장 종료",
            sell_block_action="blocked_sell_market_closed",
        )

        buffer = io.StringIO()
        with (
            mock.patch.object(
                selftest_module, "get_korean_market_session", return_value=fake_session
            ),
            mock.patch.object(
                selftest_module,
                "build_market_session_console_lines",
                return_value=["세션: CLOSED"],
            ),
            mock.patch.object(selftest_module, "print_sell_decision"),
            mock.patch.object(selftest_module, "print_sell_preview"),
            mock.patch.object(selftest_module, "log_order_event") as mocked_log,
            mock.patch.object(selftest_module, "print_last_action") as mocked_last,
            contextlib.redirect_stdout(buffer),
        ):
            selftest_module.run_sell_test_cycle(settings, state)

        output = buffer.getvalue()
        self.assertIn("현재는 주문 가능 세션이 아니므로 매도 검토를 주문으로 보내지 않습니다. (CLOSED)", output)
        self.assertNotIn(
            "SELL_TEST_MODE 검증 경로이므로 실제 매도 주문은 보내지 않고 미리보기만 기록합니다.", output
        )
        mocked_last.assert_called_once_with("SELL_BLOCKED_CLOSED")
        mocked_log.assert_called_once()
        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["action"], "blocked_sell_market_closed")
        self.assertEqual(log_kwargs["result"], "skipped")
        self.assertEqual(log_kwargs["reason"], "장 종료")

    def test_hold_scenario_logs_strategy_rejected(self) -> None:
        settings = _make_settings(run_mode="trade", sell_test_mode="hold")
        state: dict = {}
        fake_session = SimpleNamespace(
            order_allowed=True,
            session="REGULAR",
            reason="장중",
            sell_block_action=None,
        )

        buffer = io.StringIO()
        with (
            mock.patch.object(
                selftest_module, "get_korean_market_session", return_value=fake_session
            ),
            mock.patch.object(
                selftest_module,
                "build_market_session_console_lines",
                return_value=["세션: REGULAR"],
            ),
            mock.patch.object(selftest_module, "print_sell_decision"),
            mock.patch.object(selftest_module, "print_sell_preview") as mocked_preview,
            mock.patch.object(selftest_module, "log_order_event") as mocked_log,
            mock.patch.object(selftest_module, "print_last_action") as mocked_last,
            contextlib.redirect_stdout(buffer),
        ):
            selftest_module.run_sell_test_cycle(settings, state)

        output = buffer.getvalue()
        self.assertIn("매도 테스트 결과: 현재 시나리오에서는 매도하지 않고 보유 유지입니다.", output)
        mocked_preview.assert_not_called()
        mocked_last.assert_called_once_with("SELL_BLOCKED_STRATEGY_REJECTED")
        mocked_log.assert_called_once()
        log_kwargs = mocked_log.call_args.kwargs
        self.assertEqual(log_kwargs["action"], "blocked_sell_strategy_rejected")
        self.assertEqual(log_kwargs["result"], "skipped")


class MainAliasSeamTests(unittest.TestCase):
    """app.main re-exports the selftest flows under their legacy underscore names."""

    def test_main_exposes_sell_selftest_functions(self) -> None:
        self.assertIs(main_module._sell_test_active, selftest_module.sell_test_active)
        self.assertIs(main_module._print_test_mode, selftest_module.print_test_mode)
        self.assertIs(
            main_module._run_sell_guard_selftest,
            selftest_module.run_sell_guard_selftest,
        )
        self.assertIs(
            main_module._run_sell_test_cycle,
            selftest_module.run_sell_test_cycle,
        )


if __name__ == "__main__":
    unittest.main()
