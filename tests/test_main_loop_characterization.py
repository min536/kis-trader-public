from __future__ import annotations

import weakref
import unittest
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from unittest import mock

from app import main as main_module
from app.core.session_lock import AppMainAlreadyRunningError
from app.core.time_utils import KOREA_TZ


class _StopLoop(RuntimeError):
    """Stops a patched infinite main loop after the intended scheduler ticks."""


class _LockHandle:
    pass


@dataclass(frozen=True)
class _Settings:
    run_mode: str = "mock"
    run_once: bool = False
    run_interval_seconds: int = 5
    sell_check_interval_seconds: int = 20
    buy_scan_interval_seconds: int = 180
    scan_symbols_max_per_cycle: int = 36
    buy_scan_shallow_top_k: int = 10
    buy_scan_deep_eval_limit: int = 6


def _runtime_rate_control() -> dict[str, object]:
    return {
        "mode": "normal",
        "reason": "base_defaults",
        "effective_sell_check_interval_seconds": 25,
        "effective_buy_scan_interval_seconds": 240,
        "effective_scan_symbols_max_per_cycle": 24,
        "effective_buy_scan_shallow_top_k": 8,
        "effective_buy_scan_deep_eval_limit": 4,
        "effective_sell_watch_max_holdings_per_tick": None,
    }


def _scheduler_tick(
    *,
    decision: str = "SELL_PRIORITY_WITH_BUY",
    sell_check_due: bool = True,
    buy_scan_due: bool = True,
    skip_cycle: bool = False,
) -> dict[str, object]:
    return {
        "decision": decision,
        "sell_check_due": sell_check_due,
        "buy_scan_due": buy_scan_due,
        "skip_cycle": skip_cycle,
    }


class MainLoopCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = _Settings()
        self.api_budget_state: dict[str, object] = {}
        self.now = datetime(2026, 5, 29, 10, 0, tzinfo=KOREA_TZ)
        self.mocks: dict[str, mock.Mock] = {}

        self._patch("get_settings", return_value=self.settings)
        self._patch("get_account_signature", return_value="mock_test_01")
        self._patch("acquire_app_main_lock", return_value=_LockHandle())
        self._patch("build_runtime_parameter_validation_report", return_value={})
        self._patch("build_startup_sanity_report", return_value={})
        self._patch("_print_runtime_parameter_validation")
        self._patch("_print_startup_sanity_report")
        # The startup order-log rotation resolves the account-scoped log path,
        # which needs a full Settings (base_url); this suite characterizes the
        # loop, not the rotation, so stub both seams like the other startup steps.
        self._patch("get_order_log_path", return_value="orders.jsonl")
        self._patch(
            "rotate_order_log_if_oversized",
            return_value=mock.Mock(rotated=False),
        )
        self._patch("_build_api_budget_state", return_value=self.api_budget_state)
        self._patch("_build_runtime_rate_control", return_value=_runtime_rate_control())
        self._patch(
            "compute_effective_sell_check_interval_seconds",
            return_value=25,
        )
        self._patch("_is_due", return_value=True)
        self._patch("_build_scheduler_tick_decision", return_value=_scheduler_tick())
        self._patch("_api_budget_transient_backoff_remaining_seconds", return_value=30.0)
        self._patch("_api_budget_backoff_remaining_seconds", return_value=30.0)
        self._patch("_emit_status")
        self._patch("_record_main_loop_exception_if_needed")
        self._patch("run_cycle")
        self._patch("get_korean_now", return_value=self.now)
        self.mocks["sleep"] = self.stack.enter_context(
            mock.patch.object(main_module.time, "sleep")
        )
        self.mocks["print"] = self.stack.enter_context(mock.patch("builtins.print"))

    def _patch(self, name: str, **kwargs: object) -> mock.Mock:
        patched = self.stack.enter_context(mock.patch.object(main_module, name, **kwargs))
        self.mocks[name] = patched
        return patched

    def _set_settings(self, **overrides: object) -> None:
        self.settings = replace(self.settings, **overrides)
        self.mocks["get_settings"].return_value = self.settings

    def _run_until_sleep(self) -> None:
        self.mocks["sleep"].side_effect = _StopLoop
        with self.assertRaises(_StopLoop):
            main_module.main()

    def test_run_once_acquires_and_retains_lock_before_single_full_cycle(self) -> None:
        self._set_settings(run_once=True)
        call_order: list[str] = []
        lock_ref: weakref.ReferenceType[_LockHandle] | None = None

        def acquire_lock(*args: object, **kwargs: object) -> _LockHandle:
            nonlocal lock_ref
            call_order.append("lock")
            handle = _LockHandle()
            lock_ref = weakref.ref(handle)
            return handle

        def validate(*args: object, **kwargs: object) -> dict[str, object]:
            call_order.append("validation")
            return {}

        def run_cycle(*args: object, **kwargs: object) -> None:
            call_order.append("run_cycle")
            self.assertIsNotNone(lock_ref)
            self.assertIsNotNone(lock_ref())

        self.mocks["acquire_app_main_lock"].side_effect = acquire_lock
        self.mocks["build_runtime_parameter_validation_report"].side_effect = validate
        self.mocks["run_cycle"].side_effect = run_cycle

        main_module.main()

        self.assertLess(call_order.index("lock"), call_order.index("validation"))
        self.assertLess(call_order.index("validation"), call_order.index("run_cycle"))
        self.mocks["run_cycle"].assert_called_once()
        cycle_settings = self.mocks["run_cycle"].call_args.args[0]
        cycle_kwargs = self.mocks["run_cycle"].call_args.kwargs
        self.assertEqual(cycle_settings.sell_check_interval_seconds, 25)
        self.assertEqual(cycle_settings.buy_scan_interval_seconds, 240)
        self.assertEqual(cycle_settings.scan_symbols_max_per_cycle, 24)
        self.assertEqual(cycle_settings.buy_scan_shallow_top_k, 8)
        self.assertEqual(cycle_settings.buy_scan_deep_eval_limit, 4)
        self.assertTrue(cycle_kwargs["sell_check_due"])
        self.assertTrue(cycle_kwargs["buy_scan_due"])
        self.assertEqual(
            cycle_kwargs["scheduler_state"]["decision"],
            "RUN_ONCE_FULL_CYCLE",
        )
        self.assertIsNone(cycle_kwargs["scheduler_state"]["last_sell_check_at"])
        self.assertIsNone(cycle_kwargs["scheduler_state"]["last_buy_scan_at"])
        self.mocks["sleep"].assert_not_called()

    def test_duplicate_app_main_lock_exits_before_startup_validation(self) -> None:
        self.mocks["acquire_app_main_lock"].side_effect = AppMainAlreadyRunningError(
            "duplicate"
        )

        with self.assertRaisesRegex(SystemExit, "1"):
            main_module.main()

        self.mocks["build_runtime_parameter_validation_report"].assert_not_called()
        self.mocks["build_startup_sanity_report"].assert_not_called()
        self.mocks["run_cycle"].assert_not_called()

    def test_base_tick_uses_minimum_configured_interval(self) -> None:
        self._set_settings(
            run_interval_seconds=7,
            sell_check_interval_seconds=20,
            buy_scan_interval_seconds=180,
        )
        self.mocks["_build_scheduler_tick_decision"].return_value = _scheduler_tick(
            decision="IDLE_WAIT",
            sell_check_due=False,
            buy_scan_due=False,
        )

        self._run_until_sleep()

        self.mocks["sleep"].assert_called_once_with(7)
        self.mocks["run_cycle"].assert_not_called()

    def test_base_tick_is_clamped_to_one_second(self) -> None:
        self._set_settings(
            run_interval_seconds=0,
            sell_check_interval_seconds=20,
            buy_scan_interval_seconds=180,
        )
        self.mocks["_build_scheduler_tick_decision"].return_value = _scheduler_tick(
            decision="IDLE_WAIT",
            sell_check_due=False,
            buy_scan_due=False,
        )

        self._run_until_sleep()

        self.mocks["sleep"].assert_called_once_with(1)
        self.mocks["run_cycle"].assert_not_called()

    def test_due_cycle_sleeps_only_remaining_tick_budget(self) -> None:
        self._set_settings(
            run_interval_seconds=30,
            sell_check_interval_seconds=30,
            buy_scan_interval_seconds=60,
        )
        self.mocks["get_korean_now"].side_effect = [
            self.now,
            self.now + timedelta(seconds=11),
        ]

        self._run_until_sleep()

        self.mocks["sleep"].assert_called_once_with(19.0)
        self.mocks["run_cycle"].assert_called_once()

    def test_transient_backoff_skips_entire_cycle(self) -> None:
        self.mocks["_build_scheduler_tick_decision"].return_value = _scheduler_tick(
            decision="API_TRANSIENT_BACKOFF_WAIT",
            sell_check_due=False,
            buy_scan_due=False,
            skip_cycle=True,
        )

        self._run_until_sleep()

        self.mocks["run_cycle"].assert_not_called()
        self.mocks["_api_budget_transient_backoff_remaining_seconds"].assert_called_once()
        self.mocks["_api_budget_backoff_remaining_seconds"].assert_not_called()
        self.assertEqual(self.api_budget_state["quotes_used_this_tick"], 0)

    def test_rate_limit_backoff_runs_due_sell_watch_and_defers_buy_scan(self) -> None:
        completed_at = self.now + timedelta(seconds=2)
        self.mocks["get_korean_now"].side_effect = [self.now, completed_at]
        self.mocks["_build_scheduler_tick_decision"].return_value = _scheduler_tick(
            decision="API_BACKOFF_WAIT",
            sell_check_due=True,
            buy_scan_due=False,
        )

        self._run_until_sleep()

        self.mocks["run_cycle"].assert_called_once()
        cycle_kwargs = self.mocks["run_cycle"].call_args.kwargs
        self.assertTrue(cycle_kwargs["sell_check_due"])
        self.assertFalse(cycle_kwargs["buy_scan_due"])
        self.assertEqual(cycle_kwargs["scheduler_state"]["decision"], "API_BACKOFF_WAIT")

    def test_skipped_tick_does_not_advance_due_timestamps(self) -> None:
        tick_two = self.now + timedelta(seconds=5)
        completed_at = tick_two + timedelta(seconds=2)
        self.mocks["get_korean_now"].side_effect = [self.now, tick_two, completed_at]
        self.mocks["_build_scheduler_tick_decision"].side_effect = [
            _scheduler_tick(
                decision="API_TRANSIENT_BACKOFF_WAIT",
                sell_check_due=False,
                buy_scan_due=False,
                skip_cycle=True,
            ),
            _scheduler_tick(),
        ]
        self.mocks["sleep"].side_effect = [None, _StopLoop]

        with self.assertRaises(_StopLoop):
            main_module.main()

        self.mocks["run_cycle"].assert_called_once()
        scheduler_state = self.mocks["run_cycle"].call_args.kwargs["scheduler_state"]
        self.assertIsNone(scheduler_state["last_sell_check_at"])
        self.assertIsNone(scheduler_state["last_buy_scan_at"])

    def test_attempted_cycle_advances_due_timestamps_for_the_next_tick(self) -> None:
        completed_one = self.now + timedelta(seconds=2)
        tick_two = self.now + timedelta(seconds=7)
        completed_two = tick_two + timedelta(seconds=2)
        self.mocks["get_korean_now"].side_effect = [
            self.now,
            completed_one,
            tick_two,
            completed_two,
        ]
        self.mocks["sleep"].side_effect = [None, _StopLoop]

        with self.assertRaises(_StopLoop):
            main_module.main()

        self.assertEqual(self.mocks["run_cycle"].call_count, 2)
        scheduler_state = self.mocks["run_cycle"].call_args_list[1].kwargs[
            "scheduler_state"
        ]
        self.assertEqual(scheduler_state["last_sell_check_at"], self.now)
        self.assertEqual(scheduler_state["last_buy_scan_at"], self.now)

    def test_cycle_exception_records_degraded_status_and_continues_next_tick(self) -> None:
        error = RuntimeError("cycle failed")
        completed_one = self.now + timedelta(seconds=2)
        tick_two = self.now + timedelta(seconds=7)
        completed_two = tick_two + timedelta(seconds=2)
        self.mocks["get_korean_now"].side_effect = [
            self.now,
            completed_one,
            tick_two,
            completed_two,
        ]
        self.mocks["run_cycle"].side_effect = [error, None]
        self.mocks["sleep"].side_effect = [None, _StopLoop]

        with self.assertRaises(_StopLoop):
            main_module.main()

        self.assertEqual(self.mocks["run_cycle"].call_count, 2)
        self.mocks["_record_main_loop_exception_if_needed"].assert_called_once_with(error)
        self.mocks["_emit_status"].assert_called_once()
        scheduler_state = self.mocks["run_cycle"].call_args_list[1].kwargs[
            "scheduler_state"
        ]
        self.assertEqual(scheduler_state["last_sell_check_at"], self.now)
        self.assertEqual(scheduler_state["last_buy_scan_at"], self.now)

    def test_run_once_cycle_exception_records_degraded_status_and_returns(self) -> None:
        self._set_settings(run_once=True)
        error = RuntimeError("run once failed")
        self.mocks["run_cycle"].side_effect = error

        main_module.main()

        self.mocks["_record_main_loop_exception_if_needed"].assert_called_once_with(error)
        self.mocks["_emit_status"].assert_called_once()
        self.mocks["sleep"].assert_not_called()


class MainHelperRebindingTests(unittest.TestCase):
    def test_clear_daily_pnl_pause_if_expired_is_risk_pnl_brake_original(self) -> None:
        from app.risk.pnl_brake import clear_daily_pnl_pause_if_expired

        self.assertIs(
            main_module._clear_daily_pnl_pause_if_expired,
            clear_daily_pnl_pause_if_expired,
        )

    def test_build_math_sizing_context_is_buy_flow_original(self) -> None:
        from app.execution.buy_flow import build_math_sizing_context

        self.assertIs(
            main_module._build_math_sizing_context,
            build_math_sizing_context,
        )


if __name__ == "__main__":
    unittest.main()
